# -*- coding: utf-8 -*-
from tools.refs import judge
from tools.refs.sources import ADAPTERS
from tools.refs.sources.base import Unit

BODY = "青州叶家少年看着眼前主事平静说道" * 30          # 480 汉字


def test_every_source_except_manual_has_an_adapter():
    assert set(ADAPTERS) == {"hongguo", "jjwxc", "k17", "zongheng", "qimao", "fanqie"}


def test_clean_chapter_passes():
    assert judge.chapter(Unit(1, "第一章", BODY, None)) == ("ok", "")


def test_private_use_over_one_percent_is_rejected():
    v, why = judge.chapter(Unit(1, "t", BODY + "" * 10, None))
    assert v == "rejected" and "私用区" in why


def test_truncation_marker_is_rejected():
    v, why = judge.chapter(Unit(1, "t", BODY + "请至纵横小说App阅读最新章节", None))
    assert v == "rejected" and "截断" in why


def test_text_shorter_than_80_percent_of_self_report_is_rejected():
    assert judge.chapter(Unit(1, "t", BODY, 480))[0] == "ok"
    v, why = judge.chapter(Unit(1, "t", BODY, 1000))
    assert v == "rejected" and "1000" in why


def test_too_short_is_rejected():
    assert judge.chapter(Unit(1, "t", "太短了", None))[0] == "rejected"
