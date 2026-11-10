# -*- coding: utf-8 -*-
"""参考库的库(spec 1.2):单独一个文件 `refs.db`,结构版本从 v1 起,与剧库的版本号无关。

连接、写锁、`cached_statements=0` 沿用 `tools.drama.db.Store`(网页线程与抓取线程共用连接,见那里的说明);
只换结构与迁移。不可改的规矩由触发器强制:设置改动记录、热度、指纹只许追加;任务参数不可改;内容行不许删(清理只置 purged_at)。
v2(1b):录屏 / 转写任务、录屏 / 转写 / 关键帧内容;改 CHECK 只能重建表,见 REFS_MIGRATIONS[1]。
v3(1b):ref_source.retention_forever(原文永久保留、人手动删),见 REFS_MIGRATIONS[2]。
v4(1c):参考库自己的 llm_run(与剧库同一份 DDL,只换目标类型)、单元标注 ref_unit、拆解卡 ref_card、breakdown 任务,见 REFS_MIGRATIONS[3]。
"""
from __future__ import annotations

from tools.drama.db import APPLIED_DDL, DECISION_DDL, LLM_RUN_DDL, Store, _LLM_TARGETS_V3

REFS_VERSION = 4

_BASE_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE ref_source (
  key TEXT PRIMARY KEY,
  mode TEXT NOT NULL DEFAULT 'C' CHECK (mode IN ('A', 'B', 'C')),
  cap_fetch INTEGER CHECK (cap_fetch IS NULL OR cap_fetch >= 0),
  cap_breakdown INTEGER CHECK (cap_breakdown IS NULL OR cap_breakdown >= 0),
  retention_days INTEGER CHECK (retention_days IS NULL OR retention_days >= 1),
  retention_forever INTEGER NOT NULL DEFAULT 0,
  chapters_n INTEGER CHECK (chapters_n IS NULL OR chapters_n >= 1),
  top_k INTEGER CHECK (top_k IS NULL OR top_k >= 1),
  state TEXT NOT NULL DEFAULT 'ok' CHECK (state IN ('ok', 'tripped', 'paused')),
  state_reason TEXT NOT NULL DEFAULT '',
  state_until TEXT NOT NULL DEFAULT '',
  fail_streak INTEGER NOT NULL DEFAULT 0,
  last_ranks_day TEXT NOT NULL DEFAULT '',
  last_sitemap_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE ref_setting_log (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL REFERENCES ref_source(key),
  field TEXT NOT NULL,
  old TEXT,
  new TEXT,
  at TEXT NOT NULL
);
CREATE TRIGGER ref_setting_log_no_update BEFORE UPDATE ON ref_setting_log
BEGIN SELECT RAISE(ABORT, '设置改动记录写下不可改'); END;
CREATE TRIGGER ref_setting_log_no_delete BEFORE DELETE ON ref_setting_log
BEGIN SELECT RAISE(ABORT, '设置改动记录写下不可删'); END;

CREATE TABLE ref_work (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL REFERENCES ref_source(key),
  external_id TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  author TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  tags_json TEXT NOT NULL DEFAULT '[]',
  intro TEXT NOT NULL DEFAULT '',
  roles_json TEXT NOT NULL DEFAULT '[]',
  followed INTEGER NOT NULL DEFAULT 0,
  blocked INTEGER NOT NULL DEFAULT 0,
  candidate_reason TEXT NOT NULL DEFAULT '',
  candidate_day TEXT NOT NULL DEFAULT '',
  first_seen TEXT NOT NULL,
  UNIQUE (source, external_id)
);

CREATE TABLE ref_heat (
  id INTEGER PRIMARY KEY,
  work_id INTEGER NOT NULL REFERENCES ref_work(id),
  board TEXT NOT NULL,
  day TEXT NOT NULL,
  at TEXT NOT NULL,
  rank INTEGER,
  heat REAL,
  raw_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE (work_id, board, day)
);
CREATE TRIGGER ref_heat_no_update BEFORE UPDATE ON ref_heat
BEGIN SELECT RAISE(ABORT, '热度写入后不改'); END;
CREATE TRIGGER ref_heat_no_delete BEFORE DELETE ON ref_heat
BEGIN SELECT RAISE(ABORT, '热度写入后不删'); END;

"""

# 三张会被 v2 重建的表:表与触发器 / 索引分开放,fresh 库与迁移共用同一份定义(免得两份漂移)。
JOB_TABLE = """CREATE TABLE ref_job (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL REFERENCES ref_source(key),
  kind TEXT NOT NULL CHECK (kind IN ('ranks', 'meta', 'content', 'sitemap', 'record', 'transcribe', 'breakdown')),
  work_id INTEGER REFERENCES ref_work(id),
  params_json TEXT NOT NULL DEFAULT '{}',
  auto INTEGER NOT NULL DEFAULT 0,
  day TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'waiting' CHECK (state IN ('waiting', 'running', 'done', 'failed')),
  attempts INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  started_at TEXT NOT NULL DEFAULT '',
  finished_at TEXT NOT NULL DEFAULT '',
  progress TEXT NOT NULL DEFAULT ''
)"""
JOB_TRIGGERS = [
    """CREATE TRIGGER ref_job_params_immutable BEFORE UPDATE OF source, kind, work_id, params_json, auto, day ON ref_job
BEGIN SELECT RAISE(ABORT, '抓取任务的参数不可改 —— 要改就新排一条'); END""",
]

CONTENT_TABLE = """CREATE TABLE ref_content (
  id INTEGER PRIMARY KEY,
  work_id INTEGER NOT NULL REFERENCES ref_work(id),
  kind TEXT NOT NULL CHECK (kind IN ('chapter', 'review', 'manual', 'recording', 'transcript', 'keyframe')),
  unit INTEGER,
  title TEXT NOT NULL DEFAULT '',
  path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  origin TEXT NOT NULL,
  verdict TEXT NOT NULL CHECK (verdict IN ('ok', 'rejected')),
  reason TEXT NOT NULL DEFAULT '',
  fetched_at TEXT NOT NULL,
  due_at TEXT NOT NULL,
  hard_due_at TEXT NOT NULL,
  purged_at TEXT NOT NULL DEFAULT '',
  meta_json TEXT NOT NULL DEFAULT '{}'
)"""
CONTENT_TRIGGERS = [
    """CREATE TRIGGER ref_content_no_delete BEFORE DELETE ON ref_content
BEGIN SELECT RAISE(ABORT, '内容行不删 —— 到期清理只删文件、置 purged_at(指纹还引用它)'); END""",
]

FINGERPRINT_TABLE = """CREATE TABLE ref_fingerprint (
  content_id INTEGER NOT NULL REFERENCES ref_content(id),
  pos INTEGER NOT NULL,
  h INTEGER NOT NULL,
  PRIMARY KEY (content_id, pos)
)"""
FINGERPRINT_INDEX_AND_TRIGGERS = [
    "CREATE INDEX ref_fingerprint_h ON ref_fingerprint(h)",
    """CREATE TRIGGER ref_fingerprint_no_update BEFORE UPDATE ON ref_fingerprint
BEGIN SELECT RAISE(ABORT, '指纹永久保留、不可改'); END""",
    """CREATE TRIGGER ref_fingerprint_no_delete BEFORE DELETE ON ref_fingerprint
BEGIN SELECT RAISE(ABORT, '指纹永久保留、不可删'); END""",
]

# v4:LLM 运行记录放在参考库里(和剧库隔离)。照抄剧库**当前**的 llm_run —— 建表 + 3 个触发器(剧库 v3)、
# decision_json 与锁定触发器(剧库 v15)、applied_json 与锁定触发器(剧库 v18),只把目标类型换成参考库的内容 / 作品。
# 直接拿剧库的常量拼,两边不会漂;test_db 逐列、逐触发器与剧库新建库比对。
_REFS_LLM_TARGETS = "('ref_content', 'ref_work')"
REFS_LLM_RUN_DDL = [LLM_RUN_DDL[0].replace(_LLM_TARGETS_V3, _REFS_LLM_TARGETS), *LLM_RUN_DDL[1:], *DECISION_DDL, *APPLIED_DDL]
assert _REFS_LLM_TARGETS in REFS_LLM_RUN_DDL[0]

# 单元标注:批准拆解卡时连带写入。只许追加 —— 同一内容重拆 = 新行,读取取同一 content_id 的最新一行。
# run_id 是参考库 llm_run.id,不设外键(同剧库 llm_queue 的理由:llm_run 以后可能重建)。quote_at:视频是秒,小说为空。
UNIT_DDL = [
    """CREATE TABLE ref_unit (
  id INTEGER PRIMARY KEY,
  work_id INTEGER NOT NULL REFERENCES ref_work(id),
  content_id INTEGER NOT NULL REFERENCES ref_content(id),
  unit INTEGER,
  run_id INTEGER NOT NULL,
  fields_json TEXT NOT NULL,
  summary TEXT NOT NULL,
  quote TEXT NOT NULL,
  quote_at TEXT NOT NULL DEFAULT '',
  names_json TEXT NOT NULL DEFAULT '[]',
  visual_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
)""",
    """CREATE TRIGGER ref_unit_no_update BEFORE UPDATE ON ref_unit
BEGIN SELECT RAISE(ABORT, '单元标注写下不可改 —— 重拆就追加一行'); END""",
    """CREATE TRIGGER ref_unit_no_delete BEFORE DELETE ON ref_unit
BEGIN SELECT RAISE(ABORT, '单元标注写下不可删'); END""",
]

# 拆解卡:只在批准时写入(待审的就是 llm_run 的 proposed)。除了 approved → superseded(被新版取代)什么都不许改,不许删。
CARD_DDL = [
    """CREATE TABLE ref_card (
  id INTEGER PRIMARY KEY,
  work_id INTEGER NOT NULL REFERENCES ref_work(id),
  run_id INTEGER NOT NULL,
  version INTEGER NOT NULL CHECK (version >= 1),
  review_json TEXT NOT NULL,
  creation_json TEXT NOT NULL,
  creation_sha TEXT NOT NULL,
  coverage TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('approved', 'superseded')),
  decision_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (work_id, version)
)""",
    """CREATE TRIGGER ref_card_frozen
BEFORE UPDATE OF id, work_id, run_id, version, review_json, creation_json, creation_sha, coverage, decision_json, created_at ON ref_card
BEGIN SELECT RAISE(ABORT, '拆解卡批准即锁定 —— 要改就重拆、批准新版'); END""",
    """CREATE TRIGGER ref_card_state_only_superseded BEFORE UPDATE OF state ON ref_card
WHEN NOT (OLD.state = 'approved' AND NEW.state = 'superseded')
BEGIN SELECT RAISE(ABORT, '拆解卡的状态只许从「已批准」变成「已取代」'); END""",
    """CREATE TRIGGER ref_card_no_delete BEFORE DELETE ON ref_card
BEGIN SELECT RAISE(ABORT, '拆解卡写下不可删'); END""",
]

_REBUILT = [JOB_TABLE, *JOB_TRIGGERS, CONTENT_TABLE, *CONTENT_TRIGGERS, FINGERPRINT_TABLE, *FINGERPRINT_INDEX_AND_TRIGGERS]
REFS_SCHEMA = _BASE_SCHEMA + "".join(stmt + ";\n" for stmt in [*_REBUILT, *REFS_LLM_RUN_DDL, *UNIT_DDL, *CARD_DDL])

# v1 -> v2:SQLite 改不了 CHECK,只能重建。顺序是关键 ——
# 现代 SQLite(legacy_alter_table 关)改名时会把别的表里指向它的外键一起改到新名字上:
# 指纹表先改名,内容表再改名(旧指纹表的外键随之指向 ref_content_v1,新指纹表的外键指向新 ref_content);
# 触发器 / 索引名全库唯一、跟着旧表走,所以先删旧表再建它们。DROP TABLE 的隐式 DELETE 不触发触发器。
REFS_MIGRATIONS: dict[int, list[str]] = {1: [
    "ALTER TABLE ref_job RENAME TO ref_job_v1",
    "ALTER TABLE ref_fingerprint RENAME TO ref_fingerprint_v1",
    "ALTER TABLE ref_content RENAME TO ref_content_v1",
    JOB_TABLE, CONTENT_TABLE, FINGERPRINT_TABLE,
    "INSERT INTO ref_job (id, source, kind, work_id, params_json, auto, day, state, attempts, error, created_at, started_at, finished_at)"
    " SELECT id, source, kind, work_id, params_json, auto, day, state, attempts, error, created_at, started_at, finished_at FROM ref_job_v1",
    "INSERT INTO ref_content (id, work_id, kind, unit, title, path, sha256, origin, verdict, reason, fetched_at, due_at, hard_due_at, purged_at)"
    " SELECT id, work_id, kind, unit, title, path, sha256, origin, verdict, reason, fetched_at, due_at, hard_due_at, purged_at FROM ref_content_v1",
    "INSERT INTO ref_fingerprint (content_id, pos, h) SELECT content_id, pos, h FROM ref_fingerprint_v1",
    "DROP TABLE ref_job_v1", "DROP TABLE ref_fingerprint_v1", "DROP TABLE ref_content_v1",
    *JOB_TRIGGERS, *CONTENT_TRIGGERS, *FINGERPRINT_INDEX_AND_TRIGGERS,
],
    2: ["ALTER TABLE ref_source ADD COLUMN retention_forever INTEGER NOT NULL DEFAULT 0"],
    # v4:ref_job.kind 加 breakdown —— 同 v2 重建(ref_job 没有别的表引用,单独改名即可);再建 llm_run / ref_unit / ref_card。
    # (v1 -> v2 用的也是这份 JOB_TABLE,老库在 v2 那步就已放宽,这一步对它是原样搬一遍。)
    3: [
        "ALTER TABLE ref_job RENAME TO ref_job_v3",
        JOB_TABLE,
        "INSERT INTO ref_job (id, source, kind, work_id, params_json, auto, day, state, attempts, error, created_at, started_at,"
        " finished_at, progress) SELECT id, source, kind, work_id, params_json, auto, day, state, attempts, error, created_at,"
        " started_at, finished_at, progress FROM ref_job_v3",
        "DROP TABLE ref_job_v3",
        *JOB_TRIGGERS, *REFS_LLM_RUN_DDL, *UNIT_DDL, *CARD_DDL,
    ],
}


class RefStore(Store):
    def _migrate(self) -> None:
        has_meta = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'").fetchone()
        if not has_meta:
            self.conn.executescript(REFS_SCHEMA)
            self.conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (str(REFS_VERSION),))
            return
        row = self.conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        found = int(row[0]) if row else None
        if found is None or found > REFS_VERSION or any(v not in REFS_MIGRATIONS for v in range(found, REFS_VERSION)):
            raise RuntimeError(f"参考库结构版本 {row[0] if row else '缺失'} 与代码 {REFS_VERSION} 不符 —— 不许在不匹配的结构上继续写")
        for v in range(found, REFS_VERSION):
            with self.write() as c:
                for stmt in REFS_MIGRATIONS[v]:
                    c.execute(stmt)
                c.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(v + 1),))
