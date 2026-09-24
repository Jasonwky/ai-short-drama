# -*- coding: utf-8 -*-
from tools.llm.verbatim import squash


def test_fullwidth_and_halfwidth_punctuation_match():
    assert squash("那天，下着雨！") == squash("那天,下着雨!")


def test_whitespace_is_ignored_and_folding_is_one_to_one():
    assert squash("第一段，\n  第二段") == "第一段,第二段"
    assert len(squash("还有…")) == 3                  # 「…」没有被展开成三个字符
