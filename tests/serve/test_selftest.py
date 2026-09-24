# -*- coding: utf-8 -*-
"""Playwright 自测本身要开浏览器,不进 pytest;这里只守住它依赖的前提。"""
import pytest

from tools.drama.db import Store
from tools.drama.readiness import segment_readiness
from tools.gen.capability import MINIMAX_H3, QWEN_IMAGE_EDIT_2511
from tools.serve.panels import REGISTRY
from tools.serve.selftest import KEY_SELECTOR, main, seed_demo


def test_every_registered_panel_has_a_render_check():
    # 新面板登记进 REGISTRY 却忘了给判据 → 自测会 KeyError;这里提前拦住
    assert {p.key for p in REGISTRY} == set(KEY_SELECTOR)


def test_demo_segment_only_lacks_its_first_frame(tmp_path):
    """演示段落除了「首帧还没出」之外应当全部就绪 —— 否则点「生成首帧」会被拒绝,自测测不到链路。"""
    store = Store(tmp_path / "d.db")
    ids = seed_demo(store, tmp_path)
    checks = segment_readiness(store, ids["segment"], video_cap=MINIMAX_H3, image_cap=QWEN_IMAGE_EDIT_2511,
                               host_ok=True, host_msg="", model_ok=True, model_msg="")
    assert [c.key for c in checks if not c.ok] == ["refs_ready"]
    checks = segment_readiness(store, ids["segment2"], video_cap=MINIMAX_H3, image_cap=QWEN_IMAGE_EDIT_2511,
                               host_ok=True, host_msg="", model_ok=True, model_msg="")
    assert [c.key for c in checks if not c.ok] == ["refs_ready"]      # 第 2 段的首帧由尾帧衔接补上
    store.close()


def test_there_is_no_headless_switch():
    # Playwright 测试一律有头(CLAUDE.md 硬约束)—— 开关本身就不存在,传了直接报参数错误
    with pytest.raises(SystemExit):
        main(["--headless"])
