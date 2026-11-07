# -*- coding: utf-8 -*-
"""转写按字时间戳断成句,给录屏详情页逐句显示与跳转用。纯函数。"""
from __future__ import annotations

END_MARKS = "。!?!?…"
PAUSE = 0.8        # 秒:whisper 的逐字时间首尾相接,停顿被算进停顿后那个字的时长(真机《掌上珠》第 1 集:「幸」6.1 秒、「是」9.5 秒)
LEAD = 0.4         # 停顿后那一句从「那个字结束前 LEAD 秒」算起 —— 点句子跳过去正好是重新开口
MAX_CHARS = 30     # 一句最长,太长也断


def sentences(chars: list, gap: float = 0.6) -> list[dict]:
    """chars = [[字, 起, 止], ...] → [{"start", "end", "text"}]。断句:相邻两字间隔 > gap 秒;前一字是句末标点;
    这个字自己的时长 > PAUSE(停顿被算进了它;当前句只有一个字时不断 —— whisper 常让相邻两字共用一段时长);这一句已满 MAX_CHARS 字。"""
    out: list[dict] = []
    cur: dict | None = None
    for ch, start, end in chars:
        paused = end - start > PAUSE
        if cur is not None and (round(start - cur["end"], 3) > gap or cur["text"][-1] in END_MARKS
                                or (paused and len(cur["text"]) >= 2) or len(cur["text"]) >= MAX_CHARS):
            out.append(cur)
            cur = None
        if cur is None:
            cur = {"start": round(max(start, end - LEAD), 2) if paused else start, "end": end, "text": ch}
        else:
            cur["text"] += ch
            cur["end"] = end
    if cur is not None:
        out.append(cur)
    return out
