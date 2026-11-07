# -*- coding: utf-8 -*-
import sqlite3

import pytest

from tools.refs import clock
from tools.refs.db import REFS_VERSION, RefStore


def test_fresh_db_version(refs):
    assert refs.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == str(REFS_VERSION) == "3"


def test_wrong_version_is_refused(tmp_path):
    p = tmp_path / "refs.db"
    s = RefStore(p)
    with s.write() as c:
        c.execute("UPDATE meta SET value = '9' WHERE key = 'schema_version'")
    s.close()
    with pytest.raises(RuntimeError, match="参考库结构版本 9"):
        RefStore(p)


def test_setting_log_heat_and_fingerprint_are_append_only(refs):
    with refs.write() as c:
        c.execute("INSERT INTO ref_source (key) VALUES ('jjwxc')")
        c.execute("INSERT INTO ref_setting_log (source, field, old, new, at) VALUES ('jjwxc','mode','C','B','t')")
        c.execute("INSERT INTO ref_work (source, external_id, first_seen) VALUES ('jjwxc','1','t')")
        c.execute("INSERT INTO ref_heat (work_id, board, day, at, rank, heat, raw_json) VALUES (1,'b','2026-11-07','t',1,2,'{}')")
        c.execute("INSERT INTO ref_content (work_id, kind, unit, path, sha256, origin, verdict, fetched_at, due_at, hard_due_at)"
                  " VALUES (1,'chapter',1,'p','s','o','ok','t','t','t')")
        c.execute("INSERT INTO ref_fingerprint (content_id, pos, h) VALUES (1, 0, 42)")
    for sql in ("UPDATE ref_setting_log SET new='A'", "DELETE FROM ref_setting_log",
                "UPDATE ref_heat SET heat=3", "DELETE FROM ref_heat",
                "UPDATE ref_fingerprint SET h=1", "DELETE FROM ref_fingerprint", "DELETE FROM ref_content"):
        with pytest.raises(sqlite3.IntegrityError):
            with refs.write() as c:
                c.execute(sql)


def test_job_parameters_are_immutable_but_state_moves(refs):
    with refs.write() as c:
        c.execute("INSERT INTO ref_source (key) VALUES ('jjwxc')")
        c.execute("INSERT INTO ref_job (source, kind, params_json, auto, day, created_at) VALUES ('jjwxc','ranks','{}',0,'d','t')")
        c.execute("UPDATE ref_job SET state='running', attempts=1")
    with pytest.raises(sqlite3.IntegrityError):
        with refs.write() as c:
            c.execute("UPDATE ref_job SET params_json='{\"x\":1}'")


def test_beijing_day_rolls_at_16_utc():
    assert clock.today("2026-11-06T15:59:59+00:00") == "2026-11-06"
    assert clock.today("2026-11-06T16:00:00+00:00") == "2026-11-07"
    assert clock.days_between("2026-11-01", "2026-11-07") == 6


def _v1_db(path):
    """按 v1 的结构建一个带数据的库(v1 的 REFS_SCHEMA 原文在 tests/refs/v1_schema.py)。"""
    from tests.refs.v1_schema import V1
    c = sqlite3.connect(path)
    c.executescript(V1)
    c.executescript("""
      INSERT INTO meta VALUES ('schema_version', '1');
      INSERT INTO ref_source (key) VALUES ('jjwxc');
      INSERT INTO ref_work (id, source, external_id, first_seen) VALUES (1, 'jjwxc', '9', 't');
      INSERT INTO ref_job (source, kind, params_json, auto, day, created_at) VALUES ('jjwxc', 'ranks', '{}', 0, 'd', 't');
      INSERT INTO ref_content (id, work_id, kind, unit, path, sha256, origin, verdict, fetched_at, due_at, hard_due_at)
        VALUES (5, 1, 'chapter', 1, 'p', 's', 'o', 'ok', 't', 't', 't');
      INSERT INTO ref_fingerprint (content_id, pos, h) VALUES (5, 0, 42);
    """)
    c.commit(); c.close()


def test_v1_migrates_to_v3_keeping_rows_and_foreign_keys(tmp_path):
    p = tmp_path / "refs.db"
    _v1_db(p)
    s = RefStore(p)
    assert s.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "3"
    assert s.conn.execute("SELECT COUNT(*) FROM ref_fingerprint WHERE content_id = 5").fetchone()[0] == 1
    assert s.conn.execute("SELECT COUNT(*) FROM ref_job").fetchone()[0] == 1
    assert s.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert "ref_content(id)" in s.conn.execute("SELECT sql FROM sqlite_master WHERE name='ref_fingerprint'").fetchone()[0]
    assert not s.conn.execute("SELECT name FROM sqlite_master WHERE name LIKE '%_v1'").fetchall()
    fresh = RefStore(":memory:")
    objs = "SELECT type, name FROM sqlite_master WHERE type IN ('trigger', 'index') AND name NOT LIKE 'sqlite_%' ORDER BY name"
    assert s.conn.execute(objs).fetchall() == fresh.conn.execute(objs).fetchall()
    fresh.close()
    for sql in ("DELETE FROM ref_fingerprint", "UPDATE ref_fingerprint SET h = 1", "DELETE FROM ref_content",
                "UPDATE ref_job SET params_json = '{\"x\":1}'"):
        with pytest.raises(sqlite3.IntegrityError):
            with s.write() as c:
                c.execute(sql)
    with pytest.raises(sqlite3.IntegrityError):   # 外键照样拦:指纹不能指向不存在的内容
        with s.write() as c:
            c.execute("INSERT INTO ref_fingerprint (content_id, pos, h) VALUES (99, 0, 1)")
    with s.write() as c:
        c.execute("INSERT INTO ref_content (work_id, kind, path, sha256, origin, verdict, fetched_at, due_at, hard_due_at)"
                  " VALUES (1, 'recording', 'r.webm', 's', 'o', 'ok', 't', 't', 't')")
        c.execute("UPDATE ref_job SET progress = '第 1/5 集'")
    s.close()


def test_fresh_db_is_v3_with_new_kinds(refs):
    assert refs.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "3"
    with refs.write() as c:
        c.execute("INSERT INTO ref_source (key) VALUES ('hongguo')")
        c.execute("INSERT INTO ref_job (source, kind, params_json, auto, day, created_at) VALUES ('hongguo','record','{}',0,'d','t')")
        c.execute("INSERT INTO ref_job (source, kind, params_json, auto, day, created_at) VALUES ('hongguo','transcribe','{}',0,'d','t')")
        c.execute("INSERT INTO ref_work (source, external_id, first_seen) VALUES ('hongguo','1','t')")
        for k in ("recording", "transcript", "keyframe"):
            c.execute("INSERT INTO ref_content (work_id, kind, path, sha256, origin, verdict, fetched_at, due_at, hard_due_at, meta_json)"
                      " VALUES (1, ?, 'p', 's', 'o', 'ok', 't', 't', 't', '{\"sec\": 1}')", (k,))


def test_migration_failing_midway_rolls_back_and_leaves_v1(tmp_path, monkeypatch):
    from tools.refs import db
    p = tmp_path / "refs.db"
    _v1_db(p)
    steps = db.REFS_MIGRATIONS[1]
    bad = steps[:6] + ["INSERT INTO no_such_table VALUES (1)"] + steps[6:]     # 改名、建表之后、搬数据之前炸
    monkeypatch.setitem(db.REFS_MIGRATIONS, 1, bad)
    with pytest.raises(sqlite3.OperationalError, match="no_such_table"):
        RefStore(p)
    c = sqlite3.connect(p)
    assert c.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == "1"
    names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"ref_job", "ref_content", "ref_fingerprint"} <= names and not any(n.endswith("_v1") for n in names)
    assert c.execute("SELECT COUNT(*) FROM ref_fingerprint WHERE content_id = 5").fetchone()[0] == 1
    assert "'record'" not in c.execute("SELECT sql FROM sqlite_master WHERE name = 'ref_job'").fetchone()[0]   # 还是 v1 的表
    c.close()
    monkeypatch.setitem(db.REFS_MIGRATIONS, 1, steps)
    s = RefStore(p)                                       # 修好后照常迁到最新
    assert s.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == str(REFS_VERSION)
    s.close()


def test_v2_migrates_to_v3_keeping_rows_and_adding_retention_forever(tmp_path):
    p = tmp_path / "refs.db"
    _v1_db(p)
    s = RefStore(p)                  # v1 -> 最新
    s.close()
    c = sqlite3.connect(p)           # 退回 v2 的样子:去掉新列、版本号改 2
    c.execute("ALTER TABLE ref_source DROP COLUMN retention_forever")
    c.execute("UPDATE ref_source SET retention_days = 7")
    c.execute("UPDATE meta SET value = '2' WHERE key = 'schema_version'")
    c.commit(); c.close()
    s = RefStore(p)
    assert s.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "3"
    row = s.conn.execute("SELECT retention_days, retention_forever FROM ref_source WHERE key = 'jjwxc'").fetchone()
    assert tuple(row) == (7, 0)
    assert s.conn.execute("SELECT COUNT(*) FROM ref_fingerprint WHERE content_id = 5").fetchone()[0] == 1
    s.close()
