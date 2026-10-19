# -*- coding: utf-8 -*-
"""草稿档(10 步)与「这条视频是几步出的」(设计 specs/2026-10-15-多关键帧与草稿档-design.md §三)。"""
import copy
import json

import pytest

from tests.serve.fakes import FakeComfy
from tools.drama import repo
from tools.drama.tasks import set_task_state
from tools.gen import graphs, presets
from tools.gen.planner import plan_video
from tools.serve import rerun
from tools.serve.dispatch import dispatch, steps_of
from tools.serve.fakes import OBJECT_INFO_MIN
from tools.serve.hosts import add_host, get_host

OBJECT_INFO = json.loads(OBJECT_INFO_MIN.read_text(encoding="utf-8"))
NO_SSH = lambda a, s: (0, "")  # noqa: E731


def _params(store, tid):
    return json.loads(repo.get(store, "task", tid)["params_json"])


def _steps30(store):
    edited = copy.deepcopy(presets.example("video")[0])
    edited["18"]["inputs"]["steps"] = 30
    presets.activate(store, "video", presets.confirm_save(store, presets.plan_save(store, "video", edited, OBJECT_INFO)))


def test_set_steps_finds_the_scheduler_by_type():
    wf = presets.example("video")[0]
    assert graphs.steps(graphs.set_steps(wf, 10)) == 10 and graphs.steps(wf) == 20      # 不改原图
    with pytest.raises(ValueError, match="图里有 0 个 BasicScheduler"):
        graphs.set_steps(presets.example("frame")[0], 10)


def test_draft_tier_goes_into_the_params_and_the_workflow(store, sample):
    full = plan_video(store, sample["seg"], seed=1)
    draft = plan_video(store, sample["seg"], seed=1, steps=10)
    assert "steps" not in _params(store, full) and _params(store, draft)["steps"] == 10   # 定稿参数与以前一致
    assert (steps_of(repo.get(store, "task", full)), steps_of(repo.get(store, "task", draft))) == (20, 10)
    with pytest.raises(ValueError, match="步数档只有定稿和草稿"):
        plan_video(store, sample["seg"], seed=1, steps=14)


def test_draft_beats_the_preset_steps(store, sample):
    """当前视频预设改成 30 步:定稿按预设,草稿仍是 10 步(人当场选的比全局预设具体)。"""
    _steps30(store)
    full = plan_video(store, sample["seg"], seed=1)
    draft = plan_video(store, sample["seg"], seed=1, steps=10)
    assert (steps_of(repo.get(store, "task", full)), steps_of(repo.get(store, "task", draft))) == (30, 10)


def test_submitted_workflow_has_the_draft_steps(store, sample, tmp_path):
    for rel in ("frames/s1.png", "assets/lin.png"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"x")
    comfy, host = FakeComfy(), get_host(store, add_host(store, label="a", ssh_host="h", ssh_port=1, ssh_user="root",
                                                        key_ref="k", local_port=8188, vram_gb=24.0, models_dir="/m"))
    tid = plan_video(store, sample["seg"], seed=1, steps=10)
    out = dispatch(store, comfy, host, repo.get(store, "task", tid), data_root=tmp_path, run=NO_SSH)
    assert graphs.steps(comfy.submitted[out.message]) == 10


def test_steps_of_reads_a_custom_rerun_graph(store, sample, tmp_path):
    """改参重跑在画布里改了步数:不看草稿档参数,也认得出来。"""
    tid = plan_video(store, sample["seg"], seed=1)
    graph, _ = rerun.source_graph(tmp_path, repo.get(store, "task", tid))
    graph["18"]["inputs"]["steps"] = 12
    params = {"workflow": "custom", "family": "h3", "graph": graph, "image_refs": [], "writes": "video_path",
              "expect_ext": ".mp4"}
    from tools.drama.tasks import create_task
    custom = create_task(store, kind="video", target_type="segment", target_id=sample["seg"], model="m", params=params,
                         seed=1, prompt="p", refs=[])
    assert steps_of(repo.get(store, "task", custom)) == 12


def _done(store, tid, path):
    set_task_state(store, tid, "queued", external_id=f"p{tid}")
    set_task_state(store, tid, "done", result_path=path, result_sha256="x")


def test_videos_panel_offers_two_tiers_and_tags_few_step_clips(client, store, sample):
    add_host(store, label="a", ssh_host="h", ssh_port=1, ssh_user="root", key_ref="k", local_port=8188,
             vram_gb=24.0, models_dir="/m", gpu="RTX 4090")
    page = client.get(f"/episode/{sample['episode']}/videos").text
    assert "定稿 · 20 步(默认,约 15 分钟)" in page and "草稿 · 10 步(约省一半时间,约 7 分钟)" in page
    client.post(f"/segment/{sample['seg']}/video", data={"seed": "5", "steps": "10"})
    draft = store.conn.execute("SELECT id FROM task WHERE kind = 'video'").fetchone()[0]
    assert _params(store, draft)["steps"] == 10
    _done(store, draft, "outputs/tasks/9/draft.mp4")
    repo.update(store, "segment", sample["seg"], video_path="outputs/tasks/9/draft.mp4")
    page = client.get(f"/episode/{sample['episode']}/videos").text
    assert "草稿 · 10 步</span>" in page and "当前视频只用了 10 步" in page and "道具重复" in page
    _steps30(store)
    assert "定稿 · 30 步(当前预设" in client.get(f"/episode/{sample['episode']}/videos").text


def test_final_panel_warns_about_few_step_clips_without_blocking(client, store, sample):
    tid = plan_video(store, sample["seg"], seed=1, steps=10)
    _done(store, tid, "videos/s1.mp4")                               # sample 段落当前的视频就是它
    page = client.get(f"/episode/{sample['episode']}/final").text
    assert "段落 1 的视频只用了 10 步" in page and "(不拦导出)" in page
