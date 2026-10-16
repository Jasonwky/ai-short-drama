# -*- coding: utf-8 -*-
"""改参重跑:打开当时实际提交的工作流 → 改 → 过把关 → 同目标新版本 → dispatch 原样提交。"""
import copy
import json

import pytest

from tests.serve.fakes import FakeComfy
from tools.drama import repo
from tools.drama.tasks import set_task_state, tasks_for
from tools.gen.align import plan_align
from tools.gen.assets import lock_seed, plan_asset_image
from tools.gen.planner import plan_first_frame, plan_key_frame
from tools.serve import rerun
from tools.serve.dispatch import SUBMITTED, dispatch
from tools.serve.fakes import OBJECT_INFO_MIN
from tools.serve.hosts import get_host

OBJECT_INFO = json.loads(OBJECT_INFO_MIN.read_text(encoding="utf-8"))
NO_SSH = lambda a, s: (0, "")  # noqa: E731


@pytest.fixture
def world(store, sample, host_id, tmp_path):
    for rel in ("assets/lin.png", "assets/yard.png", "assets/jade.png", "frames/s1.png", "videos/s1.mp4"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"x")
    comfy = FakeComfy()
    tid = plan_first_frame(store, sample["seg"], seed=7)
    dispatch(store, comfy, get_host(store, host_id), repo.get(store, "task", tid), data_root=tmp_path, run=NO_SSH)
    return {"root": tmp_path, "comfy": comfy, "host": get_host(store, host_id), "task": tid}


def test_opens_what_was_actually_submitted_with_library_paths(store, world):
    graph, rebuilt = rerun.source_graph(world["root"], repo.get(store, "task", world["task"]))
    assert not rebuilt
    assert sorted(set(graph[n]["inputs"]["image"] for n in ("18", "19", "20"))) == [
        "assets/jade.png", "assets/lin.png", "assets/yard.png"]                    # 上传名换回了素材库路径
    assert graph["27"]["inputs"]["seed"] == 7


def test_rerun_with_a_new_seed_and_sampler_becomes_a_new_version(store, sample, world):
    graph, _ = rerun.source_graph(world["root"], repo.get(store, "task", world["task"]))
    edited = copy.deepcopy(graph)
    edited["27"]["inputs"]["seed"] = 12345
    edited["27"]["inputs"]["sampler_name"] = "euler_ancestral"
    tid, problems = rerun.plan_rerun(store, world["root"], world["task"], edited, OBJECT_INFO)
    assert problems == []
    t = repo.get(store, "task", tid)
    p = json.loads(t["params_json"])
    assert (t["seed"], t["kind"], t["target_id"], p["workflow"], p["from_task"], p["writes"]) == (
        12345, "image", sample["seg"], "custom", world["task"], "first_frame_path")
    assert t["prompt"] == repo.get(store, "task", world["task"])["prompt"]
    assert sorted(json.loads(t["refs_json"])) == ["assets/jade.png", "assets/lin.png", "assets/yard.png"]
    out = dispatch(store, world["comfy"], world["host"], t, data_root=world["root"], run=NO_SSH)
    sent = world["comfy"].submitted[out.message]
    assert sent["27"]["inputs"]["seed"] == 12345 and sent["27"]["inputs"]["sampler_name"] == "euler_ancestral"
    assert sent["18"]["inputs"]["image"].startswith("ai-short-drama/")             # 提交时参考图换成了上传名
    assert len(tasks_for(store, "segment", sample["seg"])) == 2                     # 旧版还在,新版并列


def test_keyframe_rerun_writes_back_to_the_same_keyframe(store, sample, world):
    """关键帧的新版本写回同一张;那一张删了就不让再出。"""
    k = repo.add_keyframe(store, sample["seg"], 3.0, "@林小雨 在 @老宅庭院 转身,握着 @玉佩")
    src = plan_key_frame(store, k, seed=3)
    dispatch(store, world["comfy"], world["host"], repo.get(store, "task", src), data_root=world["root"], run=NO_SSH)
    graph, _ = rerun.source_graph(world["root"], repo.get(store, "task", src))
    graph["27"]["inputs"]["seed"] = 99
    tid, problems = rerun.plan_rerun(store, world["root"], src, graph, OBJECT_INFO)
    assert problems == [] and json.loads(repo.get(store, "task", tid)["params_json"])["keyframe_id"] == k
    repo.delete_keyframe(store, k)
    tid, problems = rerun.plan_rerun(store, world["root"], src, graph, OBJECT_INFO)
    assert tid is None and problems == ["这张关键帧已经删除,不能再为它出新版本"]


def test_console_fields_are_locked(store, world):
    graph, _ = rerun.source_graph(world["root"], repo.get(store, "task", world["task"]))
    edited = copy.deepcopy(graph)
    edited["21"]["inputs"]["prompt"] = "画布里改的提示词"
    edited["30"]["inputs"]["width"] = 512
    tid, problems = rerun.plan_rerun(store, world["root"], world["task"], edited, OBJECT_INFO)
    assert tid is None
    assert any("21.prompt" in p for p in problems) and any("节点 30" in p for p in problems)


def test_tampered_submitted_workflow_is_refused(store, world):
    f = world["root"] / "outputs/tasks" / str(world["task"]) / SUBMITTED
    f.write_text(f.read_text(encoding="utf-8").replace('"euler"', '"dpmpp_2m"'), encoding="utf-8")
    with pytest.raises(ValueError, match="被改过"):
        rerun.source_graph(world["root"], repo.get(store, "task", world["task"]))


def test_old_task_without_a_submitted_file_is_rebuilt_and_says_so(store, sample, world):
    old = plan_first_frame(store, sample["seg"], seed=9)          # 还没提交过:没有 workflow.json
    graph, rebuilt = rerun.source_graph(world["root"], repo.get(store, "task", old))
    assert rebuilt and graph["27"]["inputs"]["seed"] == 9


def test_only_comfyui_tasks_are_editable(store, sample, world):
    assert rerun.editable(repo.get(store, "task", world["task"]))
    assert not rerun.editable(repo.get(store, "task", plan_align(store, sample["seg"])))


def test_locked_character_seed_applies_to_reruns(store, sample, world):
    store.conn.execute("UPDATE drama SET style = 'realistic' WHERE id = ?", (sample["drama"],))
    repo.update(store, "character", sample["lin"], final_prompt="A young woman.")
    (world["root"] / "assets/_canvas").mkdir(parents=True, exist_ok=True)
    (world["root"] / "assets/_canvas/gray_1024.png").write_bytes(b"x")
    tid = plan_asset_image(store, "character", sample["lin"], seed=55)
    dispatch(store, world["comfy"], world["host"], repo.get(store, "task", tid), data_root=world["root"], run=NO_SSH)
    set_task_state(store, tid, "done", result_path="outputs/tasks/x/lin2.png")
    lock_seed(store, sample["lin"], tid)
    graph, _ = rerun.source_graph(world["root"], repo.get(store, "task", tid))
    edited = copy.deepcopy(graph)
    edited["27"]["inputs"]["seed"] = 56
    _, problems = rerun.plan_rerun(store, world["root"], tid, edited, OBJECT_INFO)
    assert problems == ["种子已锁定为 55 —— 要换种子先在资产面板解锁"]
