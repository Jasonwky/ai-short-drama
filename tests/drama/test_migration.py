# -*- coding: utf-8 -*-
from tools.drama import repo
from tools.drama.db import SCHEMA_VERSION, Store
from tools.drama.tasks import create_task, set_task_state


def _cols(store, table):
    return {r[1] for r in store.conn.execute(f"PRAGMA table_info({table})")}


def _drop_v16(s):
    """退回 v15:删掉 v16 新增的 drama.premise_json。"""
    if "premise_json" in _cols(s, "drama"):         # 更老的造库流程里列可能已经不在
        s.conn.execute("ALTER TABLE drama DROP COLUMN premise_json")


def _drop_v15(s):
    """退回 v14:删掉 v15 新增的 llm_run.decision_json 与它的触发器;v16 的也一并退掉。"""
    _drop_v16(s)
    s.conn.execute("DROP TRIGGER IF EXISTS llm_run_decision_locked")
    if "decision_json" in _cols(s, "llm_run"):      # 更老的造库流程会先把 llm_run 整张删掉 / 换成旧表
        s.conn.execute("ALTER TABLE llm_run DROP COLUMN decision_json")


def _drop_v14(s):
    """退回 v13:删掉 v14 新增的看片确认勾选项列;v15 的也一并退掉。"""
    _drop_v15(s)
    s.conn.execute("ALTER TABLE segment_review DROP COLUMN checks_json")


def _drop_v13(s):
    """退回 v12:删掉 v13 新增的看片确认表、场景陈设两列、段落场记表列;v14 的也一并退掉。"""
    _drop_v14(s)
    s.conn.execute("DROP TABLE segment_review")
    s.conn.execute("ALTER TABLE scene DROP COLUMN purpose")
    s.conn.execute("ALTER TABLE scene DROP COLUMN furnishings")
    s.conn.execute("ALTER TABLE segment DROP COLUMN blocking_json")


def _drop_v12(s):
    """退回 v11:删掉 v12 新增的白屏放行表(触发器随表一起删);v13 的也一并退掉。"""
    _drop_v13(s)
    s.conn.execute("DROP TABLE blank_override")


def _drop_v11(s):
    """退回 v10:删掉 v11 新增的配乐表与导出记录上的配乐快照列(先删引用它的触发器);v12 的也一并退掉。"""
    _drop_v12(s)
    s.conn.execute("DROP TABLE music_cue")
    s.conn.execute("DROP TRIGGER export_music_immutable")
    s.conn.execute("ALTER TABLE export DROP COLUMN music_json")


def _make_v1(path):
    """造一个 v1 结构的库:建最新结构,再删掉 v2 新增的列、把版本号改回 1。"""
    s = Store(path)
    d = repo.create_drama(s, "玉佩", "9:16")
    s.conn.execute("ALTER TABLE task DROP COLUMN state_since")
    s.conn.execute("ALTER TABLE host DROP COLUMN models_dir")
    s.conn.execute("DROP TABLE llm_run")          # v3 新增(触发器随表一起删)
    s.conn.execute("DROP TABLE export")           # v4 新增
    s.conn.execute("DROP TABLE setting")
    s.conn.execute("DROP TABLE segment_keyframe")                               # v10 新增
    s.conn.execute("ALTER TABLE host DROP COLUMN gpu")                         # v8 新增
    s.conn.execute("DROP TABLE workflow_preset_active")                       # v7 新增
    s.conn.execute("DROP TABLE workflow_preset")
    s.conn.execute("ALTER TABLE character DROP COLUMN fourview_path")        # v6 新增
    s.conn.execute("ALTER TABLE segment DROP COLUMN last_image_prompt")
    s.conn.execute("DROP TABLE music_cue")                                    # v11 新增
    _drop_v12(s)
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
    s.conn.execute("DROP TABLE segment_keyframe")                               # v10 新增
    s.conn.execute("ALTER TABLE host DROP COLUMN gpu")                         # v8 新增
    s.conn.execute("DROP TABLE workflow_preset_active")                       # v7 新增
    s.conn.execute("DROP TABLE workflow_preset")
    s.conn.execute("ALTER TABLE character DROP COLUMN fourview_path")        # v6 新增
    s.conn.execute("ALTER TABLE segment DROP COLUMN last_image_prompt")
    _drop_v11(s)
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
    s.conn.execute("DROP TABLE segment_keyframe")                               # v10 新增
    s.conn.execute("ALTER TABLE host DROP COLUMN gpu")                         # v8 新增
    s.conn.execute("DROP TABLE workflow_preset_active")                       # v7 新增
    s.conn.execute("DROP TABLE workflow_preset")
    s.conn.execute("ALTER TABLE character DROP COLUMN fourview_path")
    s.conn.execute("ALTER TABLE segment DROP COLUMN last_image_prompt")
    _drop_v11(s)
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
    s.conn.execute("DROP TABLE segment_keyframe")                               # v10 新增
    s.conn.execute("ALTER TABLE host DROP COLUMN gpu")
    s.conn.execute("DROP TABLE workflow_preset_active")
    s.conn.execute("DROP TABLE workflow_preset")
    _drop_v11(s)
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
    s.conn.execute("DROP TABLE segment_keyframe")                               # v10 新增
    s.conn.execute("ALTER TABLE host DROP COLUMN gpu")
    _drop_v11(s)
    s.conn.execute("UPDATE meta SET value = '7' WHERE key = 'schema_version'")
    s.close()

    s = Store(path)
    assert get_host(s, hid).ssh_port == 1 and get_host(s, hid).gpu is None
    assert estimate.basis(s).timing is None
    set_gpu(s, hid, "RTX 4090")
    assert estimate.basis(s).note == "按 RTX 4090 估算"
    s.close()


def test_v8_segments_pass_through_v9_without_keyframes(tmp_path):
    """v9 加的三列在 v10 搬走、删掉:v8 的段落一路升上来没有关键帧,段落上也不留那三列。"""
    path = tmp_path / "drama.db"
    s = Store(path)
    d = repo.create_drama(s, "玉佩", "9:16")
    seg = repo.add_segment(s, repo.add_episode(s, d, 1, "script"), 1, duration_sec=8.0)
    s.conn.execute("DROP TABLE segment_keyframe")
    _drop_v11(s)
    s.conn.execute("UPDATE meta SET value = '8' WHERE key = 'schema_version'")
    s.close()

    s = Store(path)
    assert repo.keyframes(s, seg) == [] and not {"key_time_sec", "key_image_prompt", "key_frame_path"} & _cols(s, "segment")
    s.close()


def test_v9_keyframe_moves_into_its_own_table(tmp_path):
    """v10:关键帧搬进 segment_keyframe(一段多张)。v9 段落上设了的那张原样搬过去、标 legacy_v9;没设的段落不搬。"""
    path = tmp_path / "drama.db"
    s = Store(path)
    d = repo.create_drama(s, "玉佩", "9:16")
    ep = repo.add_episode(s, d, 1, "script")
    seg = repo.add_segment(s, ep, 1, duration_sec=8.0)
    plain = repo.add_segment(s, ep, 2, duration_sec=8.0)
    s.conn.execute("DROP TABLE segment_keyframe")
    s.conn.execute("ALTER TABLE segment ADD COLUMN key_time_sec REAL")
    s.conn.execute("ALTER TABLE segment ADD COLUMN key_image_prompt TEXT NOT NULL DEFAULT ''")
    s.conn.execute("ALTER TABLE segment ADD COLUMN key_frame_path TEXT")
    s.conn.execute("UPDATE segment SET key_time_sec = 4.0, key_image_prompt = '@林小雨 转身', key_frame_path = 'k.png'"
                   " WHERE id = ?", (seg,))
    _drop_v11(s)
    s.conn.execute("UPDATE meta SET value = '9' WHERE key = 'schema_version'")
    s.close()

    s = Store(path)
    [k] = repo.keyframes(s, seg)
    assert (k["time_sec"], k["image_prompt"], k["frame_path"], k["legacy_v9"]) == (4.0, "@林小雨 转身", "k.png", 1)
    assert repo.keyframes(s, plain) == []
    assert not {"key_time_sec", "key_image_prompt", "key_frame_path"} & _cols(s, "segment")   # 旧列删掉了
    s.close()


def test_v10_database_gains_music_cues(tmp_path):
    """v11:配乐(整集多段)。旧导出记录的配乐快照为空列表;许可说明空串在库这一层也拒。"""
    import json
    import sqlite3

    import pytest

    from tools.drama.db import now

    path = tmp_path / "drama.db"
    s = Store(path)
    d = repo.create_drama(s, "玉佩", "9:16")
    ep = repo.add_episode(s, d, 1, "script")
    s.conn.execute("INSERT INTO export (episode_id, produce_id, content_producer, inputs_json, created_at)"
                   " VALUES (?, 'P1', 'X', '[]', ?)", (ep, now()))
    _drop_v11(s)
    s.conn.execute("UPDATE meta SET value = '10' WHERE key = 'schema_version'")
    s.close()

    s = Store(path)
    assert json.loads(s.conn.execute("SELECT music_json FROM export").fetchone()[0]) == []
    cid = repo.add_music_cue(s, ep, path="assets/music/a.mp3", sha256="0" * 64, license="曲库 #1", start_sec=0,
                             end_sec=5)
    assert repo.music_cues(s, ep)[0]["id"] == cid and repo.music_cues(s, ep)[0]["gain_db"] == -14
    with pytest.raises(sqlite3.IntegrityError):
        s.conn.execute("UPDATE music_cue SET license = '  ' WHERE id = ?", (cid,))
    with pytest.raises(sqlite3.IntegrityError, match="身份字段不可变"):
        s.conn.execute("UPDATE export SET music_json = '[1]'")
    s.close()


def test_v11_database_gains_locked_blank_overrides(tmp_path):
    """v12:白屏人工放行。理由空串在库这一层也拒;放行记录一经写下不可改。"""
    import sqlite3

    import pytest

    path = tmp_path / "drama.db"
    s = Store(path)
    d = repo.create_drama(s, "玉佩", "9:16")
    seg = repo.add_segment(s, repo.add_episode(s, d, 1, "script"), 1, video_path="v.mp4")
    _drop_v12(s)
    s.conn.execute("UPDATE meta SET value = '11' WHERE key = 'schema_version'")
    s.close()

    s = Store(path)
    oid = repo.add_blank_override(s, seg, video_path="v.mp4", sha256="a" * 64, spans=[[0.5, 1.5]], reason="雪景")
    assert repo.get(s, "blank_override", oid)["reason"] == "雪景"
    with pytest.raises(sqlite3.IntegrityError, match="不可改"):
        s.conn.execute("UPDATE blank_override SET reason = '别的' WHERE id = ?", (oid,))
    with pytest.raises(sqlite3.IntegrityError):
        s.conn.execute("INSERT INTO blank_override (segment_id, video_path, sha256, spans_json, reason, created_at)"
                       " VALUES (?, 'v.mp4', 'b', '[]', '  ', 'now')", (seg,))
    with pytest.raises(ValueError, match="★必填"):
        repo.add_blank_override(s, seg, video_path="v.mp4", sha256="a" * 64, spans=[[0.5, 1.5]], reason=" ")
    s.close()


def test_v12_database_gains_reviews_furnishings_and_blocking(tmp_path):
    """v13:看片确认(写下不可改)、场景用途与陈设、段落场记表;老数据默认空。"""
    import sqlite3

    import pytest

    path = tmp_path / "drama.db"
    s = Store(path)
    d = repo.create_drama(s, "玉佩", "9:16")
    sc = repo.add_scene(s, d, "正堂")
    seg = repo.add_segment(s, repo.add_episode(s, d, 1, "script"), 1, video_path="v.mp4")
    _drop_v13(s)
    s.conn.execute("UPDATE meta SET value = '12' WHERE key = 'schema_version'")
    s.close()

    s = Store(path)
    assert repo.get(s, "scene", sc)["furnishings"] == "" and repo.get(s, "segment", seg)["blocking_json"] == ""
    rid = repo.add_review(s, seg, video_path="v.mp4", sha256="a" * 64, note="")
    assert repo.review(s, seg, "a" * 64)["id"] == rid and repo.review(s, seg, "b" * 64) is None
    with pytest.raises(sqlite3.IntegrityError, match="不可改"):
        s.conn.execute("UPDATE segment_review SET note = '别的' WHERE id = ?", (rid,))
    from tools.llm import runs                                    # llm_run 重建:视觉抽检的目标是段落
    rid = runs.start(s, task="vision_check", target_type="segment", target_id=seg, model="m", input_sha="x",
                     llm_mode="fake", params={})
    assert runs.get(s, rid)["target_type"] == "segment"
    s.close()


def test_v13_to_v14_old_reviews_have_no_ticked_items(tmp_path):
    """v14:看片确认记下人勾了哪些清单项;v13 的老确认为空 = 按旧清单确认的。"""
    path = tmp_path / "drama.db"
    s = Store(path)
    d = repo.create_drama(s, "玉佩", "9:16")
    seg = repo.add_segment(s, repo.add_episode(s, d, 1, "script"), 1, video_path="v.mp4")
    _drop_v14(s)
    s.conn.execute("INSERT INTO segment_review (segment_id, video_path, sha256, note, created_at)"
                   " VALUES (?, 'v.mp4', ?, '', '2026-10-22T00:00:00+00:00')", (seg, "a" * 64))
    s.conn.execute("UPDATE meta SET value = '13' WHERE key = 'schema_version'")
    s.close()

    s = Store(path)
    assert repo.review(s, seg, "a" * 64)["checks_json"] == ""
    repo.add_review(s, seg, video_path="v.mp4", sha256="b" * 64, checks=["physics", "seam"])
    assert repo.review(s, seg, "b" * 64)["checks_json"] == '["physics", "seam"]'
    s.close()


def test_v14_to_v15_llm_runs_gain_a_locked_decision(tmp_path):
    """v15:批准时人的选择(采纳哪些问题 / 挑哪个候选)记进 llm_run,批准后不可改。"""
    import sqlite3

    import pytest

    from tools.llm import runs
    path = tmp_path / "drama.db"
    s = Store(path)
    d = repo.create_drama(s, "玉佩", "9:16")
    _drop_v15(s)
    s.conn.execute("UPDATE meta SET value = '14' WHERE key = 'schema_version'")
    s.close()

    s = Store(path)
    rid = runs.start(s, task="critique", target_type="drama", target_id=d, model="m", input_sha="x",
                     llm_mode="real", params={"role": "观众"})
    runs.propose(s, rid, {"issues": []}, "j")
    runs.approve(s, rid, decision={"adopt": [], "reject": []})
    assert runs.get(s, rid)["decision_json"] == '{"adopt": [], "reject": []}'
    with pytest.raises(sqlite3.IntegrityError, match="不可改"):
        s.conn.execute("UPDATE llm_run SET decision_json = '{}' WHERE id = ?", (rid,))
    s.close()


def test_v15_to_v16_dramas_gain_an_empty_premise(tmp_path):
    """v16:立项发散的结果写进 drama.premise_json;老剧为空。"""
    path = tmp_path / "drama.db"
    s = Store(path)
    d = repo.create_drama(s, "玉佩", "9:16")
    _drop_v16(s)
    s.conn.execute("UPDATE meta SET value = '15' WHERE key = 'schema_version'")
    s.close()

    s = Store(path)
    assert repo.get(s, "drama", d)["premise_json"] == ""
    repo.update(s, "drama", d, premise_json='{"title": "替嫁"}')
    assert repo.get(s, "drama", d)["premise_json"] == '{"title": "替嫁"}'
    s.close()
