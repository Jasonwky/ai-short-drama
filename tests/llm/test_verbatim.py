# -*- coding: utf-8 -*-
from tools.llm.verbatim import squash


def test_fullwidth_and_halfwidth_punctuation_match():
    assert squash("那天，下着雨！") == squash("那天,下着雨!")


def test_whitespace_is_ignored_and_folding_is_one_to_one():
    assert squash("第一段，\n  第二段") == "第一段,第二段"
    assert len(squash("还有…")) == 3                  # 「…」没有被展开成三个字符


def test_restore_returns_the_source_own_wording():
    """判卷按折叠后比对通过的抄写,写回时换回原文的写法(2026-10-10 真 LLM:模型把「，」抄成「,」,判卷放行,半角逗号却进了字幕)。"""
    from tools.llm.verbatim import restore
    src = "林小雨说：「这块玉佩，是我母亲留给我的。」\n沈墨说：「天快黑了，你还不进屋？」"
    assert restore("这块玉佩,是我母亲留给我的。", src) == "这块玉佩，是我母亲留给我的。"
    assert restore("天快黑了, 你还不进屋?", src) == "天快黑了，你还不进屋？"
    assert restore("这句原文里没有", src) is None
