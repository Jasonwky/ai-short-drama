# -*- coding: utf-8 -*-
"""提示词里的 `@名字` 引用,以及参考图槽位分配。

「模型不会自己知道哪张图管什么」(设计 §6.1):人和 LLM 在提示词里写 `@林小雨`,
提交生成前再按槽位展开成编号标签(见 tools/drama/tags.py)。

槽位是硬约束:出首帧用的 Qwen-Image-Edit 只有 3 个图槽。
「角色四视图 + 场景空镜 + 道具白底」正好占满,两个角色同框就放不下。
闸门 0-3 实测:出槽的道具只靠文字描述,**设计必然丢失**(雕龙淡青玉 → 通用绿色团块)。
所以出槽的素材必须报出来,不许静默丢弃。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_UNKNOWN_FRAGMENT = re.compile(r"[^\s@，。,.!?！？：:、「」]{1,12}")


@dataclass(frozen=True)
class Ref:
    kind: str          # character / scene / prop
    name: str
    path: str | None   # 参考图路径;缺图时为 None


def find_mentions(prompt: str, names: list[str]) -> tuple[list[str], list[str]]:
    """返回 (按出现顺序去重的已知名字, 对不上的片段)。

    名字取「@ 之后最长的已知名字前缀」:`@林小雨推开门` 要识别成「林小雨」,
    不能把「推开门」吞进名字;同时有「林小」和「林小雨」时取后者。
    """
    by_len = sorted(set(names), key=len, reverse=True)
    found: list[str] = []
    unknown: list[str] = []
    i = 0
    while (j := prompt.find("@", i)) >= 0:
        rest = prompt[j + 1:]
        hit = next((n for n in by_len if rest.startswith(n)), None)
        if hit is None:
            m = _UNKNOWN_FRAGMENT.match(rest)
            unknown.append(m.group(0) if m else "")
            i = j + 1
            continue
        if hit not in found:
            found.append(hit)
        i = j + 1 + len(hit)
    return found, unknown


def allocate_slots(characters: list[tuple[Ref, int]], scene: Ref | None, props: list[Ref],
                   slots: int) -> tuple[list[Ref], list[Ref]]:
    """按优先级填参考图槽位,返回 (进槽的, 出槽的)。

    优先级(设计 §6.1):台词最多的角色 > 场景 > 第二个角色 > 道具 > 其余角色。
    characters 是 (角色, 本镜台词字数);台词一样多时保持绑定顺序。
    """
    chars = [r for r, _ in sorted(characters, key=lambda x: -x[1])]
    order = chars[:1] + ([scene] if scene else []) + chars[1:2] + list(props) + chars[2:]
    return order[:slots], order[slots:]
