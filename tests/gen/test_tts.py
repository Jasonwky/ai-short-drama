# -*- coding: utf-8 -*-
"""段落台词音频的规划:说话人要有定稿音色;参数定死台词与音色快照;台词 / 音色变了算过期。"""
import json

import pytest

from tools.drama import repo
from tools.drama.tasks import set_task_state
from tools.gen import tts

TWO = "【镜头1】林小雨说：「这块玉佩，是我母亲留给我的。」沈墨说：「我知道。」"


def _voice(store, cid, path, text="试听句"):
    repo.update(store, "character", cid, voice_ref_path=path, voice_ref_text=text)
    with store.write() as c:
        c.execute("INSERT INTO asset_approval (table_name, row_id, slot, path, sha256, created_at)"
                  " VALUES ('character', ?, 'voice', ?, ?, 't')", (cid, path, f"sha-{path}"))


@pytest.fixture
def two(store, sample):
    repo.bind_characters(store, sample["seg"], [sample["lin"], sample["shen"]])
    repo.update(store, "segment", sample["seg"], description=TWO)
    _voice(store, sample["lin"], "v/lin.wav", "林的试听句")
    _voice(store, sample["shen"], "v/shen.wav", "沈的试听句")
    return sample


def test_plan_freezes_lines_and_approved_voices(store, two):
    tid = tts.plan_tts(store, two["seg"], seed=9)
    t = repo.get(store, "task", tid)
    p = json.loads(t["params_json"])
    assert (t["kind"], t["target_type"], t["seed"]) == ("tts", "segment", 9)
    assert p["lines"] == [["林小雨", "这块玉佩，是我母亲留给我的。"], ["沈墨", "我知道。"]]
    assert p["voices"]["沈墨"] == {"path": "v/shen.wav", "sha256": "sha-v/shen.wav", "ref_text": "沈的试听句"}
    assert p["writes"] == "line_audio_path" and p["expect_ext"] == ".wav"
    assert json.loads(t["refs_json"]) == ["v/lin.wav", "v/shen.wav"]


def test_speaker_without_an_approved_voice_is_named(store, two):
    store.conn.execute("DELETE FROM asset_approval WHERE row_id = ? AND slot = 'voice'", (two["shen"],))
    assert any("沈墨" in p and "定稿" in p for p in tts.tts_problems(store, two["seg"]))
    with pytest.raises(ValueError, match="沈墨"):
        tts.plan_tts(store, two["seg"], seed=1)


def test_unknown_speaker_and_silent_segment_are_refused(store, two):
    repo.update(store, "segment", two["seg"], description="路人说：「让一让。」")
    assert any("路人" in p for p in tts.tts_problems(store, two["seg"]))
    repo.update(store, "segment", two["seg"], description="林小雨低头不语。")
    assert any("没有台词" in p for p in tts.tts_problems(store, two["seg"]))


def _done(store, tid, path):
    set_task_state(store, tid, "queued", external_id="x")
    set_task_state(store, tid, "done", result_path=path)
    repo.update(store, "segment", repo.get(store, "task", tid)["target_id"], line_audio_path=path)


def test_stale_when_lines_or_voices_change(store, two):
    _done(store, tts.plan_tts(store, two["seg"], seed=1), "outputs/tasks/50/lines.wav")
    seg = lambda: repo.get(store, "segment", two["seg"])   # noqa: E731
    assert tts.stale(store, seg()) == []
    repo.update(store, "segment", two["seg"], description=TWO.replace("我知道。", "我早就知道。"))
    assert any("台词" in r for r in tts.stale(store, seg()))
    repo.update(store, "segment", two["seg"], description=TWO)
    _voice(store, two["shen"], "v/shen2.wav")
    assert any("沈墨" in r and "音色" in r for r in tts.stale(store, seg()))


def test_audio_not_made_by_a_tts_task_is_stale(store, two):
    repo.update(store, "segment", two["seg"], line_audio_path="up/hand.wav")
    assert tts.stale(store, repo.get(store, "segment", two["seg"])) == ["当前台词音频不是由台词音频任务生成的,重新合成"]
    repo.update(store, "segment", two["seg"], line_audio_path=None)
    assert tts.stale(store, repo.get(store, "segment", two["seg"])) == []
