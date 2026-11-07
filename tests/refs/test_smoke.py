# -*- coding: utf-8 -*-
from tools.refs import net, smoke
from tools.refs.sources.base import AdapterError, RankItem


def test_overall_state():
    assert smoke.overall({"a": "PASS", "b": "UNAVAILABLE"}) == "PASS"
    assert smoke.overall({"a": "FAIL", "b": "PASS"}) == "FAIL"
    assert smoke.overall({"a": "UNAVAILABLE", "b": "UNAVAILABLE"}) == "SKIP"


def test_check_one_classifies(tmp_path):
    ok = lambda f, b: [RankItem("1", "甲", 1, None, {})]  # noqa: E731
    def bad(f, b):
        raise AdapterError("0 条")
    def down(f, b):
        raise net.FetchError("blocked", "412")
    assert smoke.check_one(ok, "b", None)["state"] == "PASS"
    assert smoke.check_one(bad, "b", None)["state"] == "FAIL"
    def moved(f, b):
        raise net.FetchError("http", "HTTP 404", 404)
    assert smoke.check_one(down, "b", None)["state"] == "UNAVAILABLE"
    assert smoke.check_one(moved, "b", None)["state"] == "FAIL"           # 榜单地址变了 = 适配器坏了
