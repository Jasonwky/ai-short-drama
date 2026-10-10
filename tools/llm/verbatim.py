# -*- coding: utf-8 -*-
"""「逐字」比对用的规整:去掉空白,并把全角 ASCII 区字符一对一折成半角。

2026-09-24 真 LLM 契约测试:原文用半角逗号「,」,模型照抄时自动写成全角「，」——
字都对,只是标点宽度不同,却被判「找不到原文」。分镜判卷核对台词时用它。
折叠是一个字符对一个字符(不用 NFKC:它会把「…」展开成三个字符)。
"""
from __future__ import annotations

# 全角 ！ 到 ～(U+FF01–FF5E)对应半角 ! 到 ~(U+0021–007E)
_FOLD = {c: c - 0xFEE0 for c in range(0xFF01, 0xFF5F)}


def squash(text: str) -> str:
    return "".join(ch for ch in text if not ch.isspace()).translate(_FOLD)


def restore(text: str, source: str) -> str | None:
    """text 折叠后在 source 里出现时,返回 source 里**原本的写法**(那一段原文);找不到返回 None。
    判卷按折叠后比对放行模型的抄写;写回时换回原文,不让模型改过的标点宽度进剧本和字幕。"""
    pos = [i for i, ch in enumerate(source) if not ch.isspace()]
    folded = "".join(source[i] for i in pos).translate(_FOLD)
    want = squash(text)
    at = folded.find(want) if want else -1
    if at < 0:
        return None
    return source[pos[at]:pos[at + len(want) - 1] + 1]
