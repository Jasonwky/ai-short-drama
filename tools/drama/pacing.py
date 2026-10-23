# -*- coding: utf-8 -*-
"""大纲节奏统计:人批准过的逐集节拍 → 统计 + 提示(spec 2026-10-23-剧集脑爆系统-design.md 第 2a 节)。

**只提示不拦**。阈值只收调研里的 A / B 档(spike-results/2026-10-23-剧集脑爆系统调研.md 第 3 节):
A = 具名从业者原话,B = 媒体或二手转述。「连续压」的出处只有 C 档(普遍流传),所以只统计、不设阈值。
"""
from __future__ import annotations

THRESHOLDS = {
    "hook_first10": {"text": "前 10 集每集结尾都有钩子", "value": 1.0,
                     "source": "虎嗅 2026-08 从业者访谈(经搜索摘要转述,原文没读到)", "tier": "B"},
    "max_reversal_gap": {"text": "3–5 集内至少有一次反转", "value": 5,
                         "source": "36氪 / 后浪研究所 2023-12 编剧原话(甲方要求 3 到 5 集内的最低反转数)", "tier": "A"},
    "paywall_window": {"text": "第 9–10 集开始卡付费点", "value": (9, 10),
                       "source": "虎嗅 2026-08 从业者访谈(经搜索摘要转述)", "tier": "B"},
}


def stats(beats: dict[int, dict]) -> dict:
    """beats:集号 → 节拍。返回覆盖率、反转间隔、卡点集号、最长连续「压」。"""
    nums = sorted(beats)
    first10 = [n for n in nums if n <= 10]
    reversals = [n for n in nums if beats[n]["reversal"] != "无"]
    longest = run = 0
    for n in nums:
        run = run + 1 if beats[n]["emotion"] == "压" else 0
        longest = max(longest, run)
    # reversal_gaps 只含相邻两次反转之间的间隔;两端另算:开头(第 0 集)到首个反转、末个反转到最后一个标过的集。
    # 一次反转都没有时,整段 L 集记在 leading_gap,trailing_gap 为 None
    last = nums[-1] if nums else 0
    return {"hook_coverage_first10": (sum(beats[n]["hook"] == "有" for n in first10) / len(first10)) if first10 else None,
            "reversal_gaps": [b - a for a, b in zip(reversals, reversals[1:])],
            "leading_gap": (reversals[0] if reversals else last) if nums else None,
            "trailing_gap": (last - reversals[-1]) if reversals else None,
            "paywall_episodes": [n for n in nums if beats[n]["paywall"] == "是"],
            "longest_press": longest}


def _src(key: str) -> str:
    t = THRESHOLDS[key]
    return f"(经验值:{t['text']};出处:{t['source']},{t['tier']} 档)"


def hints(beats: dict[int, dict]) -> list[str]:
    nums = sorted(beats)
    out = [f"第 {n} 集结尾没有钩子 {_src('hook_first10')}" for n in nums if n <= 10 and beats[n]["hook"] != "有"]
    reversals = [n for n in nums if beats[n]["reversal"] != "无"]
    limit = THRESHOLDS["max_reversal_gap"]["value"]
    out += [f"第 {a} 集到第 {b} 集隔了 {b - a} 集才有下一次反转 {_src('max_reversal_gap')}"
            for a, b in zip(reversals, reversals[1:]) if b - a > limit]
    if nums:
        src = _src("max_reversal_gap")
        if not reversals:
            if nums[-1] > limit:
                out.append(f"全剧 {nums[-1]} 集一次反转都没有 {src}")
        else:
            if reversals[0] > limit:
                out.append(f"开头到第 {reversals[0]} 集都没有反转 {src}")
            if nums[-1] - reversals[-1] > limit:
                out.append(f"第 {reversals[-1]} 集之后到第 {nums[-1]} 集都没有反转 {src}")
    lo, hi = THRESHOLDS["paywall_window"]["value"]
    if nums and nums[-1] >= hi and not any(lo <= n <= hi and beats[n]["paywall"] == "是" for n in nums):
        out.append(f"第 {lo}–{hi} 集里没有付费卡点候选 {_src('paywall_window')}")
    return out
