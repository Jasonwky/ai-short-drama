# -*- coding: utf-8 -*-
"""参考库结构 v1 的 REFS_SCHEMA 原文(`git show f888cdd:tools/refs/db.py`),只给迁移测试建 v1 库用,不 import 旧模块。"""

V1 = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE ref_source (
  key TEXT PRIMARY KEY,
  mode TEXT NOT NULL DEFAULT 'C' CHECK (mode IN ('A', 'B', 'C')),
  cap_fetch INTEGER CHECK (cap_fetch IS NULL OR cap_fetch >= 0),
  cap_breakdown INTEGER CHECK (cap_breakdown IS NULL OR cap_breakdown >= 0),
  retention_days INTEGER CHECK (retention_days IS NULL OR retention_days >= 1),
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

CREATE TABLE ref_job (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL REFERENCES ref_source(key),
  kind TEXT NOT NULL CHECK (kind IN ('ranks', 'meta', 'content', 'sitemap')),
  work_id INTEGER REFERENCES ref_work(id),
  params_json TEXT NOT NULL DEFAULT '{}',
  auto INTEGER NOT NULL DEFAULT 0,
  day TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'waiting' CHECK (state IN ('waiting', 'running', 'done', 'failed')),
  attempts INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  started_at TEXT NOT NULL DEFAULT '',
  finished_at TEXT NOT NULL DEFAULT ''
);
CREATE TRIGGER ref_job_params_immutable BEFORE UPDATE OF source, kind, work_id, params_json, auto, day ON ref_job
BEGIN SELECT RAISE(ABORT, '抓取任务的参数不可改 —— 要改就新排一条'); END;

CREATE TABLE ref_content (
  id INTEGER PRIMARY KEY,
  work_id INTEGER NOT NULL REFERENCES ref_work(id),
  kind TEXT NOT NULL CHECK (kind IN ('chapter', 'review', 'manual')),
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
  purged_at TEXT NOT NULL DEFAULT ''
);
CREATE TRIGGER ref_content_no_delete BEFORE DELETE ON ref_content
BEGIN SELECT RAISE(ABORT, '内容行不删 —— 到期清理只删文件、置 purged_at(指纹还引用它)'); END;

CREATE TABLE ref_fingerprint (
  content_id INTEGER NOT NULL REFERENCES ref_content(id),
  pos INTEGER NOT NULL,
  h INTEGER NOT NULL,
  PRIMARY KEY (content_id, pos)
);
CREATE INDEX ref_fingerprint_h ON ref_fingerprint(h);
CREATE TRIGGER ref_fingerprint_no_update BEFORE UPDATE ON ref_fingerprint
BEGIN SELECT RAISE(ABORT, '指纹永久保留、不可改'); END;
CREATE TRIGGER ref_fingerprint_no_delete BEFORE DELETE ON ref_fingerprint
BEGIN SELECT RAISE(ABORT, '指纹永久保留、不可删'); END;
"""
