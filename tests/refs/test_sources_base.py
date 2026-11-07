# -*- coding: utf-8 -*-
import pytest

from tools.refs.sources.base import AdapterError, RankItem, check_ranks, hanzi, num


@pytest.mark.parametrize("text,value", [("8339万热度", 83390000), ("314.3万收藏", 3143000), ("1.2亿", 120000000),
                                        ("22,274,893,824", 22274893824), ("15251798", 15251798), ("", None), ("无", None)])
def test_num(text, value):
    assert num(text) == value


def test_hanzi_counts_only_cjk():
    assert hanzi("第1章 红杏墙。abc") == 5


def test_empty_ranks_is_a_failure_not_an_empty_day():
    with pytest.raises(AdapterError, match="0 条"):
        check_ranks([])


def test_ranks_need_ids_titles_and_unique_positions():
    ok = [RankItem("1", "甲", 1, 10.0, {}), RankItem("2", "乙", 2, None, {})]
    assert check_ranks(ok) == ok
    with pytest.raises(AdapterError, match="缺"):
        check_ranks([RankItem("", "甲", 1, None, {})])
    with pytest.raises(AdapterError, match="名次重复"):
        check_ranks([RankItem("1", "甲", 1, None, {}), RankItem("2", "乙", 1, None, {})])


def test_json_after_decodes_value_after_marker():
    from tools.refs.sources.base import json_after
    assert json_after('<script>var X = {"a": [1, 2]};</script>', "X = ") == {"a": [1, 2]}


@pytest.mark.parametrize("page", ["<html></html>", "X = {bad", "X = "])
def test_json_after_missing_or_broken_is_adapter_error(page):
    from tools.refs.sources.base import json_after
    with pytest.raises(AdapterError, match="X = "):
        json_after(page, "X = ")


def test_field_walks_keys_and_names_the_missing_one():
    from tools.refs.sources.base import field
    d = {"a": {"b": {"c": 3}}, "n": None}
    assert field(d, "a", "b", "c") == 3
    with pytest.raises(AdapterError, match="缺键 a.x"):
        field(d, "a", "x")
    with pytest.raises(AdapterError, match="缺键 n"):
        field(d, "n")
    with pytest.raises(AdapterError, match="缺键 a.b.c.d"):
        field(d, "a", "b", "c", "d")
    with pytest.raises(AdapterError):
        field([1], "a")
