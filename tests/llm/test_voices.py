# -*- coding: utf-8 -*-
import pytest

from tools.drama import repo
from tools.llm import voices
from tools.llm.gate import schema_errors, strict_schema_problems

OK = {"gender": "male", "age": "middle_aged", "timbre": "低沉沙哑", "pace": "fast"}


def test_schema_is_strict_and_enums_closed():
    assert strict_schema_problems(voices.SCHEMA) == [] and schema_errors(OK, voices.SCHEMA) == []
    assert schema_errors({**OK, "age": "30岁"}, voices.SCHEMA)


def test_sentence_is_composed_by_code_in_a_fixed_shape():
    assert voices.compose(OK) == "中年男声,低沉沙哑,语速偏快"


def test_judge_keeps_numbers_and_real_people_out():
    judge = voices.judge_factory({})
    assert judge(OK) == []
    assert any("数字" in p for p in judge({**OK, "timbre": "低沉3分"}))
    assert any("像某某" in p for p in judge({**OK, "timbre": "像某位歌手"}))
    assert any("2–12" in p for p in judge({**OK, "timbre": "低"}))


def test_context_collects_the_characters_own_lines(store, sample):
    repo.update(store, "episode", sample["episode"], script="林小雨说：「你来了。」沈墨说：「嗯。」林小雨说：「坐吧。」")
    ctx = voices.context(store, sample["lin"])
    assert ctx["lines"] == ["你来了。", "坐吧。"] and ctx["name"] == "林小雨"
    assert "你来了" in voices.user_message(ctx)


def test_no_basis_no_call(store, sample):
    with pytest.raises(ValueError, match="没有依据"):
        voices.context(store, sample["shen"])
