# -*- coding: utf-8 -*-
import json

import pytest

from tools.drama import repo
from tools.gen.align import align_problems, plan_align
from tools.gen.capability import WHISPER_ALIGN


def test_align_task_freezes_video_and_lines(store, sample):
    tid = plan_align(store, sample["seg"])
    t = repo.get(store, "task", tid)
    p = json.loads(t["params_json"])
    assert (t["kind"], t["model"], t["seed"]) == ("align", WHISPER_ALIGN.name, 0)
    assert p == {"workflow": "whisper_align", "video": "videos/s1.mp4", "lines": ["这块玉佩，是我母亲留给我的。"],
                 "writes": "subtitle_path", "expect_ext": ".srt"}
    assert json.loads(t["refs_json"]) == ["videos/s1.mp4"]


def test_no_video_cannot_be_aligned(store, sample):
    repo.update(store, "segment", sample["seg"], video_path=None)
    assert align_problems(store, sample["seg"]) == ["还没有视频"]
    with pytest.raises(ValueError, match="还没有视频"):
        plan_align(store, sample["seg"])


def test_no_dialogue_is_planned_as_a_speech_check(store, sample):
    """spec 2026-10-28 第 3 节:没台词的段落也要查 —— 第 1 集第 2 段没台词却在说话。"""
    repo.update(store, "segment", sample["seg"], description="【镜头1】林小雨低头看着玉佩。")
    p = json.loads(repo.get(store, "task", plan_align(store, sample["seg"]))["params_json"])
    assert (p["lines"], p["writes"], p["expect_ext"]) == ([], "speech_check", ".json")
