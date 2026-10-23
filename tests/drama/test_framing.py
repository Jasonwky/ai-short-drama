# -*- coding: utf-8 -*-
"""竖屏可拍性(spec 2026-10-23-不穿帮验收-design.md 第 2 节)。例子取自题甲第 6 段。"""
from tools.drama import framing


def _e(character, pose="站"):
    return {"character": character, "pose": pose, "facing": "镜头", "side": "中", "holding": "无"}


LYING_PAIR = [_e("萧珩", "仰躺"), _e("沈青禾")]


def test_lying_with_another_person_in_vertical_frame_needs_a_close_shot():
    # 10-23 对照:全景 4/4 人出床,床尾纵深 2/2 出床,近景 2/2 合理
    for shot in ("全景", "中景", ""):
        (msg,) = framing.problems("9:16", LYING_PAIR, shot)
        assert "萧珩" in msg and "近景" in msg
    assert framing.problems("9:16", LYING_PAIR, "近景") == []
    assert framing.problems("9:16", LYING_PAIR, "特写") == []


def test_rule_only_applies_to_vertical_frames_with_two_or_more_people():
    assert framing.problems("16:9", LYING_PAIR, "全景") == []
    assert framing.problems("9:16", [_e("萧珩", "仰躺")], "全景") == []
    assert framing.problems("9:16", [_e("萧珩"), _e("沈青禾")], "全景") == []
    assert framing.problems("9:16", [_e("李福", "趴"), _e("沈青禾")], "全景") != []


def test_three_people_in_vertical_frame_is_only_a_hint():
    three = [_e("甲"), _e("乙"), _e("丙")]
    (hint,) = framing.hints("9:16", three)
    assert "3 人" in hint
    assert framing.hints("9:16", three[:2]) == [] and framing.hints("16:9", three) == []


def test_close_shot_phrase_is_written_by_code():
    assert framing.phrase("9:16", LYING_PAIR, "近景") == "近景,画面只拍到躺着的人胸口以上,站着的人在画面上方"
    assert framing.phrase("9:16", LYING_PAIR, "全景") == ""
    assert framing.phrase("16:9", LYING_PAIR, "近景") == ""
