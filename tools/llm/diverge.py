# -*- coding: utf-8 -*-
"""方案发散的共用纯函数(spec 2026-10-23-剧集脑爆系统-design.md 第 0 节)。

为什么(spike-results/2026-10-23-剧集脑爆系统调研.md):多次让 LLM 发散,结果会雷同(Echoes in AI);
调温度、换措辞救不了;有效的是**先定维度、再按维度强制分化**。所以维度由代码给闭集,LLM 只在闭集里取值,
这里的判卷保证方案两两真不一样;趋同度是**用数据审发散**,不是让 LLM 自评。
典型度(0..1,LLM 自报的相对量)只用来给最高的那个打「套路款」标签,不排序、不淘汰。
"""
from __future__ import annotations

from itertools import combinations


def distinct_problems(candidates: list[dict], dims: tuple[str, ...], min_diff: int = 2) -> list[str]:
    """任意两个方案在维度上至少有 min_diff 个取值不同;不够的每一对报一条。"""
    out = []
    for (i, a), (j, b) in combinations(enumerate(candidates, 1), 2):
        same = [d for d in dims if a[d] == b[d]]
        if len(dims) - len(same) < min_diff:
            out.append(f"方案 {i} 和 {j}:只在 {len(dims) - len(same)} 个维度上不同(相同的有 {'、'.join(same)}),"
                       f"要至少 {min_diff} 个维度不一样")
    return out


def overlap(candidates: list[dict], dims: tuple[str, ...]) -> float:
    """两两之间维度取值相同的比例,取平均;0 = 各不相同,1 = 完全一样。"""
    pairs = list(combinations(candidates, 2))
    if not pairs:
        return 0.0
    return sum(sum(a[d] == b[d] for d in dims) / len(dims) for a, b in pairs) / len(pairs)


def assemble(candidates: list[dict], picks: dict[str, int]) -> dict:
    """按人逐字段的选择拼出一份(纯代码,不再问 LLM)。picks:字段 → 方案下标。"""
    return {field: candidates[i][field] for field, i in picks.items()}


def routine_index(candidates: list[dict]) -> int | None:
    """典型度最高的那个(「套路款」标签);没有方案为 None。"""
    if not candidates:
        return None
    return max(range(len(candidates)), key=lambda i: candidates[i]["typicality"])
