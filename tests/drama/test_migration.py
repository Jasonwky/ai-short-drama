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
    s.conn.execute("DROP TABLE export")           # v4 新增
    s.conn.execute("DROP TABLE setting")
    s.conn.execute("ALTER TABLE segment DROP COLUMN key_time_sec")                # v9 新增
    s.conn.execute("ALTER TABLE segment DROP COLUMN key_image_prompt")
    s.conn.execute("ALTER TABLE segment DROP COLUMN key_frame_path")
    s.conn.execute("ALTER TABLE host DROP COLUMN gpu")                         # v8 新增
    s.conn.execute("DROP TABLE workflow_preset_active")                       # v7 新增
    s.conn.execute("DROP TABLE workflow_preset")
    s.conn.execute("ALTER TABLE character DROP COLUMN fourview_path")        # v6 新增
    s.conn.execute("ALTER TABLE segment DROP COLUMN last_image_prompt")
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


def test_v4_llm_run_is_rebuilt_to_allow_scene_and_prop_targets(tmp_path):
    """v5 放宽 llm_run.target_type:旧数据原样保留,触发器照样在。"""
    import sqlite3

    import pytest

    from tools.drama.db import LLM_RUN_DDL
    from tools.llm import runs

    path = tmp_path / "drama.db"
    s = Store(path)
    s.conn.execute("DROP TABLE llm_run")
    for stmt in LLM_RUN_DDL:                        # v3 / v4 时代的表:target_type 只许 drama / episode / character
        s.conn.execute(stmt)
    rid = runs.start(s, task="voice", target_type="character", target_id=1, model="m", input_sha="x", llm_mode="real",
                     params={})
    runs.propose(s, rid, {"a": 1}, "j")
    with pytest.raises(sqlite3.IntegrityError):
        runs.start(s, task="describe", target_type="scene", target_id=1, model="m", input_sha="x", llm_mode="real",
                   params={})
    s.conn.execute("ALTER TABLE segment DROP COLUMN key_time_sec")                # v9 新增
    s.conn.execute("ALTER TABLE segment DROP COLUMN key_image_prompt")
    s.conn.execute("ALTER TABLE segment DROP COLUMN key_frame_path")
    s.conn.execute("ALTER TABLE host DROP COLUMN gpu")                         # v8 新增
    s.conn.execute("DROP TABLE workflow_preset_active")                       # v7 新增
    s.conn.execute("DROP TABLE workflow_preset")
    s.conn.execute("ALTER TABLE character DROP COLUMN fourview_path")        # v6 新增
    s.conn.execute("ALTER TABLE segment DROP COLUMN last_image_prompt")
    s.conn.execute("UPDATE meta SET value = '4' WHERE key = 'schema_version'")
    s.close()

    s = Store(path)
    assert runs.output(runs.get(s, rid)) == {"a": 1}                     # 旧数据还在
    runs.start(s, task="describe", target_type="prop", target_id=1, model="m", input_sha="x", llm_mode="real",
               params={})
    with pytest.raises(sqlite3.IntegrityError, match="不可改"):          # 触发器重建了
        s.conn.execute("UPDATE llm_run SET output_json = '{}' WHERE id = ?", (rid,))
    s.close()


def test_v5_database_gains_fourview_and_last_frame_prompt(tmp_path):
    """v6:角色四视图(H3 参考图)、段落尾帧提示词(首尾帧模式)。旧数据原样保留,新列有默认值。"""
    path = tmp_path / "drama.db"
    s = Store(path)
    d = repo.create_drama(s, "玉佩", "9:16")
    lin = repo.add_character(s, d, "林小雨", turnaround_path="assets/lin.png")
    s.conn.execute("ALTER TABLE segment DROP COLUMN key_time_sec")                # v9 新增
    s.conn.execute("ALTER TABLE segment DROP COLUMN key_image_prompt")
    s.conn.execute("ALTER TABLE segment DROP COLUMN key_frame_path")
    s.conn.execute("ALTER TABLE host DROP COLUMN gpu")                         # v8 新增
    s.conn.execute("DROP TABLE workflow_preset_active")                       # v7 新增
    s.conn.execute("DROP TABLE workflow_preset")
    s.conn.execute("ALTER TABLE character DROP COLUMN fourview_path")
    s.conn.execute("ALTER TABLE segment DROP COLUMN last_image_prompt")
    s.conn.execute("UPDATE meta SET value = '5' WHERE key = 'schema_version'")
    s.close()

    s = Store(path)
    row = repo.get(s, "character", lin)
    assert row["turnaround_path"] == "assets/lin.png" and row["fourview_path"] is None
    repo.update(s, "character", lin, fourview_path="outputs/tasks/3/fv.png")
    ep = repo.add_episode(s, d, 1, "script")
    seg = repo.add_segment(s, ep, 1, last_image_prompt="空无一人的庭院")
    assert repo.get(s, "segment", seg)["last_image_prompt"] == "空无一人的庭院"
    s.close()


def test_v6_database_gains_preset_tables_and_presets_are_immutable(tmp_path):
    """v7:工作流模板预设。一经保存不可改、不可删(旧任务引用着它)。"""
    import sqlite3

    import pytest

    path = tmp_path / "drama.db"
    s = Store(path)
    s.conn.execute("ALTER TABLE segment DROP COLUMN key_time_sec")                # v9 新增
    s.conn.execute("ALTER TABLE segment DROP COLUMN key_image_prompt")
    s.conn.execute("ALTER TABLE segment DROP COLUMN key_frame_path")
    s.conn.execute("ALTER TABLE host DROP COLUMN gpu")
    s.conn.execute("DROP TABLE workflow_preset_active")
    s.conn.execute("DROP TABLE workflow_preset")
    s.conn.execute("UPDATE meta SET value = '6' WHERE key = 'schema_version'")
    s.close()

    s = Store(path)
    s.conn.execute("INSERT INTO workflow_preset (category, version, diff_json, created_at) VALUES ('frame', 1, '{}', 'now')")
    with pytest.raises(sqlite3.IntegrityError, match="不可改"):
        s.conn.execute("UPDATE workflow_preset SET note = 'x'")
    with pytest.raises(sqlite3.IntegrityError, match="不可删"):
        s.conn.execute("DELETE FROM workflow_preset")
    with pytest.raises(sqlite3.IntegrityError):
        s.conn.execute("INSERT INTO workflow_preset_active (category) VALUES ('music')")
    s.close()


def test_v7_hosts_gain_an_unregistered_gpu(tmp_path):
    """v8:云机登记卡的型号(估时按卡算)。旧云机没有型号 = 无法估时,不猜。"""
    from tools.gen import estimate
    from tools.serve.hosts import add_host, get_host, set_gpu

    path = tmp_path / "drama.db"
    s = Store(path)
    hid = add_host(s, label="4090", ssh_host="h", ssh_port=1, ssh_user="root", key_ref="k", local_port=8188,
                   vram_gb=24.0, models_dir="/m")
    s.conn.execute("ALTER TABLE segment DROP COLUMN key_time_sec")                # v9 新增
    s.conn.execute("ALTER TABLE segment DROP COLUMN key_image_prompt")
    s.conn.execute("ALTER TABLE segment DROP COLUMN key_frame_path")
    s.conn.execute("ALTER TABLE host DROP COLUMN gpu")
    s.conn.execute("UPDATE meta SET value = '7' WHERE key = 'schema_version'")
    s.close()

    s = Store(path)
    assert get_host(s, hid).ssh_port == 1 and get_host(s, hid).gpu is None
    assert estimate.basis(s).timing is None
    set_gpu(s, hid, "RTX 4090")
    assert estimate.basis(s).note == "按 RTX 4090 估算"
    s.close()


def test_v8_segments_gain_an_optional_keyframe(tmp_path):
    """v9:段落可叠加一张中间关键帧(第几秒、提示词、当前图)。旧段落没有 = 不用关键帧。"""
    path = tmp_path / "drama.db"
    s = Store(path)
    d = repo.create_drama(s, "玉佩", "9:16")
    seg = repo.add_segment(s, repo.add_episode(s, d, 1, "script"), 1, duration_sec=8.0)
    for col in ("key_time_sec", "key_image_prompt", "key_frame_path"):
        s.conn.execute(f"ALTER TABLE segment DROP COLUMN {col}")
    s.conn.execute("UPDATE meta SET value = '8' WHERE key = 'schema_version'")
    s.close()

    s = Store(path)
    row = repo.get(s, "segment", seg)
    assert (row["key_time_sec"], row["key_image_prompt"], row["key_frame_path"]) == (None, "", None)
    repo.update(s, "segment", seg, key_time_sec=4.0, key_image_prompt="@林小雨 转身", key_frame_path="k.png")
    assert repo.get(s, "segment", seg)["key_time_sec"] == 4.0
    s.close()
