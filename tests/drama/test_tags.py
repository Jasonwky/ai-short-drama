# -*- coding: utf-8 -*-
import pytest

from tools.drama.refs import Ref
from tools.drama.tags import check_tags, expand

LIN, YARD, JADE = Ref("character", "林小雨", "l"), Ref("scene", "老宅庭院", "y"), Ref("prop", "玉佩", "j")


def test_expand_qwen_and_degrade_dropped_to_text():
    out = expand("@林小雨 拿着 @玉佩 站在 @老宅庭院", [LIN, YARD], [JADE], "qwen")
    assert out == "Picture 1(林小雨) 拿着 玉佩 站在 Picture 2(老宅庭院)"


def test_expand_h3_dialect():
    assert expand("@林小雨 说话", [LIN], [], "h3") == "<Picture 1>(林小雨) 说话"


def test_expand_refuses_unbound_mention():
    with pytest.raises(ValueError, match="@王五"):
        expand("@王五 走来", [LIN], [], "h3")


def test_expanded_prompt_passes_tag_check():
    out = expand("@林小雨 在 @老宅庭院", [LIN, YARD], [], "qwen")
    assert check_tags(out, n_images=2, dialect="qwen") == []


def test_out_of_range_image_reported():
    assert check_tags("<Picture 3> 走来", n_images=2) == [
        "提示词引用了第 3 张参考图,但只接入了 2 张",
        "接了 2 张参考图,但提示词从未引用第 1 张 —— 多图必须逐一指代",
        "接了 2 张参考图,但提示词从未引用第 2 张 —— 多图必须逐一指代",
    ]


def test_single_image_needs_no_textual_reference():
    # 多视角 LoRA 的格式 `<sks> front view …` 不提 Picture —— 单图没有歧义
    assert check_tags("<sks> front view eye-level shot wide shot", n_images=1, dialect="qwen") == []


def test_the_real_bug_from_the_cloud_box_is_caught():
    prompt = "Use <Picture 2> and <Picture 1> as reference frames and <Audio 1> exactly as it is"
    assert check_tags(prompt, n_images=2, n_audios=0) == [
        "提示词引用了 <Audio 1>,但只接入了 0 段参考音频"]


def test_attached_audio_must_be_referenced():
    assert check_tags("<Picture 1> 说话", n_images=1, n_audios=1) == [
        "接了参考音频,但提示词从未引用 <Audio 1> —— 模型不知道这段音频做什么用"]


def test_qwen_dialect_does_not_read_h3_tags():
    assert check_tags("<Picture 1>", n_images=1, dialect="qwen") == []
