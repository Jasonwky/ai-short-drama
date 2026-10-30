# -*- coding: utf-8 -*-
"""v24:task.kind 加 voice / tts、asset_approval.slot 加 voice(都要重建表)、两个新列。"""
import sqlite3

import pytest

from tools.drama import db
from tools.drama.db import Store

INSERT_TASK = ("INSERT INTO task (kind, target_type, target_id, model, params_json, seed, prompt, refs_json, created_at)"
               " VALUES (?, 'character', 1, 'm', '{}', 1, 'p', '[]', 't')")


def _v23(path):
    """造一个 v23 的库:把 v24 的 SCHEMA 换回旧 CHECK、去掉新列,再放一条任务、一条定稿。"""
    old = (db.SCHEMA.replace(db._TASK_KINDS_V24, db._TASK_KINDS_V1)
           .replace(db._SLOTS_V24, db._SLOTS_V23)
           .replace("  voice_ref_text TEXT,\n", "").replace("  line_audio_path TEXT,\n", "")
           .replace("ALTER TABLE task ADD COLUMN result_sec REAL;\n", ""))     # v25 的也退掉
    assert old.count(db._TASK_KINDS_V1) == 1 and "voice_ref_text" not in old
    c = sqlite3.connect(path)
    c.executescript(old)
    c.execute("INSERT INTO meta (key, value) VALUES ('schema_version', '23')")
    c.execute(INSERT_TASK, ("image",))
    c.execute("INSERT INTO asset_approval (table_name, row_id, slot, path, sha256, created_at)"
              " VALUES ('character', 1, 'image', 'a.png', 'x', 't')")
    c.commit()
    c.close()


def test_v23_db_migrates_and_keeps_rows_and_triggers(tmp_path):
    _v23(tmp_path / "d.db")
    s = Store(tmp_path / "d.db")
    assert s.conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == str(db.SCHEMA_VERSION)
    assert s.conn.execute("SELECT kind FROM task").fetchone()[0] == "image"
    assert s.conn.execute("SELECT path FROM asset_approval").fetchone()[0] == "a.png"
    with s.write() as c:
        c.execute(INSERT_TASK, ("voice",))
        c.execute("INSERT INTO asset_approval (table_name, row_id, slot, path, sha256, created_at)"
                  " VALUES ('character', 1, 'voice', 'a.wav', 'x', 't')")
    with pytest.raises(sqlite3.DatabaseError, match="不可变"), s.write() as c:
        c.execute("UPDATE task SET seed = 2 WHERE kind = 'voice'")
    with pytest.raises(sqlite3.DatabaseError, match="不可改"), s.write() as c:
        c.execute("UPDATE asset_approval SET reason = 'r'")
    assert "voice_ref_text" in [r[1] for r in s.conn.execute("PRAGMA table_info(character)")]
    assert "line_audio_path" in [r[1] for r in s.conn.execute("PRAGMA table_info(segment)")]
    assert s.conn.execute("SELECT name FROM sqlite_master WHERE name = 'asset_approval_target'").fetchone()
    s.conn.close()


def test_new_db_takes_the_new_kinds_and_slot_only(store):
    with store.write() as c:
        c.execute(INSERT_TASK, ("tts",))
        c.execute("INSERT INTO asset_approval (table_name, row_id, slot, path, sha256, created_at)"
                  " VALUES ('character', 1, 'voice', 'a.wav', 'x', 't')")
    with pytest.raises(sqlite3.IntegrityError), store.write() as c:
        c.execute(INSERT_TASK, ("music",))


def test_v24_db_gains_result_sec(tmp_path):
    s = Store(tmp_path / "d.db")
    s.conn.execute("ALTER TABLE task DROP COLUMN result_sec")
    s.conn.execute("UPDATE meta SET value = '24' WHERE key = 'schema_version'")
    s.close()
    s = Store(tmp_path / "d.db")
    assert "result_sec" in [r[1] for r in s.conn.execute("PRAGMA table_info(task)")]
    s.close()
