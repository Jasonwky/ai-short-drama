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

