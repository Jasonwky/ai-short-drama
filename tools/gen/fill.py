# -*- coding: utf-8 -*-
"""增量补全:一集(或一部剧)里**只跑缺的 / 失败的**(设计 §七「贯穿所有面板」、调研结论「禁止全量重跑」)。

每个目标归入四类之一,界面上逐条列出:
- `has`:已经有当前产物 —— 跳过(换版本走「用这一版」,不在这里);
- `in_flight`:已有在途任务 —— 跳过(不重复交,重交就是白烧一次 GPU);
- `blocked`:内容不就绪 —— 跳过,写明原因(与单条生成时同一套检查);
- `todo`:要做 —— 包括上次失败、至今没有产物的。

两态:`plan_fill` 只算不改,给出清单、每条的种子与估算总耗时;人确认后 `confirm_fill` 才建任务。
确认时**只认确认页上列出的那几条**,并逐条重新判一次 —— 这期间状态变了(比如别人刚点过单条生成)的就跳过,不重复交。
"""
from __future__ import annotations

from dataclasses import dataclass

from tools.drama import repo
from tools.drama.db import Store
from tools.gen import presets
from tools.gen.align import align_problems, plan_align
from tools.gen.assets import SPEC, asset_problems, fourview_problems, locked_seed, plan_asset_image, plan_fourview
from tools.gen.capability import MINIMAX_H3, QWEN_IMAGE_EDIT_2511, WHISPER_ALIGN
from tools.gen.planner import (first_frame_problems, last_frame_problems, plan_first_frame, plan_last_frame, plan_video,
                               video_problems)

IN_FLIGHT = ("pending", "queued", "running", "waiting_host")
KINDS = {"frames": ("episode", "首帧"), "lastframes": ("episode", "尾帧"), "videos": ("episode", "视频"),
         "subtitles": ("episode", "字幕"), "assets": ("drama", "资产图"), "fourviews": ("drama", "四视图")}
_ASSET_LABEL = {"character": "角色", "scene": "场景", "prop": "道具"}


@dataclass(frozen=True)
class Item:
    target_type: str
    target_id: int
    label: str
    status: str          # has / in_flight / blocked / todo
    reason: str = ""
    est_seconds: float = 0.0
    seed: int | None = None      # 锁定种子(角色);None = 用「起始种子 + 序号」


@dataclass(frozen=True)
class FillPlan:
    kind: str
    scope_id: int
    items: tuple[Item, ...]

    @property
    def todo(self) -> list[Item]:
        return [i for i in self.items if i.status == "todo"]

    def est_seconds(self) -> float:
        return sum(i.est_seconds for i in self.todo)

    def summary(self) -> str:
        count = {s: sum(1 for i in self.items if i.status == s) for s in ("has", "in_flight", "blocked")}
        what = KINDS[self.kind][1]
        head = (f"将提交 {len(self.todo)} 条{what},约 {self.est_seconds() / 3600:.2f} 小时(按实测耗时估算)"
                if self.todo else f"没有要补的{what}")
        return (f"{head};跳过:已有 {count['has']}、在途 {count['in_flight']}、不能做 {count['blocked']}")


def _in_flight(store: Store, target_type: str, target_id: int, field: str) -> bool:
    """这个目标的这个字段有没有在途任务。按写回字段分:首帧 / 尾帧、正面图 / 四视图都是同一目标的 image 任务。"""
    return store.conn.execute(
        f"SELECT 1 FROM task WHERE target_type = ? AND target_id = ? AND json_extract(params_json, '$.writes') = ?"
        f" AND state IN ({','.join('?' * len(IN_FLIGHT))}) LIMIT 1",
        (target_type, target_id, field, *IN_FLIGHT)).fetchone() is not None


def _item(store: Store, target_type: str, row, label: str, kind: str, field: str, problems, est: float,
          seed_lock: bool = True) -> Item:
    if row[field]:
        return Item(target_type, row["id"], label, "has")
    if _in_flight(store, target_type, row["id"], field):
        return Item(target_type, row["id"], label, "in_flight")
    probs = problems()
    if probs:
        return Item(target_type, row["id"], label, "blocked", ";".join(probs))
    return Item(target_type, row["id"], label, "todo", est_seconds=est,
                seed=locked_seed(store, target_type, row["id"]) if seed_lock and target_type in SPEC else None)


def plan_fill(store: Store, kind: str, scope_id: int) -> FillPlan:
    if kind not in KINDS:
        raise ValueError(f"未知的补全类型 {kind},可选:{sorted(KINDS)}")
    items = []
    if kind in ("frames", "lastframes", "videos", "subtitles"):
        repo.get(store, "episode", scope_id)
        for s in store.conn.execute("SELECT * FROM segment WHERE episode_id = ? ORDER BY number", (scope_id,)):
            label = f"段落 {s['number']}"
            if kind == "frames":
                items.append(_item(store, "segment", s, label, "image", "first_frame_path",
                                   lambda sid=s["id"]: first_frame_problems(store, sid), QWEN_IMAGE_EDIT_2511.sec_per_item))
            elif kind == "lastframes":
                items.append(_item(store, "segment", s, label, "image", "last_frame_path",
                                   lambda sid=s["id"]: last_frame_problems(store, sid), QWEN_IMAGE_EDIT_2511.sec_per_item))
            elif kind == "subtitles":
                items.append(_item(store, "segment", s, label, "align", "subtitle_path",
                                   lambda sid=s["id"]: align_problems(store, sid), WHISPER_ALIGN.sec_per_item))
            else:
                est = (MINIMAX_H3.est_seconds(MINIMAX_H3.frames_for(s["duration_sec"]), presets.video_steps(store))
                       if s["duration_sec"] else 0.0)
                items.append(_item(store, "segment", s, label, "video", "video_path",
                                   lambda sid=s["id"]: video_problems(store, sid), est))
    elif kind == "fourviews":
        repo.get(store, "drama", scope_id)
        for r in store.conn.execute("SELECT * FROM character WHERE drama_id = ? ORDER BY id", (scope_id,)):
            items.append(_item(store, "character", r, f"角色「{r['name']}」", "image", "fourview_path",
                               lambda rid=r["id"]: fourview_problems(store, rid), QWEN_IMAGE_EDIT_2511.sec_per_item,
                               seed_lock=False))      # 锁定的是正面图的种子,不管四视图
    else:
        repo.get(store, "drama", scope_id)
        for table in ("character", "scene", "prop"):
            for r in store.conn.execute(f"SELECT * FROM {table} WHERE drama_id = ? ORDER BY id", (scope_id,)):
                items.append(_item(store, table, r, f"{_ASSET_LABEL[table]}「{r['name']}」", "image", SPEC[table][0],
                                   lambda t=table, rid=r["id"]: asset_problems(store, t, rid),
                                   QWEN_IMAGE_EDIT_2511.sec_per_item))
    return FillPlan(kind, scope_id, tuple(items))


def confirm_fill(store: Store, kind: str, scope_id: int, targets: list[tuple[str, int]], base_seed: int) -> list[int]:
    """只为确认页上列出的 targets 建任务,种子 = base_seed + 在清单里的序号(锁了种子的角色用锁定种子);
    期间状态变了的跳过。"""
    now_todo = {(i.target_type, i.target_id) for i in plan_fill(store, kind, scope_id).todo}
    created = []
    for n, (ttype, tid) in enumerate(targets):
        if (ttype, tid) not in now_todo:
            continue
        seed = base_seed + n
        if kind == "frames":
            created.append(plan_first_frame(store, tid, seed=seed))
        elif kind == "lastframes":
            created.append(plan_last_frame(store, tid, seed=seed))
        elif kind == "fourviews":
            created.append(plan_fourview(store, tid, seed=seed))
        elif kind == "videos":
            created.append(plan_video(store, tid, seed=seed))
        elif kind == "subtitles":
            created.append(plan_align(store, tid))                  # 对齐是确定性的,不用种子
        else:
            locked = locked_seed(store, ttype, tid)
            created.append(plan_asset_image(store, ttype, tid, seed=seed if locked is None else locked))
    return created
