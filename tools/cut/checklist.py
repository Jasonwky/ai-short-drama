# -*- coding: utf-8 -*-
"""看片验收清单:把「达到真人剧集水平」拆成逐项可勾的检查(spec 2026-10-23-不穿帮验收-design.md 第 1 节)。

出处两类:平台 AI 短剧审核条目(二手转述,spike-results/2026-10-23-AI短剧防穿帮的判定与实施调研.md)
与我们自己踩过的坑(10-21 白底、10-22 床在正堂 / 跪向 / 多一只手、10-23 躺姿出床)。
「人勾」的项人必须逐项勾上才能确认;「自动」的项已有检测把关(烧字、白屏、字幕判卷拦导出),这里只列出来对照。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    key: str
    name: str
    source: str
    how: str          # 人勾 / 自动 / 自动 + 人勾


ITEMS = (
    Item("identity", "每个人认得出是谁,前后段没换脸", "平台「人物建模」", "人勾"),
    Item("costume", "服装、发型、配饰与角色造型一致,和上一段一样", "平台「服化道不一致」;场记连续性", "人勾"),
    Item("anatomy", "人数对、没有重复的人;手脚数对", "平台「三条腿」;10-22 第 4 段多一只手", "人勾"),
    Item("physics", "人在家具里、比例合理、动作不反常识", "平台「违背物理或空间逻辑」;10-23 躺姿出床", "人勾"),
    Item("props", "道具大小、数量合理,在该在的手里", "10-21 / 10-22 银针", "人勾"),
    Item("direction", "朝向和视线对、没越轴", "场记 180 度线;10-22 跪向", "人勾"),
    Item("set", "场景对、陈设属于这个场景", "10-22 床在正堂", "自动 + 人勾"),
    Item("seam", "和上一段接得上(看接缝与切镜帧)", "场记连续性;平台「场景突兀切换」", "人勾"),
    Item("frozen", "画面没有异常静止", "平台「画面异常静止」", "人勾"),
    Item("text", "画面里没有烧进去的字、没有双轨字幕", "平台「双轨字幕」", "自动"),
    Item("blank", "没有影棚白底", "10-21 白底", "自动"),
    Item("lipsync", "台词对得上、字幕对齐判卷通过", "平台「音画不同步」", "自动"),
)
MANUAL = tuple(i.key for i in ITEMS if "人勾" in i.how)
_NAME = {i.key: i.name for i in ITEMS}


def missing(checked: list[str]) -> list[str]:
    """还没勾的人勾项(按清单顺序,给名称)。"""
    return [_NAME[k] for k in MANUAL if k not in checked]
