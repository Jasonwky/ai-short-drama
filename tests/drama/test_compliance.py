# -*- coding: utf-8 -*-
"""合规敏感词(广电总局「霸总」「爽而有度」管理提示,新闻转述)。"""
from tools.drama import compliance


def test_flagged_words_name_their_source():
    (p,) = compliance.problems("霸总的契约新娘")
    assert "霸总" in p and "管理提示" in p
    assert compliance.problems("替嫁冲喜:王妃她医术通天") == []


def test_every_word_has_a_source():
    for word, source in compliance.WORDS.items():
        assert word and source
