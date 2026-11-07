# -*- coding: utf-8 -*-
"""小说章节入库前的三条判定(spec 1.1 第 4 条;阈值出处 spike-results/2026-11-07-小说平台免费章节实测.md):
私用区 ≤ 1 %(超了疑似字体加密)、没有截断提示、正文汉字 ≥ 自报字数的 80 %(有自报时)。另拦太短的(< 200 汉字)。"""
from __future__ import annotations

import re

from tools.refs.sources.base import Unit, hanzi

PUA_MAX = 0.01
WORDS_MIN_RATIO = 0.8
MIN_HANZI = 200
TRUNCATION = ("下载App", "下载APP", "开通会员", "订阅本章", "请至", "App阅读", "APP阅读", "购买本章", "免费畅读")


def chapter(u: Unit) -> tuple[str, str]:
    chars = len(re.sub(r"\s", "", u.text))
    pua = len(re.findall(r"[-]", u.text))
    if chars and pua / chars > PUA_MAX:
        return "rejected", f"疑似字体加密:私用区字符 {pua}/{chars}({pua / chars:.1%})"
    hit = next((m for m in TRUNCATION if m in u.text), None)
    if hit:
        return "rejected", f"有截断提示「{hit}」—— 不是完整的免费章"
    n = hanzi(u.text)
    if n < MIN_HANZI:
        return "rejected", f"正文太短({n} 个汉字)"
    if u.self_words and n < WORDS_MIN_RATIO * u.self_words:
        return "rejected", f"字数对不上:正文 {n} 个汉字,页面自报 {u.self_words} 字"
    return "ok", ""
