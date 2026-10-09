# -*- coding: utf-8 -*-
import sqlite3

import pytest

from tools.drama.db import Store
from tools.llm import runs

KEY = {"task": "storyboard", "target_type": "episode", "target_id": 1, "input_sha": "abc"}


def _start(store, mode="real"):
    return runs.start(store, model="gpt-5.6-sol", llm_mode=mode, **KEY)


def test_happy_path_and_lock(store):
    rid = _start(store)
    runs.propose(store, rid, {"segments": []}, "journal/llm/x.json")
    assert runs.locked(store, **KEY) is None                        # 还没批准:不算锁定
    runs.approve(store, rid)
    row = runs.locked(store, **KEY)
    assert row["id"] == rid and runs.output(row) == {"segments": []} and row["decided_at"]
    assert runs.locked(store, **{**KEY, "input_sha": "other"}) is None   # 输入变了就不是同一版


def test_output_is_immutable_once_written(store):
    rid = _start(store)
    runs.propose(store, rid, {"a": 1}, "j")
    with pytest.raises(sqlite3.IntegrityError, match="不可改"):
        store.conn.execute("UPDATE llm_run SET output_json = '{}' WHERE id = ?", (rid,))
    with pytest.raises(sqlite3.IntegrityError, match="身份字段不可变"):
        store.conn.execute("UPDATE llm_run SET model = 'other' WHERE id = ?", (rid,))


def test_fake_output_can_never_be_approved(store):
    rid = _start(store, mode="fake")
    runs.propose(store, rid, {"a": 1}, "j")
    with pytest.raises(sqlite3.IntegrityError, match="假 LLM"):
        runs.approve(store, rid)
    runs.reject(store, rid)                                         # 驳回可以


def test_illegal_transitions_and_silent_failure(store):
    rid = _start(store)
    with pytest.raises(ValueError, match="不能从 running 变成 approved"):
        runs.approve(store, rid)
    with pytest.raises(ValueError, match="写明原因"):
        runs.fail(store, rid, " ")
    runs.fail(store, rid, "GateFailed: 3 轮都没过判卷", "j")
    assert runs.get(store, rid)["state"] == "failed"


def test_v2_database_gets_the_llm_run_table(tmp_path):
    s = Store(tmp_path / "d.db")
    s.conn.execute("DROP TABLE llm_run")
    s.conn.execute("DROP TABLE export")            # v4 的表:造 v2 库时一并删掉
    s.conn.execute("DROP TABLE setting")
    s.conn.execute("ALTER TABLE host DROP COLUMN gpu")                         # v8 新增
    s.conn.execute("DROP TABLE workflow_preset_active")                       # v7 新增
    s.conn.execute("DROP TABLE workflow_preset")
    s.conn.execute("ALTER TABLE character DROP COLUMN fourview_path")        # v6 新增
    s.conn.execute("ALTER TABLE segment DROP COLUMN last_image_prompt")
    s.conn.execute("UPDATE meta SET value = '2' WHERE key = 'schema_version'")
    s.close()
    s = Store(tmp_path / "d.db")
    rid = runs.start(s, model="m", llm_mode="fake", **KEY)
    runs.propose(s, rid, {}, "j")
    with pytest.raises(sqlite3.IntegrityError):                     # 触发器也随迁移建好了
        runs.approve(s, rid)
    s.close()


def test_stale_running_run_is_failed_on_restart(store):
    """控制台被关掉 / 被系统回收时正在跑的 LLM 运行,重启后不会再有人接着跑:标失败,别让它永远「生成中」。"""
    stuck, done = _start(store), _start(store)
    runs.propose(store, done, {"a": 1}, "j")
    assert runs.fail_stale(store) == 1
    assert runs.get(store, stuck)["state"] == "failed" and "控制台重启" in runs.get(store, stuck)["error"]
    assert runs.get(store, done)["state"] == "proposed"                  # 已经有结果的不动
    assert runs.fail_stale(store) == 0
