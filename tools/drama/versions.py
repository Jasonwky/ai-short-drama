# -*- coding: utf-8 -*-
"""挑历史版本:把某次生成的产物设为「当前版」(设计 §七「历史版本不覆盖」「逐条重绘保留历史」)。

worker 写回只填空位(已有产物不覆盖),新版本留在 task 表里 —— 换不换由人在这里决定。
换版本会让下游作废,所以走 plan/confirm 两态(CLAUDE.md):
- 换角色/场景/道具的参考图 → 绑定了它的段落,参考帧和视频全部作废(`invalidation` 的同名事件);
- 换段落首帧/尾帧 → 只有本段视频作废(另一张参考帧不受影响,不清);
- 换段落视频 → 本段字幕作废(时间轴是对着旧视频算的),要对新视频重新对齐。
`plan_pick` 只算不改;人看了代价确认后 `confirm_pick` 才落盘。
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from tools.drama import invalidation, repo
from tools.drama.db import Store
from tools.drama.invalidation import Impact
from tools.gen.capability import Capability

_FRAME_FIELDS = ("first_frame_path", "last_frame_path")


@dataclass(frozen=True)
class Pick:
    task_id: int
    target_type: str
    target_id: int
    field: str
    new_path: str
    impact: Impact

    def summary(self) -> str:
        return self.impact.summary()


def plan_pick(store: Store, task_id: int, *, video_cap: Capability, image_cap: Capability) -> Pick:
    task = repo.get(store, "task", task_id)
    if task["state"] != "done":
        raise ValueError(f"task {task_id} 状态是 {task['state']},只有完成的任务能被选为当前版")
    field = json.loads(task["params_json"])["writes"]
    ttype, tid = task["target_type"], task["target_id"]
    if repo.get(store, ttype, tid)[field] == task["result_path"]:
        raise ValueError("这一版已经是当前版")

    if ttype in ("character", "scene", "prop"):
        imp = invalidation.impact(store, ttype, tid, video_cap=video_cap, image_cap=image_cap)
    elif field in _FRAME_FIELDS:
        seg = repo.get(store, "segment", tid)
        if seg["video_path"]:
            imp = Impact((tid,), 0, 1, video_cap.est_seconds(video_cap.frames_for(seg["duration_sec"])))
        else:
            imp = Impact((), 0, 0, 0.0)
    else:
        imp = Impact((), 0, 0, 0.0)   # 换视频 / 字幕:不作废别的视频(换视频时字幕在 confirm 里清)
    return Pick(task_id, ttype, tid, field, task["result_path"], imp)


def confirm_pick(store: Store, pick: Pick) -> None:
    if pick.target_type in ("character", "scene", "prop"):
        invalidation.apply(store, pick.impact)
    elif pick.field in _FRAME_FIELDS and pick.impact.segment_ids:
        with store.write() as c:
            c.execute("UPDATE segment SET video_path = NULL, subtitle_path = NULL WHERE id = ?", (pick.target_id,))
    elif pick.field == "video_path":
        repo.update(store, "segment", pick.target_id, subtitle_path=None)
    repo.update(store, pick.target_type, pick.target_id, **{pick.field: pick.new_path})
