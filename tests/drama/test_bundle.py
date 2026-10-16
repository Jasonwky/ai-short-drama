# -*- coding: utf-8 -*-
import json
import sqlite3
import zipfile

import pytest

from tools.drama import bundle, repo
from tools.drama.db import Store
from tools.drama.tasks import create_task, set_task_state, tasks_for
from tools.llm import runs


def _file(root, rel, data):
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(data)


def _done(store, root, ttype, tid, rel, data, refs=(), writes="first_frame_path"):
    _file(root, rel, data)
    t = create_task(store, kind="image", target_type=ttype, target_id=tid, model="m", params={
        "writes": writes, "image_refs": list(refs)}, seed=5, prompt="p", refs=list(refs))
    set_task_state(store, t, "queued", external_id=f"p{t}")
    set_task_state(store, t, "done", result_path=rel, result_sha256="x")
    return t


@pytest.fixture
def project(store, sample, tmp_path):
    """sample 剧 + 真文件 + 两版首帧历史 + 一条已批准的 LLM 记录。"""
    root = tmp_path / "src"
    for rel in ("assets/lin.png", "assets/shen.png", "assets/yard.png", "assets/jade.png", "videos/s1.mp4"):
        _file(root, rel, rel.encode())
    _done(store, root, "segment", sample["seg"], "outputs/tasks/1/a.png", b"v1", refs=["assets/lin.png"])
    _done(store, root, "segment", sample["seg"], "outputs/tasks/2/a.png", b"v2", refs=["assets/lin.png"])
    repo.update(store, "segment", sample["seg"], first_frame_path="outputs/tasks/2/a.png")
    _file(root, "journal/llm/run.json", b"{}")
    rid = runs.start(store, task="voice", target_type="character", target_id=sample["lin"], model="m",
                     input_sha="s", llm_mode="real", params={})
    runs.propose(store, rid, {"gender": "女"}, str(root / "journal/llm/run.json"))
    runs.approve(store, rid)
    return {**sample, "root": root}


def test_round_trip_keeps_history_bindings_and_locks(store, project, tmp_path):
    rel = bundle.export_bundle(store, project["root"], project["drama"])
    dest_root = tmp_path / "dst"
    dest = Store(":memory:")
    new = bundle.import_bundle(dest, dest_root, project["root"] / rel)

    ep = dest.conn.execute("SELECT * FROM episode WHERE drama_id = ?", (new,)).fetchone()
    [seg] = dest.conn.execute("SELECT * FROM segment WHERE episode_id = ?", (ep["id"],)).fetchall()
    assets = repo.segment_assets(dest, seg["id"])
    assert [c["name"] for c in assets["characters"]] == ["林小雨"] and assets["scene"]["name"] == "老宅庭院"
    assert [p["name"] for p in assets["props"]] == ["玉佩"]                         # 绑定跟过来了

    prefix = seg["first_frame_path"].split("/outputs/")[0]
    assert prefix.startswith("imports/") and seg["first_frame_path"] == f"{prefix}/outputs/tasks/2/a.png"
    assert (dest_root / seg["first_frame_path"]).read_bytes() == b"v2"
    hist = tasks_for(dest, "segment", seg["id"])
    assert [t["result_path"] for t in hist] == [f"{prefix}/outputs/tasks/1/a.png", f"{prefix}/outputs/tasks/2/a.png"]
    assert json.loads(hist[0]["refs_json"]) == [f"{prefix}/assets/lin.png"]
    assert json.loads(hist[0]["params_json"])["image_refs"] == [f"{prefix}/assets/lin.png"]
    assert hist[0]["host_id"] is None and hist[0]["external_id"] == "p1" and hist[0]["seed"] == 5

    lin = dest.conn.execute("SELECT * FROM character WHERE drama_id = ? AND name = '林小雨'", (new,)).fetchone()
    assert (dest_root / lin["turnaround_path"]).read_bytes() == b"assets/lin.png"
    [run] = runs.history(dest, "character", lin["id"])
    assert run["state"] == "approved" and runs.output(run) == {"gender": "女"}         # 审批锁定跟过来了
    assert (dest_root / prefix / "journal/llm/run.json").read_bytes() == b"{}"
    assert run["journal_path"] == str(dest_root.resolve() / prefix / "journal/llm/run.json")


def test_import_into_the_same_project_is_a_separate_copy(store, project):
    rel = bundle.export_bundle(store, project["root"], project["drama"])
    new = bundle.import_bundle(store, project["root"], project["root"] / rel)
    assert new != project["drama"]
    assert repo.get(store, "segment", project["seg"])["first_frame_path"] == "outputs/tasks/2/a.png"   # 原剧不动
    with pytest.raises(sqlite3.IntegrityError, match="task 的生成参数不可变"):          # 导入的 task 照样受触发器保护
        store.conn.execute("UPDATE task SET seed = 1 WHERE target_type = 'segment' AND target_id != ?",
                           (project["seg"],))


def test_export_refuses_in_flight_and_missing_files(store, project):
    t = create_task(store, kind="video", target_type="segment", target_id=project["seg"], model="m", params={},
                    seed=1, prompt="p", refs=[])
    (project["root"] / "assets/jade.png").unlink()
    with pytest.raises(ValueError) as e:
        bundle.export_bundle(store, project["root"], project["drama"])
    assert f"task #{t} 还在「pending」" in str(e.value) and "文件不在本机:assets/jade.png" in str(e.value)
    assert not (project["root"] / "outputs/bundles").exists()


def _rezip(src, dst, edit):
    with zipfile.ZipFile(src) as z:
        items = {n: z.read(n) for n in z.namelist()}
    m = json.loads(items["manifest.json"])
    edit(m, items)
    items["manifest.json"] = json.dumps(m).encode()
    with zipfile.ZipFile(dst, "w") as z:
        for n, b in items.items():
            z.writestr(n, b)


def test_import_rejects_tampered_or_unsafe_bundles(store, project, tmp_path):
    good = project["root"] / bundle.export_bundle(store, project["root"], project["drama"])
    dest_root = tmp_path / "dst"
    cases = {
        "sha256 与清单不符": lambda m, i: i.__setitem__("files/assets/lin.png", b"evil"),
        "不许跨版本导入": lambda m, i: m.__setitem__("schema_version", 1),
        "不安全的路径": lambda m, i: m["files"].__setitem__("../escape.txt", "0"),
        "缺文件": lambda m, i: m["files"].pop("assets/yard.png"),
    }
    for why, edit in cases.items():
        bad = tmp_path / f"{len(why)}.zip"
        _rezip(good, bad, edit)
        with pytest.raises(ValueError, match=why):
            bundle.import_bundle(store, dest_root, bad)
    assert store.conn.execute("SELECT COUNT(*) FROM drama").fetchone()[0] == 1          # 一部都没多
    left = dest_root / "imports"
    assert not left.exists() or not any(left.iterdir())                               # 落了一半的文件已删掉


def test_not_a_bundle(store, tmp_path):
    with zipfile.ZipFile(tmp_path / "x.zip", "w") as z:
        z.writestr("readme.txt", "hi")
    with pytest.raises(ValueError, match="缺少 manifest.json"):
        bundle.import_bundle(store, tmp_path, tmp_path / "x.zip")


def test_scene_and_prop_llm_runs_travel_too(store, project, tmp_path):
    """v5 起场景 / 道具也能有 LLM 记录(从图反推描述),工程包要把它们带上。"""
    rid = runs.start(store, task="describe_prop", target_type="prop", target_id=project["jade"], model="m",
                     input_sha="s", llm_mode="real", params={})
    runs.propose(store, rid, {"description": "绿玉"}, str(project["root"] / "journal/llm/run.json"))
    rel = bundle.export_bundle(store, project["root"], project["drama"])
    dest = Store(":memory:")
    new = bundle.import_bundle(dest, tmp_path / "dst", project["root"] / rel)
    jade = dest.conn.execute("SELECT id FROM prop WHERE drama_id = ?", (new,)).fetchone()[0]
    [run] = runs.history(dest, "prop", jade)
    assert run["task"] == "describe_prop" and runs.output(run) == {"description": "绿玉"}


def test_imported_segments_do_not_take_ids_owned_by_old_history(store, project, tmp_path):
    rel = bundle.export_bundle(store, project["root"], project["drama"])
    create_task(store, kind="video", target_type="segment", target_id=900, model="m", params={}, seed=1,
                prompt="p", refs=[])                               # 某个已删段落留下的历史
    new = bundle.import_bundle(store, project["root"], project["root"] / rel)
    ep = store.conn.execute("SELECT id FROM episode WHERE drama_id = ?", (new,)).fetchone()[0]
    [seg] = [r[0] for r in store.conn.execute("SELECT id FROM segment WHERE episode_id = ?", (ep,))]
    assert seg > 900


def test_alignment_task_video_path_moves_with_the_bundle(store, project, tmp_path):
    """对齐任务的参数里存着视频路径(规划时定死、执行前核对),导入后要跟着加前缀。"""
    from tools.gen.align import plan_align
    t = plan_align(store, project["seg"])
    _file(project["root"], "outputs/tasks/9/subtitles.srt", b"1\n")
    set_task_state(store, t, "queued", external_id=f"align-{t}")
    set_task_state(store, t, "done", result_path="outputs/tasks/9/subtitles.srt", result_sha256="x")
    dest = Store(":memory:")
    new = bundle.import_bundle(dest, tmp_path / "dst", project["root"] / bundle.export_bundle(store, project["root"],
                                                                                              project["drama"]))
    [seg] = dest.conn.execute("SELECT s.* FROM segment s JOIN episode e ON e.id = s.episode_id WHERE e.drama_id = ?",
                              (new,)).fetchall()
    [align] = [r for r in tasks_for(dest, "segment", seg["id"]) if r["kind"] == "align"]
    assert json.loads(align["params_json"])["video"] == seg["video_path"]


def test_custom_workflow_image_paths_move_with_the_bundle(store, project, tmp_path):
    """改参重跑的任务把整份工作流存在参数里,「加载图像」节点写的是素材库路径 —— 导入后也要加前缀。"""
    graph = {"18": {"class_type": "LoadImage", "inputs": {"image": "assets/lin.png"}},
             "27": {"class_type": "KSampler", "inputs": {"seed": 5}}}
    t = create_task(store, kind="image", target_type="segment", target_id=project["seg"], model="m", seed=5,
                    params={"workflow": "custom", "graph": graph, "image_refs": ["assets/lin.png"],
                            "writes": "first_frame_path", "expect_ext": ".png"}, prompt="p", refs=["assets/lin.png"])
    set_task_state(store, t, "queued", external_id="px")
    set_task_state(store, t, "done", result_path="outputs/tasks/1/a.png", result_sha256="x")
    dest = Store(":memory:")
    new = bundle.import_bundle(dest, tmp_path / "dst", project["root"] / bundle.export_bundle(store, project["root"],
                                                                                              project["drama"]))
    [seg] = dest.conn.execute("SELECT s.* FROM segment s JOIN episode e ON e.id = s.episode_id WHERE e.drama_id = ?",
                              (new,)).fetchall()
    [custom] = [r for r in tasks_for(dest, "segment", seg["id"]) if '"custom"' in r["params_json"]]
    p = json.loads(custom["params_json"])
    prefix = seg["first_frame_path"].split("/outputs/")[0]
    assert p["graph"]["18"]["inputs"]["image"] == f"{prefix}/assets/lin.png" == p["image_refs"][0]


def test_keyframes_and_their_task_ids_move_with_the_bundle(store, project, tmp_path):
    """关键帧随段落过去(图加前缀、legacy_v9 原样);任务参数里的 keyframe_id 按新编号重映射,原库已删的仍是「已删除」。"""
    from tools.drama import targets
    gone = repo.add_keyframe(store, project["seg"], 1.0)
    repo.delete_keyframe(store, gone)                                   # 原库编号从 2 起,与导入后的新编号错开
    _file(project["root"], "outputs/tasks/8/k.png", b"k")
    k = repo.add_keyframe(store, project["seg"], 4.0, "p", "outputs/tasks/8/k.png")
    for kid in (k, gone):                                               # 在途的不让导出:记成失败的尝试
        t = create_task(store, kind="image", target_type="segment", target_id=project["seg"], model="m", seed=1,
                        prompt="p", params={"writes": "key_frame_path", "keyframe_id": kid}, refs=[])
        set_task_state(store, t, "failed", error="x")
    dest = Store(":memory:")
    new = bundle.import_bundle(dest, tmp_path / "dst", project["root"] / bundle.export_bundle(store, project["root"],
                                                                                              project["drama"]))
    [seg] = dest.conn.execute("SELECT s.* FROM segment s JOIN episode e ON e.id = s.episode_id WHERE e.drama_id = ?",
                              (new,)).fetchall()
    prefix = seg["first_frame_path"].split("/outputs/")[0]
    [nk] = repo.keyframes(dest, seg["id"])
    assert (nk["time_sec"], nk["frame_path"], nk["legacy_v9"]) == (4.0, f"{prefix}/outputs/tasks/8/k.png", 0)
    assert (tmp_path / "dst" / nk["frame_path"]).read_bytes() == b"k"
    mine, deleted = [r for r in tasks_for(dest, "segment", seg["id"]) if "key" in r["params_json"]]
    assert targets.slot(dest, mine) == ("segment_keyframe", nk["id"], "frame_path") and nk["id"] != k
    assert json.loads(deleted["params_json"])["keyframe_id"] is None and targets.slot(dest, deleted)[1] is None


def test_video_anchors_move_with_the_bundle(store, project, tmp_path):
    """视频任务参数里的锚点 [[路径, 帧号], …] 导入后也要加前缀(P7 起就漏了;关键帧对比要从它取图)。"""
    _file(project["root"], "outputs/tasks/8/k.png", b"k")
    repo.add_keyframe(store, project["seg"], 4.0, "p", "outputs/tasks/8/k.png")
    t = create_task(store, kind="video", target_type="segment", target_id=project["seg"], model="m", seed=5,
                    params={"workflow": "minimax_h3", "writes": "video_path", "image_refs": ["outputs/tasks/2/a.png"],
                            "anchors": [["outputs/tasks/2/a.png", 0], ["outputs/tasks/8/k.png", 96]]},
                    prompt="p", refs=["outputs/tasks/2/a.png", "outputs/tasks/8/k.png"])
    set_task_state(store, t, "queued", external_id="pv")
    set_task_state(store, t, "done", result_path="videos/s1.mp4", result_sha256="x")
    dest = Store(":memory:")
    new = bundle.import_bundle(dest, tmp_path / "dst", project["root"] / bundle.export_bundle(store, project["root"],
                                                                                              project["drama"]))
    [seg] = dest.conn.execute("SELECT s.* FROM segment s JOIN episode e ON e.id = s.episode_id WHERE e.drama_id = ?",
                              (new,)).fetchall()
    prefix = seg["first_frame_path"].split("/outputs/")[0]
    [video] = [r for r in tasks_for(dest, "segment", seg["id"]) if r["kind"] == "video"]
    assert json.loads(video["params_json"])["anchors"] == [[f"{prefix}/outputs/tasks/2/a.png", 0],
                                                            [f"{prefix}/outputs/tasks/8/k.png", 96]]
