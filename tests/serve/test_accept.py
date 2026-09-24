# -*- coding: utf-8 -*-
"""验收入口只测判决逻辑与参数闸门 —— 在 pytest 里真跑三层会递归调用 pytest。"""
from tools.serve.accept import main, verdict


def test_skip_is_never_a_pass():
    assert verdict([{"state": "PASS"}, {"state": "PASS"}, {"state": "SKIP"}]) == "PARTIAL"
    assert verdict([{"state": "PASS"}, {"state": "FAIL"}, {"state": "SKIP"}]) == "FAIL"
    assert verdict([{"state": "PASS"}] * 3) == "PASS"


def test_headless_is_refused(capsys):
    assert main(["--headless"]) == 2
    assert "拒绝无头模式" in capsys.readouterr().err
