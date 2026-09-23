# -*- coding: utf-8 -*-
import pytest

from tools.drama import rules
from tools.gen.capability import MINIMAX_H3

DESC = "【镜头1】林小雨低头看着玉佩。【镜头2】林小雨说：「这块玉佩，是我母亲留给我的。」"


def test_dialogue_chars_ignores_punctuation():
    # 这块玉佩(4) + 是我母亲留给我的(8)
    assert rules.dialogue_chars(DESC) == 12


def test_narration_counts_as_dialogue():
    d = "【镜头1】旁白：那一年，她十七岁。【镜头2】林小雨抬头。"
    assert rules.dialogue_chars(d) == 7


def test_dialogue_by_speaker():
    d = "【镜头1】林小雨说：「你来了。」【镜头2】沈墨说：「我来晚了。」【镜头3】林小雨说：「嗯。」"
    assert rules.dialogue_by_speaker(d) == {"林小雨": 4, "沈墨": 4}


def test_min_duration():
    assert rules.min_duration_sec(DESC) == pytest.approx(12 / 4.5 + 2)


def test_valid_segment_passes():
    assert rules.check_segment(DESC, 8.0, MINIMAX_H3) == []


def test_missing_duration_is_not_defaulted():
    assert rules.check_segment(DESC, None, MINIMAX_H3) == ["缺少时长 —— 不许用默认值填空"]


def test_too_short_reports_both_model_range_and_dialogue():
    problems = rules.check_segment(DESC, 4.0, MINIMAX_H3)
    assert len(problems) == 2
    assert "训练区间下界" in problems[0]
    assert "台词 12 字需 ≥4.7 秒,当前 4 秒" == problems[1]


def test_too_long_is_outside_trained_range():
    assert "训练区间上界" in rules.check_segment(DESC, 16.0, MINIMAX_H3)[0]


@pytest.mark.parametrize("n", [1, 5])
def test_subshot_count_bounds(n):
    d = "".join(f"【镜头{i}】画面。" for i in range(1, n + 1))
    assert rules.check_segment(d, 8.0, MINIMAX_H3) == [f"子镜头 {n} 个,应为 2–4 个(按【镜头N】计)"]


def test_expected_segment_range():
    # 1000 字 → 120 秒 → 10 段 → ±20% → 8..12
    assert rules.expected_segment_range("字" * 1000) == (8, 12)


def test_check_episode():
    assert rules.check_episode("字" * 1000, 10) == []
    assert rules.check_episode("字" * 1000, 15) == ["剧本 1000 字(约 120 秒),段落数应在 8–12,当前 15"]
