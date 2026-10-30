# -*- coding: utf-8 -*-
"""台词音频任务的执行:假云机 + 假合成脚本 + 假转写(按次数换「听到的话」)。"""
import json

import pytest

from tools.drama import repo
from tools.gen.tts import plan_tts
from tools.serve import ttser
from tools.serve.fakes import FakeAligner, FakeComfy, FakeTts
from tools.serve.hosts import get_host

TWO = "林小雨说：「这块玉佩，是我母亲留给我的。」沈墨说：「我知道。」"
BOTH = "这块玉佩是我母亲留给我的我知道"


class Hearing(FakeAligner):
    """每次转写依次「听到」给定的几段话(最后一段之后一直重复它)。"""
    def __init__(self, *heard):
        super().__init__(heard[0])
        self.queue = list(heard)

    def __call__(self, args, stdin):
        self.heard = self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]
        return super().__call__(args, stdin)


@pytest.fixture
def world(store, sample, host_id, tmp_path):
    repo.bind_characters(store, sample["seg"], [sample["lin"], sample["shen"]])
    repo.update(store, "segment", sample["seg"], description=TWO)
    for cid, name in ((sample["lin"], "lin"), (sample["shen"], "shen")):
        (tmp_path / "v").mkdir(exist_ok=True)
        (tmp_path / f"v/{name}.wav").write_bytes(b"RIFF")
        repo.update(store, "character", cid, voice_ref_path=f"v/{name}.wav", voice_ref_text=f"{name}的试听句")
        store.conn.execute("INSERT INTO asset_approval (table_name, row_id, slot, path, sha256, created_at)"
                           " VALUES ('character', ?, 'voice', ?, 'x', 't')", (cid, f"v/{name}.wav"))
    return {"root": tmp_path, "host": get_host(store, host_id), "comfy": FakeComfy(), "tts": FakeTts()}


def _run(store, world, tid, asr):
    return ttser.run(store, world["comfy"], world["host"], repo.get(store, "task", tid), data_root=world["root"],
                     run_tts=world["tts"], run_align=asr)


def test_lines_are_cloned_listened_to_and_written_back(store, sample, world):
    tid = plan_tts(store, sample["seg"], seed=5)
    out = _run(store, world, tid, Hearing(BOTH))
    assert out.state == "done"
    rel = f"outputs/tasks/{tid}/lines.wav"
    assert repo.get(store, "segment", sample["seg"])["line_audio_path"] == rel
    check = ttser.check_of(world["root"], rel)
    assert check["ok"] and check["attempt"] == 1 and check["hits"] == [1.0, 1.0] and len(check["spans"]) == 2
    assert repo.get(store, "task", tid)["result_sec"] == check["sec"] > 0
    job = json.loads(world["tts"].calls[0][1])
    assert job["seed"] == 5 and job["voices"]["沈墨"]["text"] == "shen的试听句"
    assert job["voices"]["林小雨"]["audio"].startswith("/root/autodl-tmp/ComfyUI/input/ai-short-drama/")
    assert len(world["comfy"].uploaded) == 3          # 两个定稿音色 + 合成出的台词音频


def test_a_missed_line_retries_with_a_new_seed(store, sample, world):
    tid = plan_tts(store, sample["seg"], seed=5)
    out = _run(store, world, tid, Hearing("这块玉佩是我母亲留给我的", BOTH))
    assert out.state == "done"
    check = ttser.check_of(world["root"], f"outputs/tasks/{tid}/lines.wav")
    assert check["attempt"] == 2 and [a["seed"] for a in check["attempts"]] == [5, 1005]
    assert check["attempts"][0]["hits"][1] < 0.5


def test_gives_up_after_three_attempts_and_names_the_line(store, sample, world):
    tid = plan_tts(store, sample["seg"], seed=5)
    out = _run(store, world, tid, Hearing("这块玉佩是我母亲留给我的"))
    assert out.state == "failed" and "第 2 句「我知道。」" in out.message and len(world["tts"].calls) == 3
    assert repo.get(store, "segment", sample["seg"])["line_audio_path"] is None
    assert len(json.loads((world["root"] / f"outputs/tasks/{tid}/tts.json").read_text(encoding="utf-8"))["attempts"]) == 3


def test_existing_audio_is_kept(store, sample, world):
    repo.update(store, "segment", sample["seg"], line_audio_path="old.wav")
    tid = plan_tts(store, sample["seg"], seed=5)
    out = _run(store, world, tid, Hearing(BOTH))
    assert out.state == "done" and "已有" in out.message
    assert repo.get(store, "segment", sample["seg"])["line_audio_path"] == "old.wav"


def test_busy_comfyui_and_unreachable_ssh_wait(store, sample, world):
    tid = plan_tts(store, sample["seg"], seed=5)
    world["comfy"].running.append("someone-else")
    assert _run(store, world, tid, Hearing(BOTH)).state == "pending" and world["tts"].calls == []
    world["comfy"].running.clear()
    world["tts"] = lambda args, stdin: (255, "ssh: connect timed out")
    assert _run(store, world, tid, Hearing(BOTH)).state == "pending"
    assert repo.get(store, "task", tid)["state"] == "pending"
