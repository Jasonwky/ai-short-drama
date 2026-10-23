# -*- coding: utf-8 -*-
"""竖屏可拍性:有些构图在 9:16 里画不出来,分镜阶段就拦(spec 2026-10-23-不穿帮验收-design.md 第 2 节)。

为什么:竖屏全景里「躺着的全身 + 站着的人 + 整张床」放不下,模型保住人的大小,就让人伸出床外。
2026-10-23 第 6 段对照(spike-results/2026-10-23-躺姿穿帮归因与规避方案.md):原样全景 4/4 出床、
床尾纵深 2/2 出床、近景(只拍胸口以上)2/2 合理。所以规则只放行近景和特写。
三人以上同框只提示:真人竖屏剧「三个人以上的群戏比较少,即便有群戏也是会分批次」(界面新闻采访主创)。
"""
from __future__ import annotations

SHOT_TYPES = ("特写", "近景", "中景", "全景")
CLOSE = ("特写", "近景")
LYING = ("仰躺", "侧躺", "趴")
VERTICAL = "9:16"
# 与 10-23 对照 a1 组同一写法(那组 2/2 合理)
CLOSE_PHRASE = "近景,画面只拍到躺着的人胸口以上,站着的人在画面上方"


def _lying(entries: list[dict]) -> list[str]:
    return [e["character"] for e in entries if e["pose"] in LYING]


def problems(aspect: str, entries: list[dict], shot_type: str) -> list[str]:
    """拦:竖屏、有人躺着、同框 ≥ 2 人,景别却不是近景 / 特写。"""
    lying = _lying(entries)
    if aspect != VERTICAL or len(entries) < 2 or not lying or shot_type in CLOSE:
        return []
    return [f"竖屏里「{'、'.join(lying)}」躺着、同框 {len(entries)} 人,景别是「{shot_type or '没填'}」:"
            f"画面放不下,人会伸出床外 —— 景别只能选近景或特写(实测全景 4/4 出床、近景 2/2 合理)"]


def hints(aspect: str, entries: list[dict]) -> list[str]:
    """只提示:竖屏三人以上同框。"""
    if aspect == VERTICAL and len(entries) >= 3:
        return [f"竖屏同框 {len(entries)} 人:真人竖屏剧三人以上的群戏少,多半分批拍 —— 考虑拆成两段"]
    return []


def phrase(aspect: str, entries: list[dict], shot_type: str) -> str:
    """拼进首帧提示词的那句(由代码写,LLM 不写);规则不适用时为空。"""
    if aspect == VERTICAL and len(entries) >= 2 and _lying(entries) and shot_type in CLOSE:
        return CLOSE_PHRASE
    return ""
