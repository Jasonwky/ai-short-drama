# -*- coding: utf-8 -*-
import sqlite3

import pytest

from tools.refs import clock
from tools.refs.db import REFS_VERSION, RefStore


def test_fresh_db_has_version_1(refs):
    assert refs.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == str(REFS_VERSION) == "1"


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
