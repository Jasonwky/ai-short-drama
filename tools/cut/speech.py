# -*- coding: utf-8 -*-
"""台词「念得对不对」的比对:按无声调拼音比,不按汉字比。

Whisper 对 TTS / 成片的转写常出同音字与繁体(「伙夫」→「霍夫」、「新来的」→「新來的」),按字比会把念对的判错
(spike-results/2026-10-29-台词与口型匹配方案调研.md 第 4.2 节:10 条里不满分的 4 条全是这种)。
"""
from __future__ import annotations

import difflib

from pypinyin import lazy_pinyin

from tools.cut.subtitles import PUNCT


def syllables(text: str) -> list[str]:
    clean = "".join(c for c in text if c not in PUNCT and not c.isspace())
    return [s.lower() for s in lazy_pinyin(clean)]


def hit(want: str, heard: str) -> float:
    """要求的台词有多少按拼音被听到(0..1,difflib 的匹配比)。要求为空时算 1。"""
    a, b = syllables(want), syllables(heard)
    if not a:
        return 1.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def line_hits(lines: list[str], heard: str) -> list[float]:
    """逐句命中:各句音节拼成一串与听到的对齐,每句里被对上的音节占比(0..1)。
    按整段对齐、不按时间切 —— 漏句能看出来,夹在句间的多余话不影响(多说由语音检查管)。"""
    seqs = [syllables(line) for line in lines]
    joined = [s for seq in seqs for s in seq]
    matched = [False] * len(joined)
    for a, _, size in difflib.SequenceMatcher(None, joined, syllables(heard), autojunk=False).get_matching_blocks():
        matched[a:a + size] = [True] * size
    out, start = [], 0
    for seq in seqs:
        out.append(1.0 if not seq else round(sum(matched[start:start + len(seq)]) / len(seq), 3))
        start += len(seq)
    return out
