# -*- coding: utf-8 -*-
from tools.drama import repo
from tools.drama.db import SCHEMA_VERSION, Store
from tools.drama.tasks import create_task, set_task_state


def _cols(store, table):
    return {r[1] for r in store.conn.execute(f"PRAGMA table_info({table})")}


def _make_v1(path):
    """造一个 v1 结构的库:建最新结构,再删掉 v2 新增的列、把版本号改回 1。"""
    s = Store(path)
    d = repo.create_drama(s, "玉佩", "9:16")
    s.conn.execute("ALTER TABLE task DROP COLUMN state_since")
    s.conn.execute("ALTER TABLE host DROP COLUMN models_dir")
    s.conn.execute("DROP TABLE llm_run")          # v3 新增(触发器随表一起删)
    s.conn.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
    s.close()
    return d


def test_v1_database_is_migrated_in_place(tmp_path):
    path = tmp_path / "drama.db"
    d = _make_v1(path)
    s = Store(path)
    assert {"state_since"} <= _cols(s, "task") and {"models_dir"} <= _cols(s, "host")
    assert s.conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == str(SCHEMA_VERSION)
    assert repo.get(s, "drama", d)["title"] == "玉佩"  # 数据还在
    s.close()


def test_state_since_moves_with_every_transition(store, sample):
    t = create_task(store, kind="image", target_type="segment", target_id=sample["seg"], model="m",
                    params={}, seed=1, prompt="p", refs=[])
    assert repo.get(store, "task", t)["state_since"]
    store.conn.execute("UPDATE task SET state_since = '2000-01-01T00:00:00+00:00' WHERE id = ?", (t,))
    set_task_state(store, t, "queued")
    assert repo.get(store, "task", t)["state_since"] > "2000-01-01T00:00:00+00:00"
