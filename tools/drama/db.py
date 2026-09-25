# -*- coding: utf-8 -*-
"""SQLite 存储 —— 控制面的唯一真相源。只用标准库 sqlite3。

几条业务硬规则直接写成数据库约束和触发器,而不是靠调用方自觉:
- 画幅建后不可改(改画幅等于全部参考帧和视频作废)
- task 的生成参数不可变(溯源的前提;要重跑就新建一条)
- 外键开启(段落绑到不存在的场景要当场失败)

写操作一律走 Store.write() 的单锁 —— 设计 M-21「落盘走单线程」,从根上消灭并发覆盖。
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 5

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE drama (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  aspect_ratio TEXT NOT NULL CHECK (aspect_ratio IN ('9:16', '16:9')),
  style TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TRIGGER drama_aspect_immutable BEFORE UPDATE OF aspect_ratio ON drama
BEGIN SELECT RAISE(ABORT, '画幅建后不可改 —— 改画幅等于全部参考帧和视频作废,请新建剧'); END;

CREATE TABLE episode (
  id INTEGER PRIMARY KEY,
  drama_id INTEGER NOT NULL REFERENCES drama(id),
  number INTEGER NOT NULL,
  source_type TEXT NOT NULL CHECK (source_type IN ('novel', 'synopsis', 'script', 'footage')),
  source_text TEXT NOT NULL DEFAULT '',
  outline TEXT NOT NULL DEFAULT '',
  script TEXT NOT NULL DEFAULT '',
  UNIQUE (drama_id, number)
);

CREATE TABLE character (
  id INTEGER PRIMARY KEY,
  drama_id INTEGER NOT NULL REFERENCES drama(id),
  name TEXT NOT NULL,
  appearance TEXT NOT NULL DEFAULT '',
  styling TEXT NOT NULL DEFAULT '',
  voice_desc TEXT NOT NULL DEFAULT '',
  final_prompt TEXT NOT NULL DEFAULT '',
  seed INTEGER,
  turnaround_path TEXT,
  voice_ref_path TEXT,
  library_id INTEGER,
  UNIQUE (drama_id, name)
);

CREATE TABLE scene (
  id INTEGER PRIMARY KEY,
  drama_id INTEGER NOT NULL REFERENCES drama(id),
  episode_id INTEGER REFERENCES episode(id),
  name TEXT NOT NULL,
  location TEXT NOT NULL DEFAULT '',
  time TEXT NOT NULL DEFAULT '',
  lighting TEXT NOT NULL DEFAULT '',
  final_prompt TEXT NOT NULL DEFAULT '',
  image_path TEXT,
  library_id INTEGER,
  UNIQUE (drama_id, name)
);

CREATE TABLE prop (
  id INTEGER PRIMARY KEY,
  drama_id INTEGER NOT NULL REFERENCES drama(id),
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  final_prompt TEXT NOT NULL DEFAULT '',
  image_path TEXT,
  library_id INTEGER,
  UNIQUE (drama_id, name)
);

CREATE TABLE segment (
  id INTEGER PRIMARY KEY,
  episode_id INTEGER NOT NULL REFERENCES episode(id),
  number INTEGER NOT NULL,
  scene_id INTEGER REFERENCES scene(id),
  duration_sec REAL,
  shot_type TEXT NOT NULL DEFAULT '',
  angle TEXT NOT NULL DEFAULT '',
  movement TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  result TEXT NOT NULL DEFAULT '',
  atmosphere TEXT NOT NULL DEFAULT '',
  layout TEXT NOT NULL DEFAULT '',
  image_prompt TEXT NOT NULL DEFAULT '',
  video_prompt TEXT NOT NULL DEFAULT '',
  bgm_prompt TEXT NOT NULL DEFAULT '',
  sfx_prompt TEXT NOT NULL DEFAULT '',
  reference_mode TEXT NOT NULL DEFAULT 'first' CHECK (reference_mode IN
    ('first', 'last', 'key', 'first_last', 'first_last_key', 'text_only', 'anchors')),
  first_frame_path TEXT,
  last_frame_path TEXT,
  video_path TEXT,
  subtitle_path TEXT,
  UNIQUE (episode_id, number)
);

CREATE TABLE segment_character (
  segment_id INTEGER NOT NULL REFERENCES segment(id) ON DELETE CASCADE,
  character_id INTEGER NOT NULL REFERENCES character(id),
  position INTEGER NOT NULL,
  PRIMARY KEY (segment_id, character_id)
);

CREATE TABLE segment_prop (
  segment_id INTEGER NOT NULL REFERENCES segment(id) ON DELETE CASCADE,
  prop_id INTEGER NOT NULL REFERENCES prop(id),
  position INTEGER NOT NULL,
  PRIMARY KEY (segment_id, prop_id)
);

CREATE TABLE host (
  id INTEGER PRIMARY KEY,
  label TEXT NOT NULL,
  ssh_host TEXT NOT NULL,
  ssh_port INTEGER NOT NULL,
  ssh_user TEXT NOT NULL,
  key_ref TEXT NOT NULL,
  local_port INTEGER NOT NULL,
  remote_comfy_port INTEGER NOT NULL DEFAULT 8188,
  vram_gb REAL NOT NULL,
  models_dir TEXT NOT NULL DEFAULT ''
);

CREATE TABLE task (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('image', 'video', 'align')),
  target_type TEXT NOT NULL CHECK (target_type IN ('segment', 'character', 'scene', 'prop')),
  target_id INTEGER NOT NULL,
  model TEXT NOT NULL,
  params_json TEXT NOT NULL,
  seed INTEGER NOT NULL,
  prompt TEXT NOT NULL,
  refs_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  host_id INTEGER REFERENCES host(id),
  external_id TEXT,
  state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN
    ('pending', 'queued', 'running', 'waiting_host', 'done', 'failed')),
  result_path TEXT,
  result_sha256 TEXT,
  error TEXT,
  started_at TEXT,
  finished_at TEXT,
  state_since TEXT
);
CREATE TRIGGER task_params_immutable
BEFORE UPDATE OF kind, target_type, target_id, model, params_json, seed, prompt, refs_json, created_at ON task
BEGIN SELECT RAISE(ABORT, 'task 的生成参数不可变 —— 这是溯源的前提;要重跑请新建一条 task'); END;
"""

# v3:LLM 产物。落盘即不可改;人工批准即锁定;假 LLM 的产物永远过不了审批(CLAUDE.md LLM 纪律)。
LLM_RUN_DDL = [
    """CREATE TABLE llm_run (
  id INTEGER PRIMARY KEY,
  task TEXT NOT NULL,
  target_type TEXT NOT NULL CHECK (target_type IN ('drama', 'episode', 'character')),
  target_id INTEGER NOT NULL,
  model TEXT NOT NULL,
  input_sha TEXT NOT NULL,
  params_json TEXT NOT NULL,
  llm_mode TEXT NOT NULL CHECK (llm_mode IN ('real', 'fake')),
  state TEXT NOT NULL DEFAULT 'running' CHECK (state IN ('running', 'proposed', 'failed', 'approved', 'rejected')),
  output_json TEXT,
  journal_path TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  decided_at TEXT
)""",
    """CREATE TRIGGER llm_run_identity_immutable
BEFORE UPDATE OF task, target_type, target_id, model, input_sha, params_json, llm_mode, created_at ON llm_run
BEGIN SELECT RAISE(ABORT, 'llm_run 的身份字段不可变'); END""",
    """CREATE TRIGGER llm_run_output_locked BEFORE UPDATE OF output_json ON llm_run
WHEN OLD.output_json IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'LLM 产物一经落盘不可改 —— 要改就重跑一条'); END""",
    """CREATE TRIGGER llm_run_fake_never_approved BEFORE UPDATE OF state ON llm_run
WHEN NEW.state = 'approved' AND OLD.llm_mode = 'fake'
BEGIN SELECT RAISE(ABORT, '假 LLM 的产物不许通过审批闸门'); END""",
]
# v5:从图反推描述的目标可以是场景 / 道具。SQLite 改不了 CHECK,只能重建表(见 MIGRATIONS[4]);
# v3 的 LLM_RUN_DDL 已发布,原样保留给迁移用,新库直接建 v5 版。
_LLM_TARGETS_V3 = "('drama', 'episode', 'character')"
_LLM_TARGETS_V5 = "('drama', 'episode', 'character', 'scene', 'prop')"
LLM_RUN_DDL_V5 = [LLM_RUN_DDL[0].replace(_LLM_TARGETS_V3, _LLM_TARGETS_V5), *LLM_RUN_DDL[1:]]
SCHEMA += "".join(f"{stmt};\n" for stmt in LLM_RUN_DDL_V5)

# v4:成片导出。ProduceID 与溯源报告绑定,身份字段不可改;导出结束(done / failed)后整行不可改。
# setting 只收白名单里的键:服务提供者编码是★必答项,由运营方填,记住后下次预填。
EXPORT_DDL = [
    """CREATE TABLE setting (
  key TEXT PRIMARY KEY CHECK (key IN ('content_producer')),
  value TEXT NOT NULL
)""",
    """CREATE TABLE export (
  id INTEGER PRIMARY KEY,
  episode_id INTEGER NOT NULL REFERENCES episode(id),
  produce_id TEXT NOT NULL UNIQUE,
  content_producer TEXT NOT NULL,
  inputs_json TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'running' CHECK (state IN ('running', 'done', 'failed')),
  labeled_path TEXT,
  plain_path TEXT,
  report_path TEXT,
  checks_json TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  finished_at TEXT
)""",
    """CREATE TRIGGER export_identity_immutable
BEFORE UPDATE OF episode_id, produce_id, content_producer, inputs_json, created_at ON export
BEGIN SELECT RAISE(ABORT, '导出记录的身份字段不可变 —— ProduceID 与溯源报告绑定'); END""",
    """CREATE TRIGGER export_finished_locked BEFORE UPDATE ON export WHEN OLD.state != 'running'
BEGIN SELECT RAISE(ABORT, '导出已结束,记录不可再改 —— 要重导就新建一条'); END""",
]
SCHEMA += "".join(f"{stmt};\n" for stmt in EXPORT_DDL)

# 从版本 N 升到 N+1 要执行的语句。只追加,不改已发布的条目。
MIGRATIONS: dict[int, list[str]] = {
    # v2:task.state_since 记录进入当前状态的时刻(waiting_host 超 24 小时放弃要用);
    #     host.models_dir 给 LoRA 预检定位远端模型文件
    1: ["ALTER TABLE task ADD COLUMN state_since TEXT",
        "ALTER TABLE host ADD COLUMN models_dir TEXT NOT NULL DEFAULT ''"],
    2: LLM_RUN_DDL,
    3: EXPORT_DDL,
    # v5:重建 llm_run 放宽 target_type;旧表改名 → 建新表 → 原样拷数据 → 删旧表(旧触发器随之删除)→ 建新触发器
    4: ["ALTER TABLE llm_run RENAME TO llm_run_v3", LLM_RUN_DDL_V5[0],
        "INSERT INTO llm_run SELECT * FROM llm_run_v3", "DROP TABLE llm_run_v3", *LLM_RUN_DDL_V5[1:]],
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if str(path) != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.Lock()
        try:
            self._migrate()
        except BaseException:
            self.conn.close()  # 版本不符时别留着文件句柄(Windows 上会锁住数据库文件)
            raise

    def _migrate(self) -> None:
        has_meta = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'").fetchone()
        if not has_meta:
            self.conn.executescript(SCHEMA)
            self.conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                              (str(SCHEMA_VERSION),))
            return
        row = self.conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        found = int(row[0]) if row else None
        if found is None or found > SCHEMA_VERSION or any(v not in MIGRATIONS for v in range(found, SCHEMA_VERSION)):
            raise RuntimeError(
                f"数据库结构版本 {row[0] if row else '缺失'} 与代码 {SCHEMA_VERSION} 不符 —— 不许在不匹配的结构上继续写")
        for v in range(found, SCHEMA_VERSION):
            with self.write() as c:
                for stmt in MIGRATIONS[v]:
                    c.execute(stmt)
                c.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(v + 1),))

    @contextmanager
    def write(self):
        """单写锁 + 事务。块内抛异常整体回滚。"""
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    def close(self) -> None:
        self.conn.close()
