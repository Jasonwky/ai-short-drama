# -*- coding: utf-8 -*-
import sqlite3

import pytest

from tools.drama.db import SCHEMA_VERSION, Store, now


def _insert_drama(store, aspect="9:16"):
    with store.write() as c:
        return c.execute("INSERT INTO drama (title, aspect_ratio, created_at) VALUES (?, ?, ?)",
                         ("玉佩", aspect, now())).lastrowid


def test_schema_has_all_tables(store):
    names = {r[0] for r in store.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"meta", "drama", "episode", "character", "scene", "prop", "segment",
            "segment_character", "segment_prop", "host", "task"} <= names


def test_aspect_ratio_is_checked(store):
    with pytest.raises(sqlite3.IntegrityError):
        _insert_drama(store, aspect="4:3")


def test_aspect_ratio_cannot_change_after_creation(store):
    d = _insert_drama(store)
    with pytest.raises(sqlite3.IntegrityError, match="画幅建后不可改"):
        with store.write() as c:
            c.execute("UPDATE drama SET aspect_ratio = '16:9' WHERE id = ?", (d,))
    assert store.conn.execute("SELECT aspect_ratio FROM drama").fetchone()[0] == "9:16"


def test_foreign_keys_are_enforced(store):
    with pytest.raises(sqlite3.IntegrityError):
        with store.write() as c:
            c.execute("INSERT INTO episode (drama_id, number, source_type) VALUES (999, 1, 'script')")


def test_task_generation_params_are_immutable(store):
    with store.write() as c:
        t = c.execute(
            "INSERT INTO task (kind, target_type, target_id, model, params_json, seed, prompt, refs_json, created_at)"
            " VALUES ('image', 'segment', 1, 'm', '{}', 7, 'p', '[]', ?)", (now(),)).lastrowid
    with pytest.raises(sqlite3.IntegrityError, match="不可变"):
        with store.write() as c:
            c.execute("UPDATE task SET seed = 8 WHERE id = ?", (t,))


def test_write_rolls_back_on_error(store):
    with pytest.raises(RuntimeError):
        with store.write() as c:
            c.execute("INSERT INTO drama (title, aspect_ratio, created_at) VALUES ('x', '9:16', ?)", (now(),))
            raise RuntimeError("boom")
    assert store.conn.execute("SELECT COUNT(*) FROM drama").fetchone()[0] == 0


def test_reopen_keeps_data_and_rejects_version_mismatch(tmp_path):
    path = tmp_path / "drama.db"
    s = Store(path)
    _insert_drama(s)
    s.close()

    s = Store(path)
    assert s.conn.execute("SELECT COUNT(*) FROM drama").fetchone()[0] == 1
    s.conn.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    s.close()

    with pytest.raises(RuntimeError, match=f"99 与代码 {SCHEMA_VERSION} 不符"):
        Store(path)


def test_two_threads_reading_the_same_statement_never_read_empty(store):
    """控制台里网页请求和后台 LLM 线程共用一个连接,会同时跑同一条 SQL(P19-B 逐集队列压出来的:
    两边都在 prepare 里 repo.get 剧那一行)。按 id 读一条早已存在的记录,一次都不许读空。"""
    import threading
    from tools.drama import repo
    d = repo.create_drama(store, "玉佩", "9:16")
    misses = []

    def reader():
        for _ in range(3000):
            if repo.get(store, "drama", d) is None:
                misses.append(1)

    def safe_reader():
        try:
            reader()
        except KeyError:                       # repo.get 读空时抛 KeyError(「drama N 不存在」)
            misses.append(1)
    ts = [threading.Thread(target=safe_reader) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not misses, f"两个线程同时读,有 {len(misses)} 次读空"
