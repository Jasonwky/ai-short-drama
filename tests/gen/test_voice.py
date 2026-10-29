# -*- coding: utf-8 -*-
"""角色试听音的规划:试听句怎么选、参数定死。"""
import json

import pytest

from tools.drama import repo
from tools.gen.voice import FALLBACK_TEXT, plan_voice, sample_text


def test_sample_text_is_the_characters_longest_line(store, sample):
    repo.update(store, "segment", sample["seg"],
                description="林小雨说：「好。」沈墨说：「你听我解释,这不是你想的那样。」林小雨说：「这块玉佩，是我母亲留给我的。」")
    assert sample_text(store, sample["lin"]) == "这块玉佩，是我母亲留给我的。"
    assert sample_text(store, sample["shen"]) == "你听我解释,这不是你想的那样。"


def test_sample_text_falls_back_when_the_character_never_speaks(store, sample):
    assert sample_text(store, sample["shen"]) == FALLBACK_TEXT


def test_overlong_lines_are_not_used_as_the_sample(store, sample):
    repo.update(store, "segment", sample["seg"], description="林小雨说：「" + "很" * 41 + "长。」")
    assert sample_text(store, sample["lin"]) == FALLBACK_TEXT


def test_plan_voice_freezes_description_and_text(store, sample):
    tid = plan_voice(store, sample["lin"], seed=7)
    t = repo.get(store, "task", tid)
    p = json.loads(t["params_json"])
    assert (t["kind"], t["target_type"], t["target_id"], t["seed"]) == ("voice", "character", sample["lin"], 7)
    assert t["prompt"] == p["instruct"] == "清亮女声" and p["text"] == "这块玉佩，是我母亲留给我的。"
    assert p["writes"] == "voice_ref_path" and p["expect_ext"] == ".wav" and p["language"] == "Chinese"


def test_plan_voice_needs_a_description(store, sample):
    with pytest.raises(ValueError, match="声音描述"):
        plan_voice(store, sample["shen"], seed=1)
