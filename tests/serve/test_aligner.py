# -*- coding: utf-8 -*-
"""字幕对齐任务的执行:假云机(上传)+ 假对齐脚本(ssh runner),本机真 ffmpeg 算能量曲线与时长。"""
import json

import pytest

from tools.drama import repo
from tools.gen.align import plan_align
from tools.serve import aligner
from tools.serve.fakes import FakeAligner, FakeComfy, FakeTunnel, sample_mp4
from tools.serve.hosts import get_host
from tools.serve.worker import Worker

LINE = "这块玉佩，是我母亲留给我的。"


@pytest.fixture(scope="module")
def mp4():
    return sample_mp4()


@pytest.fixture
def world(store, sample, host_id, tmp_path, mp4):
    (tmp_path / "videos").mkdir()
    (tmp_path / "videos/s1.mp4").write_bytes(mp4)
    return {"root": tmp_path, "host": get_host(store, host_id), "comfy": FakeComfy()}


def _run(store, world, tid, runner):
    return aligner.run(store, world["comfy"], world["host"], repo.get(store, "task", tid), data_root=world["root"],
                       run=runner)


def test_aligned_subtitles_are_written_back(store, sample, world):
    tid = plan_align(store, sample["seg"])
    runner = FakeAligner("这块玉佩是我母亲留给我的")
    out = _run(store, world, tid, runner)
    t = repo.get(store, "task", tid)
    assert out.state == "done" and t["state"] == "done" and t["host_id"] == world["host"].id
    assert repo.get(store, "segment", sample["seg"])["subtitle_path"] == f"outputs/tasks/{tid}/subtitles.srt"
    srt = (world["root"] / t["result_path"]).read_text(encoding="utf-8")
    assert srt.startswith("1\n00:00:00,000 --> ") and LINE in srt
    # 视频按内容哈希分子目录传上去,云机脚本拿到的是 ComfyUI input 目录下的绝对路径
    assert world["comfy"].uploaded == [(world["root"] / "videos/s1.mp4").as_posix()]
    model_dir, remote = runner.calls[0]
    assert remote.startswith("/root/autodl-tmp/ComfyUI/input/ai-short-drama/") and remote.endswith("/s1.mp4")
    saved = json.loads((world["root"] / f"outputs/tasks/{tid}/align.json").read_text(encoding="utf-8"))
    assert len(saved["envelope"]) == 10 and saved["problems"] == []


def test_failed_check_keeps_the_evidence_and_writes_nothing(store, sample, world):
    tid = plan_align(store, sample["seg"])
    out = _run(store, world, tid, FakeAligner("今天天气真不错"))
    assert out.state == "failed" and "判卷不过" in out.message and "align.json" in out.message
    assert (world["root"] / f"outputs/tasks/{tid}/align.json").is_file()
    assert repo.get(store, "segment", sample["seg"])["subtitle_path"] is None


@pytest.mark.parametrize("code,state", [(255, "pending"), (3, "failed")])
def test_ssh_unreachable_waits_but_script_errors_fail(store, sample, world, code, state):
    tid = plan_align(store, sample["seg"])
    out = _run(store, world, tid, lambda args, stdin: (code, "显存不够" if code == 3 else "Connection refused"))
    assert out.state == state == repo.get(store, "task", tid)["state"]


def test_unreachable_comfy_leaves_it_pending(store, sample, world):
    tid = plan_align(store, sample["seg"])
    world["comfy"].reachable = False
    assert _run(store, world, tid, FakeAligner(LINE)).state == "pending"


def test_video_swapped_after_planning_voids_the_task(store, sample, world, mp4):
    tid = plan_align(store, sample["seg"])
    (world["root"] / "videos/s2.mp4").write_bytes(mp4)
    repo.update(store, "segment", sample["seg"], video_path="videos/s2.mp4")
    out = _run(store, world, tid, FakeAligner(LINE))
    assert out.state == "failed" and "换了视频" in out.message


def test_existing_subtitles_are_not_overwritten(store, sample, world):
    repo.update(store, "segment", sample["seg"], subtitle_path="subs/old.srt")
    tid = plan_align(store, sample["seg"])
    out = _run(store, world, tid, FakeAligner(LINE))
    assert out.state == "done" and "待人挑选" in out.message
    assert repo.get(store, "segment", sample["seg"])["subtitle_path"] == "subs/old.srt"


def test_worker_runs_align_tasks_through_the_align_runner(store, sample, host_id, world):
    runner = FakeAligner(LINE)

    def no_preflight(host):
        def run(args, stdin):
            raise AssertionError("对齐任务不该走 LoRA 预检的 runner")
        return run
    w = Worker(store, host_id, data_root=world["root"], client_for=lambda h: world["comfy"], tunnel=FakeTunnel(),
               run_for=no_preflight, align_for=lambda h: runner)
    tid = plan_align(store, sample["seg"])
    w.tick()
    assert repo.get(store, "task", tid)["state"] == "done" and len(runner.calls) == 1


def test_comfyui_busy_with_anyone_waits_without_freeing(store, sample, world):
    """共用的 ComfyUI 有任务在跑 / 排队:不打断、不释放,留在待办下一轮再试。"""
    tid = plan_align(store, sample["seg"])
    world["comfy"].pending.append("someone-else")
    world["comfy"].vram_gb = 1.0
    runner = FakeAligner(LINE)
    out = _run(store, world, tid, runner)
    assert out.state == "pending" and "队列" in out.message
    assert runner.calls == [] and world["comfy"].freed == 0


def test_low_vram_frees_comfyui_models_before_aligning(store, sample, world):
    """2026-09-29 真链路:H3 视频跑完 ComfyUI 还占着 19.6 GiB,whisper 显存不够。空闲时先让它卸载模型。"""
    tid = plan_align(store, sample["seg"])
    world["comfy"].vram_gb = 3.8
    out = _run(store, world, tid, FakeAligner(LINE))
    assert out.state == "done" and world["comfy"].freed == 1


def test_vram_still_short_after_freeing_fails_loudly(store, sample, world, monkeypatch):
    monkeypatch.setattr(aligner, "FREE_WAIT_SEC", 0)
    tid = plan_align(store, sample["seg"])
    world["comfy"].vram_gb = 3.8
    world["comfy"].free_releases = False                  # 占显存的不是 ComfyUI
    runner = FakeAligner(LINE)
    out = _run(store, world, tid, runner)
    assert out.state == "failed" and "释放后显存仍只有 3.8 GiB" in out.message and runner.calls == []
