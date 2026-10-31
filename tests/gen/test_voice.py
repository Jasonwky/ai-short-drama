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


def test_template_candidate_is_a_voice_task_with_its_own_workflow(store, sample):
    """念法模板候选(spec 2026-10-31-IndexTTS2港片配音-design.md §3):kind = voice、workflow = indextts2_template,
    用角色的定稿音色 + 情绪向量预设;不写回角色,人挑了才存成模板。"""
    from tools.gen import voice
    with pytest.raises(ValueError, match="定稿"):
        voice.plan_template(store, sample["lin"], preset="惊疑", seed=9)
    repo.update(store, "character", sample["lin"], voice_ref_path="v/lin.wav", voice_ref_text="试听句")
    store.conn.execute("INSERT INTO asset_approval (table_name, row_id, slot, path, sha256, created_at)"
                       " VALUES ('character', ?, 'voice', 'v/lin.wav', 'x', 't')", (sample["lin"],))
    t = repo.get(store, "task", voice.plan_template(store, sample["lin"], preset="惊疑", seed=9))
    p = json.loads(t["params_json"])
    assert (t["kind"], t["target_type"], t["model"], p["workflow"]) == ("voice", "character", "IndexTTS-2", "indextts2_template")
    assert p["emo_vector"] == voice.EMO_PRESETS["惊疑"] and p["preset"] == "惊疑"
    assert p["voice"] == {"path": "v/lin.wav", "sha256": "x"} and p["writes"] == "voice_template_candidate"
    with pytest.raises(ValueError, match="预设"):
        voice.plan_template(store, sample["lin"], preset="唱歌", seed=1)
