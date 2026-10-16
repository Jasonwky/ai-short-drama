# -*- coding: utf-8 -*-
"""生成任务写到哪:(表, 行号, 字段)。worker 写回、挑历史版本、参考帧面板分组、留痕「同目标的其它尝试」、改参重跑都从这里算。

关键帧任务挂在**段落**上(task 表不改),参数里 `writes = key_frame_path`、`keyframe_id = <行号>`,写到那张关键帧的 `frame_path`
(specs/2026-10-15-多关键帧与草稿档-design.md §2.3)。

- P12 时代的关键帧任务没有 `keyframe_id`(任务参数不可改,补不进去):解释为该段 **`legacy_v9 = 1` 的那行**(迁移时从段落列搬来的)。
  不能用「该段编号最小的那行」—— 人删掉搬来的那张再加新的,旧任务就会误指到新的那张;
- 那一行被删了 = 行号 None(「关键帧已删除」),调用方不写回、不让挑。
"""
from __future__ import annotations

import json
import sqlite3

from tools.drama.db import Store

KEY_WRITES = "key_frame_path"


def keyframe_id(store: Store, task: sqlite3.Row) -> int | None:
    """关键帧任务指向的那张关键帧;已删除 = None。"""
    p = json.loads(task["params_json"])
    if "keyframe_id" in p:
        kid = p["keyframe_id"]
    else:
        row = store.conn.execute("SELECT id FROM segment_keyframe WHERE segment_id = ? AND legacy_v9 = 1",
                                 (task["target_id"],)).fetchone()
        kid = row[0] if row else None
    if kid is None or store.conn.execute("SELECT 1 FROM segment_keyframe WHERE id = ?", (kid,)).fetchone() is None:
        return None
    return kid


def slot(store: Store, task: sqlite3.Row) -> tuple[str, int | None, str]:
    p = json.loads(task["params_json"])
    if p.get("writes") == KEY_WRITES:
        return "segment_keyframe", keyframe_id(store, task), "frame_path"
    return task["target_type"], task["target_id"], p["writes"]


def current(store: Store, task: sqlite3.Row) -> str | None:
    """这条任务的写回位置上现在挂的产物;目标已删 = None。"""
    table, row_id, field = slot(store, task)
    if row_id is None:
        return None
    row = store.conn.execute(f"SELECT {field} FROM {table} WHERE id = ?", (row_id,)).fetchone()
    return row[0] if row else None
