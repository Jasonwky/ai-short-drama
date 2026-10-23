# -*- coding: utf-8 -*-
"""合规敏感词:片名、卖点里出现就打回(spec 2026-10-23-剧集脑爆系统-design.md 第 1 节)。

出处:广电总局 2024-11「霸总」类微短剧管理提示、2025-03「爽而有度」管理提示 —— 均为**新闻转述**
(新华网 2024-11-27 等,见 spike-results/2026-10-23-剧集脑爆系统调研.md 第 3 节),总局原文没核;
所以词表只收转述里明确点名的说法,宁少勿多。改词表要写出处。
"""
from __future__ import annotations

_BAZONG = "广电总局 2024-11「霸总」类微短剧管理提示(新闻转述):不以「霸总」一类字眼作片名"
_SHUANG = "广电总局 2025-03「爽而有度」管理提示(新闻转述):不靠拜金、炫富、一夜暴富制造爽点"
WORDS = {
    "霸总": _BAZONG,
    "霸道总裁": _BAZONG,
    "拜金": _SHUANG,
    "炫富": _SHUANG,
    "一夜暴富": _SHUANG,
}


def problems(text: str) -> list[str]:
    return [f"出现「{w}」—— {src}" for w, src in WORDS.items() if w in text]
