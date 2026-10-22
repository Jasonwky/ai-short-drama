# -*- coding: utf-8 -*-
"""视觉抽检的确定性部分:上下文、schema、判卷、和场记表比对。LLM 行为由 test_contract_real.py 真跑。"""
import json

import pytest

from tests.cut.conftest import done_task
from tools.drama import repo
from tools.llm import vision_check as vc
from tools.llm.gate import strict_schema_problems
from tools.serve.fakes import sample_mp4


def _entry(character="林小雨", **kw):
    return {"character": character, "pose": "跪", "facing": "镜头", "side": "中", "holding": "无", **kw}


@pytest.fixture
def ready(store, sample, tmp_path):
    repo.update(store, "segment", sample["seg"], video_path=None,
                blocking_json=json.dumps([_entry()], ensure_ascii=False))
    done_task(store, tmp_path, sample["seg"], "video", "outputs/tasks/v1/clip.mp4", sample_mp4())
    return tmp_path


def test_context_composites_frames_and_marks_a_check_number(store, sample, ready):
    ctx = vc.context(store, sample["seg"], ready)
    assert ctx["labels"][0] == "首帧" and ctx["characters"] == ["林小雨"]
    assert ctx["image"][:4] == b"\x89PNG" and 100 <= ctx["check_number"] <= 999
    assert str(ctx["check_number"]) not in vc.user_message(ctx)          # 数字只在图上
    assert strict_schema_problems(vc.schema(ctx)) == []


def test_context_refuses_without_blocking_or_with_a_replaced_video(store, sample, ready):
    with pytest.raises(ValueError, match="视频已经换了"):
        vc.context(store, sample["seg"], ready, video_sha256="0" * 64)
    repo.update(store, "segment", sample["seg"], blocking_json="")
    with pytest.raises(ValueError, match="还没有场记表"):
        vc.context(store, sample["seg"], ready)


def test_judge_needs_the_check_number_and_every_person_in_every_frame():
    ctx = {"labels": ["首帧", "切镜后 2.00 秒"], "characters": ["甲", "乙"], "check_number": 123}
    p = lambda c: {"character": c, "visible": "是", "pose": "站", "facing": "镜头"}     # noqa: E731
    good = {"check_number": "123", "frames": [{"index": 1, "people": [p("甲"), p("乙")]},
                                              {"index": 2, "people": [p("乙"), p("甲")]}]}
    assert vc.judge_factory(ctx)(good) == []
    bad = {"check_number": "000", "frames": [{"index": 1, "people": [p("甲")]}]}
    probs = vc.judge_factory(ctx)(bad)
    assert any("check_number" in x for x in probs) and any("第 1–2 帧" in x for x in probs)
    assert any("第 1 帧要把名单里每个人" in x for x in probs)


def test_mismatches_compare_only_the_first_frame_and_skip_unknowns():
    # 实战第 7 段:场记表写「沈玉瑶跪着面朝萧珩」,出图她背对着跪
    expected = [_entry("沈玉瑶", facing="萧珩"), _entry("萧珩", pose="坐", facing="沈玉瑶")]
    out = {"frames": [
        {"index": 1, "people": [{"character": "沈玉瑶", "visible": "是", "pose": "跪", "facing": "背对镜头"},
                                {"character": "萧珩", "visible": "是", "pose": "看不出", "facing": "沈玉瑶"}]},
        {"index": 2, "people": [{"character": "沈玉瑶", "visible": "否", "pose": "看不出", "facing": "看不出"},
                                {"character": "萧珩", "visible": "是", "pose": "站", "facing": "镜头"}]}]}
    assert vc.mismatches(expected, out) == ["「沈玉瑶」应当面朝萧珩,模型看到的是背对镜头"]
