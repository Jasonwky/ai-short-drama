# -*- coding: utf-8 -*-
"""把「这个段落要对字幕」变成一条不可变的对齐任务(与首帧 / 视频同一张 task 表、同一个 worker)。

对齐的输入是**当前视频 + 段落描述里的台词**,规划时定死:之后换了视频或改了台词,旧任务的结果对不上新内容,
worker 执行前会核对视频没换(`aligner`)。whisper 是确定性解码,种子记 0。
"""
from __future__ import annotations

from tools.drama import repo, rules
from tools.drama.db import Store
from tools.drama.tasks import create_task
from tools.gen.capability import WHISPER_ALIGN


def align_problems(store: Store, segment_id: int) -> list[str]:
    seg = repo.get(store, "segment", segment_id)
    problems = []
    if not seg["video_path"]:
        problems.append("还没有视频")
    if not rules.dialogue_lines(seg["description"]):
        problems.append("段落描述里没有台词(「某某说：「……」」),不需要字幕")
    return problems


def plan_align(store: Store, segment_id: int) -> int:
    problems = align_problems(store, segment_id)
    if problems:
        raise ValueError(";".join(problems))
    seg = repo.get(store, "segment", segment_id)
    lines = [line for _, line in rules.dialogue_lines(seg["description"])]
    params = {"workflow": "whisper_align", "video": seg["video_path"], "lines": lines,
              "writes": "subtitle_path", "expect_ext": ".srt"}
    return create_task(store, kind="align", target_type="segment", target_id=segment_id, model=WHISPER_ALIGN.name,
                       params=params, seed=0, prompt="\n".join(lines), refs=[seg["video_path"]])
