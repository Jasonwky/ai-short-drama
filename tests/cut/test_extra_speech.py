# -*- coding: utf-8 -*-
"""台词外多说:用字幕对齐已有的 whisper 逐字结果判(设计 specs/2026-10-10-台词外多说提示-design.md §一)。"""
import json
from pathlib import Path

import pytest

from tools.cut import subtitles

REAL = json.loads((Path(__file__).parent / "fixtures/align_real.json").read_text(encoding="utf-8"))["runs"]
LOUD = [100.0] * 80                  # 8 秒全程有声(0.1 秒一窗)


def _chars(text, start=0.0, step=0.2):
    return [[c, round(start + i * step, 2), round(start + (i + 1) * step, 2)] for i, c in enumerate(text)]


def _run(r):
    return subtitles.extra_speech(r["lines"], [tuple(c) for c in r["chars"]], r["envelope"])


def test_real_clip_that_read_the_stage_direction():
    """2026-09-29 那条:whisper 听到「林用普通话说这块玉佩……」,能量在 3.7–4.7 秒有一段真的说话。"""
    runs, problem = _run(REAL["2026-09-29"])
    assert problem == "" and [(r["text"], r["start"], r["end"]) for r in runs] == [("林用普通话说", 0.0, 4.76)]


@pytest.mark.parametrize("day", ["2026-10-09", "2026-10-10"])
def test_real_clips_that_only_said_the_line(day):
    assert _run(REAL[day]) == ([], "")


def test_after_and_between_lines():
    lines = ["这块玉佩", "我会带着它"]
    runs, _ = subtitles.extra_speech(lines, _chars("这块玉佩沈墨说话了我会带着它谢谢大家收看"), LOUD)
    assert [r["text"] for r in runs] == ["沈墨说话了", "谢谢大家收看"]


def test_short_runs_and_punctuation_are_ignored():
    runs, _ = subtitles.extra_speech(["这块玉佩"], _chars("嗯，这块玉佩，啊。"), LOUD)
    assert runs == []                                   # 「嗯，」「，啊。」去掉标点都不足 3 字


def test_words_made_up_over_silence_are_not_speech():
    """whisper 会在静音尾巴上编出「字幕由……提供」:能量到不了开口门槛,不算多说。"""
    env = [100.0] * 20 + [1.0] * 60                     # 前 2 秒有声,之后静音
    chars = _chars("这块玉佩", 0.0, 0.4) + _chars("字幕由某某提供", 3.0, 0.3)
    assert subtitles.extra_speech(["这块玉佩"], chars, env) == ([], "")


def test_untrustworthy_alignment_is_not_judged():
    """有一句台词没识别出来(低于 70%):所有识别字都会被当成多说 —— 不硬猜。"""
    runs, problem = subtitles.extra_speech(["这块玉佩是我母亲留给我的"], _chars("今天天气真不错啊"), LOUD)
    assert runs == [] and problem == "识别与台词对不上,无法判断是否多说"


def test_misheard_first_character_still_belongs_to_the_line():
    runs, _ = subtitles.extra_speech(["这块玉佩是我母亲留给我的"], _chars("哲块玉佩是我母亲留给我的"), LOUD)
    assert runs == []
