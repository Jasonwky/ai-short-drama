# -*- coding: utf-8 -*-
"""场记表:每段开头每个出场角色「在哪、什么姿态、面朝谁、拿着什么」的结构化状态(spec 2026-10-22-连续性保障-design.md 第 4 项)。

为什么:每段首帧各出各的,上一段谁跪着、谁拿着针,下一段一概不知道 —— 跪向背对、躺姿横出床外都出在这里
(题甲第 1 集看片,spike-results/2026-10-22-看片反馈六条归因.md)。做法:
- 分镜 LLM 只填闭集(姿态 / 朝向 / 左右 / 手持),存段落的 `blocking_json`;
- 首帧提示词里的这部分**由代码拼**(`phrase`),LLM 不写、不会漏;
- 相邻两段同场景时**对表**(`transition_problems`):姿态变了,前一段描述里要交代动作;
  左右站位对调只**提示**(`side_swaps`,越轴没有真片证据,不打回)。
存角色 / 道具的**名字**,不存 id:工程包导入时不用重映射,改名的事这里不管(分镜里也是按名字 @ 的)。
"""
from __future__ import annotations

import json

POSES = ("站", "坐", "跪", "仰躺", "侧躺", "趴", "走")
FIXED_FACING = ("镜头", "侧对镜头", "背对镜头")
SIDES = ("左", "中", "右")
NONE = "无"
# 拼进首帧提示词的写法。躺姿写死「全身在画面里摆放自然」:实战只写「躺在床上」时人横出床沿、腿伸出床外
POSE_PHRASE = {"站": "站着", "坐": "坐着", "跪": "跪着", "仰躺": "仰面平躺,全身舒展、完整地躺在身下的床或地面上",
               "侧躺": "侧身躺着,全身完整地躺在身下的床或地面上", "趴": "趴着", "走": "正在走动"}
# 姿态变成 X,前一段描述里要出现的动作词之一
POSE_VERBS = {"站": ("起身", "站起", "站了起来", "起来"), "坐": ("坐",), "跪": ("跪",), "仰躺": ("躺", "倒"),
              "侧躺": ("躺", "倒"), "趴": ("趴", "扑倒", "倒"), "走": ("走", "上前", "迈", "冲")}


def parse(text: str) -> list[dict]:
    return json.loads(text) if text and text.strip() else []


def problems(entries: list[dict], characters: list[str], props: list[str]) -> list[str]:
    """一段的场记表自身对不对:每个出场角色恰好一项、朝向 / 手持只能是本段的人和道具、一件道具不能两人同时拿。"""
    out = []
    names = [e["character"] for e in entries]
    out += [f"场记表缺「{c}」" for c in characters if c not in names]
    out += [f"场记表里的「{n}」不是本段角色" for n in names if n not in characters]
    out += [f"场记表里「{n}」出现了不止一次" for n in sorted({n for n in names if names.count(n) > 1})]
    for e in entries:
        if e["pose"] not in POSES:
            out.append(f"「{e['character']}」的姿态「{e['pose']}」不在 {'/'.join(POSES)} 里")
        if e["facing"] not in (*FIXED_FACING, *characters) or e["facing"] == e["character"]:
            out.append(f"「{e['character']}」面朝「{e['facing']}」:只能是本段其他角色或 {'/'.join(FIXED_FACING)}")
        if e["side"] not in SIDES:
            out.append(f"「{e['character']}」的位置「{e['side']}」不在 {'/'.join(SIDES)} 里")
        if e["holding"] != NONE and e["holding"] not in props:
            out.append(f"「{e['character']}」拿着「{e['holding']}」:不是本段道具")
    held = [e["holding"] for e in entries if e["holding"] != NONE]
    out += [f"道具「{p}」同时在两个人手里" for p in sorted({p for p in held if held.count(p) > 1})]
    return out


def phrase(entries: list[dict]) -> str:
    """拼进首帧提示词的那句(@名字,由 tags.expand 换成 Picture k)。"""
    parts = []
    for e in entries:
        facing = e["facing"] if e["facing"] in FIXED_FACING else f"@{e['facing']}"
        bits = [f"@{e['character']} {POSE_PHRASE[e['pose']]}", f"面朝{facing}" if e["facing"] != "背对镜头" else "背对镜头",
                f"在画面{e['side']}侧" if e["side"] != "中" else "在画面中间"]
        if e["holding"] != NONE:
            bits.append(f"手里拿着@{e['holding']}")
        parts.append(",".join(bits))
    return ";".join(parts)


def transition_problems(prev: list[dict], prev_description: str, cur: list[dict]) -> list[str]:
    """相邻两段(同一场景):两段都在场的人姿态变了,前一段描述里要交代这个动作。"""
    before = {e["character"]: e for e in prev}
    out = []
    for e in cur:
        p = before.get(e["character"])
        if p is None or p["pose"] == e["pose"]:
            continue
        if not any(v in prev_description for v in POSE_VERBS[e["pose"]]):
            out.append(f"「{e['character']}」上一段开头{p['pose']},这一段开头{e['pose']},上一段里没交代这个动作"
                       f"(要有「{'/'.join(POSE_VERBS[e['pose']])}」之类)")
    return out


def side_swaps(prev: list[dict], cur: list[dict]) -> list[str]:
    """相邻两段(同一场景)两个人左右对调 —— 只提示(越轴),不打回。"""
    a = {e["character"]: e["side"] for e in prev}
    b = {e["character"]: e["side"] for e in cur}
    both = [n for n in b if n in a and a[n] != "中" and b[n] != "中"]
    return [f"「{x}」「{y}」左右站位和上一段对调了(可能越轴)" for i, x in enumerate(both) for y in both[i + 1:]
            if a[x] != a[y] and a[x] == b[y] and a[y] == b[x]]
