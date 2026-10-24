# -*- coding: utf-8 -*-
"""原创分集的骨架(spec 2026-10-24-原创分集-design.md)。

本文件先放三方共用的纯函数(第 4 节):读骨架、按权重分集数、一幕切成每批 ≤ 30 集、某集属于哪一幕、人读版本。
骨架 LLM 任务本身(SYSTEM / schema / 判卷 / 写回)在下面追加。

存的骨架:{"arc", "rhythm", "ending", "total", "acts": [{"title", "goal", "climax", "weight", "start", "end"}]}。
集数由代码按 LLM 给的相对权重分(LLM 不出绝对数),每幕至少 1 集。
"""
from __future__ import annotations

import json

from tools.drama import repo
from tools.drama.db import Store
from tools.llm import outline


def load(store: Store, drama_id: int) -> dict | None:
    raw = repo.get(store, "drama", drama_id)["skeleton_json"]
    return json.loads(raw) if raw else None


def allocate(total: int, weights: list[float]) -> list[tuple[int, int]]:
    """按权重把 total 集分到各幕(最大余数法,每幕 ≥ 1),返回各幕的 (起, 止) 集号。"""
    counts = outline.split_episodes(total, [max(1, round(w * 1000)) for w in weights])
    out, start = [], 1
    for n in counts:
        out.append((start, start + n - 1))
        start += n
    return out


def batches(skel: dict) -> list[tuple[int, int, int]]:
    """展开的批次:[(幕号, 起, 止)];一幕超过 outline.MAX_EPISODES 集就切成几批,各批集数尽量相等。"""
    out = []
    for no, act in enumerate(skel["acts"], 1):
        n = act["end"] - act["start"] + 1
        k = -(-n // outline.MAX_EPISODES)
        start = act["start"]
        for i in range(k):
            size = n // k + (i < n % k)
            out.append((no, start, start + size - 1))
            start += size
    return out


def act_for(skel: dict, number: int) -> tuple[int, dict] | None:
    for no, act in enumerate(skel["acts"], 1):
        if act["start"] <= number <= act["end"]:
            return no, act
    return None


def render(skel: dict) -> str:
    """喂给 LLM、也给人看的骨架:主线 / 节奏 / 结局 + 逐幕集号区间、目标、幕末大事件。"""
    head = f"主线:{skel['arc']} · 节奏:{skel['rhythm']} · 结局:{skel['ending']};共 {skel['total']} 集"
    acts = [f"第 {no} 幕「{a['title']}」第 {a['start']}–{a['end']} 集:目标:{a['goal']};幕末:{a['climax']}"
            for no, a in enumerate(skel["acts"], 1)]
    return "\n".join([head, *acts])
