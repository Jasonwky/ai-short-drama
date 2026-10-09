# -*- coding: utf-8 -*-
"""模板预设:保存前逐一试拼把关、版本、当前指针、规划时绑定、dispatch 时叠上。"""
import copy
import json
from pathlib import Path

import pytest

from tests.serve.fakes import FakeComfy
from tools.drama import repo
from tools.gen import presets
from tools.gen.planner import plan_first_frame, plan_video
from tools.serve.dispatch import SUBMITTED, dispatch
from tools.serve.hosts import add_host, get_host

OBJECT_INFO = json.loads((Path(__file__).parents[2] / "tools/serve/fixtures/object_info_min.json").read_text(encoding="utf-8"))


def _edited(category, change):
    wf = copy.deepcopy(presets.example(category)[0])
    change(wf)
    return wf


def test_save_lists_the_changes_and_tries_every_case(store):
    def euler_a(wf):
        wf["27"]["inputs"]["sampler_name"] = "euler_ancestral"
    plan = presets.plan_save(store, "frame", _edited("frame", euler_a), OBJECT_INFO)
    assert plan.problems == () and plan.lines == ("节点 27 KSampler.sampler_name:'euler' → 'euler_ancestral'",)
    pid = presets.confirm_save(store, plan, "  试试祖先采样  ")
    row = presets.get(store, pid)
    assert (row["category"], row["version"], row["note"], row["based_on"]) == ("frame", 1, "试试祖先采样", None)
    assert presets.active(store, "frame") is None                    # 存了不等于设为当前


def test_a_change_that_breaks_some_case_is_refused(store):
    def bad(wf):
        wf["27"]["inputs"]["sampler_name"] = "no_such_sampler"     # 云机上没有的采样器
    plan = presets.plan_save(store, "frame", _edited("frame", bad), OBJECT_INFO)
    assert plan.problems and all("不在云机的可选项里" in p for p in plan.problems)
    assert len(plan.problems) == 6                                   # 1–3 张图 × 两种画幅,每种都报
    with pytest.raises(ValueError):
        presets.confirm_save(store, plan)


def test_non_whitelisted_model_and_deleting_console_nodes_are_refused(store):
    def flux(wf):
        wf["90"] = {"class_type": "UNETLoader", "inputs": {"unet_name": "flux1-dev-fp8.safetensors", "weight_dtype": "default"}}
    assert any("没有登记许可证" in p for p in presets.plan_save(store, "frame", _edited("frame", flux), OBJECT_INFO).problems)

    def drop_image(wf):
        del wf["18"]
    assert presets.plan_save(store, "frame", _edited("frame", drop_image), OBJECT_INFO).problems == (
        "节点 18(LoadImage)归控制台,不能删",)


def test_nothing_changed_is_not_saved(store):
    plan = presets.plan_save(store, "video", presets.example("video")[0], OBJECT_INFO)
    assert "和内置模板没有任何不同" in plan.problems[0]


def test_video_steps_change_survives_every_slot_and_anchor_combination(store):
    def steps30(wf):
        wf["18"]["inputs"]["steps"] = 30
    plan = presets.plan_save(store, "video", _edited("video", steps30), OBJECT_INFO)
    assert plan.problems == ()
    presets.activate(store, "video", presets.confirm_save(store, plan))
    assert presets.video_steps(store) == 30
    presets.activate(store, "video", None)                           # 回到内置
    assert presets.video_steps(store) is None


def test_versions_chain_and_activation_rules(store):
    def a(wf):
        wf["27"]["inputs"]["scheduler"] = "normal"

    def b(wf):
        wf["27"]["inputs"]["scheduler"] = "beta"
    v1 = presets.confirm_save(store, presets.plan_save(store, "frame", _edited("frame", a), OBJECT_INFO))
    presets.activate(store, "frame", v1)
    assert presets.editing_graph(store, "frame")["27"]["inputs"]["scheduler"] == "normal"   # 在当前版上接着改
    same = presets.plan_save(store, "frame", _edited("frame", a), OBJECT_INFO)
    assert "和当前版本 v1 一样" in same.problems[0]
    v2 = presets.confirm_save(store, presets.plan_save(store, "frame", _edited("frame", b), OBJECT_INFO))
    assert presets.get(store, v2)["based_on"] == v1 and presets.get(store, v2)["version"] == 2
    with pytest.raises(ValueError, match="不是「视频」类的"):
        presets.activate(store, "video", v2)


def test_tasks_bind_the_current_preset_at_planning_time(store, sample, tmp_path):
    for rel in ("assets/lin.png", "assets/yard.png", "assets/jade.png", "frames/s1.png"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"png")

    def normal(wf):
        wf["27"]["inputs"]["scheduler"] = "normal"
    pid = presets.confirm_save(store, presets.plan_save(store, "frame", _edited("frame", normal), OBJECT_INFO))
    builtin_task = plan_first_frame(store, sample["seg"], seed=1)
    presets.activate(store, "frame", pid)
    preset_task = plan_first_frame(store, sample["seg"], seed=2)
    presets.activate(store, "frame", None)                           # 切回内置:已经排着的任务仍用它绑定的那份
    p = json.loads(repo.get(store, "task", preset_task)["params_json"])
    assert (p["preset_id"], p["preset_version"], p["template"]) == (pid, 1, "frame")
    assert "preset_id" not in json.loads(repo.get(store, "task", builtin_task)["params_json"])
    assert "preset_id" not in json.loads(repo.get(store, "task", plan_video(store, sample["seg"], seed=3))["params_json"])

    comfy = FakeComfy()
    host = get_host(store, add_host(store, label="4080S", ssh_host="h", ssh_port=1, ssh_user="root", key_ref="k",
                                    local_port=8188, vram_gb=32.0, models_dir="/m"))
    for tid, want in ((builtin_task, "simple"), (preset_task, "normal")):
        out = dispatch(store, comfy, host, repo.get(store, "task", tid), data_root=tmp_path, run=lambda a, s: (0, ""))
        assert comfy.submitted[out.message]["27"]["inputs"]["scheduler"] == want
        saved = json.loads((tmp_path / "outputs/tasks" / str(tid) / SUBMITTED).read_text(encoding="utf-8"))
        assert saved["submitted"] == comfy.submitted[out.message]                 # 存下的就是实际提交的那份
        assert saved["names"]["assets/lin.png"].endswith("/lin.png")
        assert (tmp_path / "outputs/tasks" / str(tid) / (SUBMITTED + ".sha256")).is_file()


def test_estimates_follow_the_current_video_preset_steps(store, sample):
    from tools.drama import invalidation
    from tools.gen.capability import MINIMAX_H3, QWEN_IMAGE_EDIT_2511
    from tools.gen.fill import plan_fill
    caps = {"video_cap": MINIMAX_H3, "image_cap": QWEN_IMAGE_EDIT_2511}
    before = invalidation.impact(store, "voice", sample["lin"], **caps).est_seconds

    def steps40(wf):
        wf["18"]["inputs"]["steps"] = 40
    presets.activate(store, "video", presets.confirm_save(store, presets.plan_save(store, "video", _edited("video", steps40), OBJECT_INFO)))
    after = invalidation.impact(store, "voice", sample["lin"], **caps).est_seconds
    assert after == pytest.approx(before * 2)                         # 20 步 → 40 步,重跑代价翻倍
    repo.update(store, "segment", sample["seg"], video_path=None)
    [item] = plan_fill(store, "videos", sample["episode"]).items
    assert item.est_seconds == pytest.approx(MINIMAX_H3.est_seconds(192, 40))
