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

from tools.drama import repo, targets
from tools.drama.db import Store
from tools.gen import estimate, presets
from tools.gen.align import align_problems, plan_align
from tools.gen.assets import SPEC, asset_problems, fourview_problems, locked_seed, plan_asset_image, plan_fourview
from tools.gen.capability import MINIMAX_H3, QWEN_IMAGE_EDIT_2511, WHISPER_ALIGN
from tools.gen.planner import (first_frame_problems, frame_passes, key_frame_problems, last_frame_problems,
                               plan_first_frame, plan_key_frame, plan_last_frame, plan_video, video_problems)

IN_FLIGHT = ("pending", "queued", "running", "waiting_host")
KINDS = {"frames": ("episode", "首帧"), "lastframes": ("episode", "尾帧"), "keyframes": ("episode", "关键帧"),
         "videos": ("episode", "视频"),
         "subtitles": ("episode", "字幕"), "assets": ("drama", "资产图"), "fourviews": ("drama", "四视图")}
_ASSET_LABEL = {"character": "角色", "scene": "场景", "prop": "道具"}


@dataclass(frozen=True)
class Item:
    target_type: str
    target_id: int
    label: str
    status: str          # has / in_flight / blocked / todo
    reason: str = ""
    est_seconds: float | None = 0.0    # None = 无法估时
    seed: int | None = None      # 锁定种子(角色);None = 用「起始种子 + 序号」


@dataclass(frozen=True)
class FillPlan:
    kind: str
    scope_id: int
    items: tuple[Item, ...]
    est_note: str = ""       # 「按 X 估算」或无法估时的原因

    @property
    def todo(self) -> list[Item]:
        return [i for i in self.items if i.status == "todo"]

    def est_seconds(self) -> float | None:
        if any(i.est_seconds is None for i in self.todo):
            return None
        return sum(i.est_seconds for i in self.todo)

    def summary(self) -> str:
        count = {s: sum(1 for i in self.items if i.status == s) for s in ("has", "in_flight", "blocked")}
        what = KINDS[self.kind][1]
        est = self.est_seconds()
        cost = self.est_note if est is None else f"约 {est / 3600:.2f} 小时({self.est_note})"
        head = f"将提交 {len(self.todo)} 条{what},{cost}" if self.todo else f"没有要补的{what}"
        return (f"{head};跳过:已有 {count['has']}、在途 {count['in_flight']}、不能做 {count['blocked']}")


def _in_flight(store: Store, target_type: str, target_id: int, field: str) -> bool:
    """这个目标的这个字段有没有在途任务。按写回字段分:首帧 / 尾帧、正面图 / 四视图都是同一目标的 image 任务。
    关键帧任务挂在段落上:按写回的是哪一张认(tools/drama/targets.py)。"""
    if target_type == "segment_keyframe":
        seg = repo.get(store, "segment_keyframe", target_id)["segment_id"]
        rows = store.conn.execute(
            f"SELECT * FROM task WHERE target_type = 'segment' AND target_id = ? AND json_extract(params_json, '$.writes') = ?"
            f" AND state IN ({','.join('?' * len(IN_FLIGHT))})", (seg, targets.KEY_WRITES, *IN_FLIGHT)).fetchall()
        return any(targets.slot(store, t)[1] == target_id for t in rows)
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
    items, b = [], estimate.basis(store)
    if kind in ("frames", "lastframes", "keyframes", "videos", "subtitles"):
        repo.get(store, "episode", scope_id)
        for s in store.conn.execute("SELECT * FROM segment WHERE episode_id = ? ORDER BY number", (scope_id,)):
            label = f"段落 {s['number']}"
            img = b.item(QWEN_IMAGE_EDIT_2511)
            frame_est = None if img is None else img * frame_passes(store, s["id"])     # 两遍合成按两张图算
            if kind == "frames":
                items.append(_item(store, "segment", s, label, "image", "first_frame_path",
                                   lambda sid=s["id"]: first_frame_problems(store, sid), frame_est))
            elif kind == "lastframes":
                items.append(_item(store, "segment", s, label, "image", "last_frame_path",
                                   lambda sid=s["id"]: last_frame_problems(store, sid), frame_est))
            elif kind == "keyframes":                           # 按张列;没设关键帧的段落不算「缺」
                for k in repo.keyframes(store, s["id"]):
                    items.append(_item(store, "segment_keyframe", k, f"{label} · 第 {k['time_sec']:g} 秒", "image",
                                       "frame_path", lambda kid=k["id"]: key_frame_problems(store, kid), frame_est))
            elif kind == "subtitles":
                items.append(_item(store, "segment", s, label, "align", "subtitle_path",
                                   lambda sid=s["id"]: align_problems(store, sid), b.item(WHISPER_ALIGN)))
            else:
                est = (b.video(MINIMAX_H3, MINIMAX_H3.frames_for(s["duration_sec"]), presets.video_steps(store))
                       if s["duration_sec"] else 0.0)
                items.append(_item(store, "segment", s, label, "video", "video_path",
                                   lambda sid=s["id"]: video_problems(store, sid), est))
    elif kind == "fourviews":
        repo.get(store, "drama", scope_id)
        for r in store.conn.execute("SELECT * FROM character WHERE drama_id = ? ORDER BY id", (scope_id,)):
            items.append(_item(store, "character", r, f"角色「{r['name']}」", "image", "fourview_path",
                               lambda rid=r["id"]: fourview_problems(store, rid), b.item(QWEN_IMAGE_EDIT_2511),
                               seed_lock=False))      # 锁定的是正面图的种子,不管四视图
    else:
        repo.get(store, "drama", scope_id)
        for table in ("character", "scene", "prop"):
            for r in store.conn.execute(f"SELECT * FROM {table} WHERE drama_id = ? ORDER BY id", (scope_id,)):
                items.append(_item(store, table, r, f"{_ASSET_LABEL[table]}「{r['name']}」", "image", SPEC[table][0],
                                   lambda t=table, rid=r["id"]: asset_problems(store, t, rid),
                                   b.item(QWEN_IMAGE_EDIT_2511)))
    return FillPlan(kind, scope_id, tuple(items), b.note)


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
        elif kind == "keyframes":
            created.append(plan_key_frame(store, tid, seed=seed))
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
