# -*- coding: utf-8 -*-
import sys
from pathlib import Path

# 让测试能 `import tools.xxx`(tools 是命名空间包,仓库根目录要在 sys.path 上)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest


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
