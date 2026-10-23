# -*- coding: utf-8 -*-
import sys
from pathlib import Path

# 让测试能 `import tools.xxx`(tools 是命名空间包,仓库根目录要在 sys.path 上)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jinja2
import pytest


class _LoudUndefined(jinja2.Undefined):
    """模板把不存在的变量**输出**到页面上就报错(`{% if x %}` 这类判断照常放行)。
    P17b 自测抓到过:立项页表单写了 `{{ drama_id }}`(render 把它留给导航、没传进模板),渲染成空串,点生成 404。
    只在测试里开 —— 正式控制台遇到边角页面不该因此 500。"""
    def __str__(self):
        raise jinja2.UndefinedError(f"模板输出了不存在的变量「{self._undefined_name}」")


@pytest.fixture(autouse=True)
def _loud_templates(monkeypatch):
    from tools.serve.panels.common import TEMPLATES
    monkeypatch.setattr(TEMPLATES.env, "undefined", _LoudUndefined)


@pytest.fixture(autouse=True)
def _blank_text_reader(monkeypatch):
    """烧字幕检测的 OCR 默认换成「什么字都没看到」:单测不装 / 不跑 EasyOCR;要测命中的测试自己再换。"""
    from tools.cut import textscan

    class Blank:
        def read(self, frame):
            return []

        def read_column(self, frame, box):
            return ""
    monkeypatch.setattr(textscan, "make_reader", Blank)


@pytest.fixture(autouse=True)
def _no_blank_screen(monkeypatch):
    """白屏检测默认换成「没白屏」:别的测试不为它跑 ffmpeg;要测检测本身 / 命中的测试自己再换。"""
    from tools.cut import blankscan
    monkeypatch.setattr(blankscan, "detect", lambda path: [])
