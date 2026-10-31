# -*- coding: utf-8 -*-
"""试听音任务的执行:假云机(上传、队列、显存)+ 假配音脚本 + 假转写脚本。"""
import json

import pytest

from tools.drama import repo
from tools.gen.voice import plan_voice
from tools.serve import voicer
from tools.serve.fakes import FakeAligner, FakeComfy, FakeVoice
from tools.serve.hosts import get_host

LINE = "这块玉佩，是我母亲留给我的。"


@pytest.fixture
def world(store, sample, host_id, tmp_path):
    return {"root": tmp_path, "host": get_host(store, host_id), "comfy": FakeComfy(), "tts": FakeVoice()}


def _run(store, world, tid, heard):
    asr = FakeAligner(heard)
    out = voicer.run(store, world["comfy"], world["host"], repo.get(store, "task", tid), data_root=world["root"],
                     run_tts=world["tts"], run_align=asr)
    return out, asr


def test_voice_is_saved_listened_to_and_written_back(store, sample, world):
    tid = plan_voice(store, sample["lin"], seed=3)
    out, asr = _run(store, world, tid, "这块玉佩是我母亲留给我的")
    t = repo.get(store, "task", tid)
    assert out.state == "done" and t["result_path"] == f"outputs/tasks/{tid}/voice.wav"
    assert (world["root"] / t["result_path"]).read_bytes()[:4] == b"RIFF"
    ch = repo.get(store, "character", sample["lin"])
    assert ch["voice_ref_path"] == t["result_path"] and ch["voice_ref_text"] == LINE
    # 配音脚本拿到的是 VoiceDesign 目录、声音描述、试听句、种子;回听的是传到 ComfyUI input 下的那个 wav
    assert world["tts"].calls[0][1:] == ["清亮女声", LINE, "3", "Chinese"]
    assert asr.calls[0][1].startswith("/root/autodl-tmp/ComfyUI/input/ai-short-drama/")
    check = voicer.check_of(world["root"], t["result_path"])
    assert check["ok"] is True and check["hit"] == 1.0


def test_misread_voice_is_kept_but_marked_not_ok(store, sample, world):
    tid = plan_voice(store, sample["lin"], seed=3)
    out, _ = _run(store, world, tid, "完全不对的一句话")
    assert out.state == "done"
    check = voicer.check_of(world["root"], repo.get(store, "task", tid)["result_path"])
    assert check["ok"] is False and check["heard"] == "完全不对的一句话"


def test_existing_voice_is_not_overwritten(store, sample, world):
    repo.update(store, "character", sample["lin"], voice_ref_path="old.wav", voice_ref_text="旧")
    tid = plan_voice(store, sample["lin"], seed=3)
    out, _ = _run(store, world, tid, LINE)
    assert out.state == "done" and "已有" in out.message
    assert repo.get(store, "character", sample["lin"])["voice_ref_path"] == "old.wav"


def test_busy_comfyui_waits_without_running_anything(store, sample, world):
    world["comfy"].pending.append("someone-else")
    tid = plan_voice(store, sample["lin"], seed=3)
    out, asr = _run(store, world, tid, LINE)
    assert out.state == "pending" and "队列" in out.message
    assert world["tts"].calls == [] and repo.get(store, "task", tid)["state"] == "pending"


@pytest.mark.parametrize("code,state", [(255, "pending"), (1, "failed")])
def test_ssh_unreachable_waits_but_script_errors_fail(store, sample, world, code, state):
    world["tts"] = lambda args, stdin: (code, "boom")
    tid = plan_voice(store, sample["lin"], seed=3)
    out, _ = _run(store, world, tid, LINE)
    assert out.state == state and repo.get(store, "task", tid)["state"] == state


def test_check_of_returns_none_for_uploaded_voices(tmp_path):
    (tmp_path / "v.wav").write_bytes(b"RIFF")
    assert voicer.check_of(tmp_path, "v.wav") is None and voicer.check_of(tmp_path, None) is None


@pytest.mark.parametrize("step", ["tts", "align"])
def test_cuda_oom_mid_run_waits_for_the_next_gap(store, sample, world, step):
    oom = lambda args, stdin: (3, "torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 20.00 MiB.")
    if step == "tts":
        world["tts"] = oom
    tid = plan_voice(store, sample["lin"], seed=3)
    out = voicer.run(store, world["comfy"], world["host"], repo.get(store, "task", tid), data_root=world["root"],
                     run_tts=world["tts"], run_align=oom if step == "align" else FakeAligner(LINE))
    assert out.state == "pending" and "显存" in out.message
    assert repo.get(store, "task", tid)["state"] == "pending"
