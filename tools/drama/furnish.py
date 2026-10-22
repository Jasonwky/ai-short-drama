# -*- coding: utf-8 -*-
"""场景陈设表:场景资产记用途与固定陈设,首帧提示词里出现的陈设要在清单上(spec 2026-10-22-连续性保障-design.md 第 3 项)。

起因:题甲第 1 集剧本把「床边昏睡的王爷」写在正堂,出图正堂正中摆床。今天先加了「厅堂里写到床」的编剧判卷(点修),
这里把它通用化:不再逐条加「X 不该在 Y」,而是看场景自己登记了什么。
只认字面:LEXICON 里的词出现在提示词里,而场景填了陈设、清单(子串)里没有它,就报。场景没填陈设 = 不查。
"""
from __future__ import annotations

PURPOSES = ("卧房", "厅堂", "书房", "庭院", "街市", "野外", "其他")
# 通用陈设词;按长度从长到短匹配,「床榻」先于「床」,命中的部分不再参与较短词的匹配
LEXICON = ("梳妆台", "博古架", "八仙桌", "太师椅", "书案", "书架", "案几", "屏风", "香炉", "灶台", "秋千", "龙椅",
           "宝座", "浴桶", "马车", "轿子", "床", "榻", "桌", "椅", "柜", "井")


def problems(prompt: str, furnishings: str) -> list[str]:
    if not furnishings.strip():
        return []
    rest, found = prompt, []
    for word in sorted(LEXICON, key=len, reverse=True):
        if word in rest:
            found.append((rest.index(word), word))
            rest = rest.replace(word, "\0" * len(word))      # 等长占位:后面较短词的位置仍对得上原文
    return [f"「{w}」不在场景的固定陈设里({furnishings.strip()})" for _, w in sorted(found) if w not in furnishings]
