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


def test_template_candidate_runs_on_indextts_and_records_prosody(store, sample, world):
    """候选只落盘 + 量韵律(prosody.json),不写回角色、不回听;脚本是 idx_remote。"""
    from tools.gen import voice
    from tools.serve.fakes import FakeTts
    (world["root"] / "v").mkdir(exist_ok=True)
    (world["root"] / "v/lin.wav").write_bytes(b"RIFF")
    repo.update(store, "character", sample["lin"], voice_ref_path="v/lin.wav", voice_ref_text="试听句")
    store.conn.execute("INSERT INTO asset_approval (table_name, row_id, slot, path, sha256, created_at)"
                       " VALUES ('character', ?, 'voice', 'v/lin.wav', 'x', 't')", (sample["lin"],))
    tid = voice.plan_template(store, sample["lin"], preset="喝令", seed=3)
    tts = FakeTts()
    out = voicer.run(store, world["comfy"], world["host"], repo.get(store, "task", tid), data_root=world["root"],
                     run_tts=tts, run_align=lambda a, s: (_ for _ in ()).throw(AssertionError("候选不回听")))
    t = repo.get(store, "task", tid)
    assert out.state == "done" and t["result_path"] == f"outputs/tasks/{tid}/template.wav"
    job = json.loads(tts.calls[0][1])
    assert tts.calls[0][0].endswith("/indextts/checkpoints") and job["emo_vector"] == voice.EMO_PRESETS["喝令"]
    report = json.loads((world["root"] / f"outputs/tasks/{tid}/prosody.json").read_text(encoding="utf-8"))
    assert set(report) == {"occupancy", "range_st", "problems"} and report["problems"]     # 假脚本出的是静音:不合格
    assert repo.get(store, "character", sample["lin"])["voice_ref_path"] == "v/lin.wav"
