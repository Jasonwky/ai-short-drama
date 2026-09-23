# -*- coding: utf-8 -*-
"""生成任务的生命周期。

- **生成参数不可变**(数据库触发器兜底):模型、参数、种子、提示词、参考素材一经创建就定死。
  重跑就新建一条 —— 这同时白送了「历史版本不覆盖,人可以挑」。
- **种子必须显式**:不能复现的生成等于没做。
- **状态只能按表迁移**;失败必须写原因,完成必须有产物 —— 不许静默失败、不许空手宣布完成。
- `waiting_host` 不是失败:机器不可达时任务还在,机器回来就接着走。
"""
from __future__ import annotations

import json
import sqlite3

from tools.drama.db import Store, now

TRANSITIONS: dict[str, set[str]] = {
    "pending": {"queued", "failed"},
    "queued": {"running", "waiting_host", "done", "failed"},
    "running": {"done", "failed", "waiting_host"},
    "waiting_host": {"queued", "running", "failed"},
    "done": set(),
    "failed": set(),
}


def create_task(store: Store, *, kind: str, target_type: str, target_id: int, model: str,
                params: dict, seed: int, prompt: str, refs: list[str]) -> int:
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise TypeError("seed 必须是显式整数 —— 不能复现的生成等于没做")
    with store.write() as c:
        return c.execute(
            "INSERT INTO task (kind, target_type, target_id, model, params_json, seed, prompt,"
            " refs_json, created_at, state_since) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, target_type, target_id, model,
             json.dumps(params, ensure_ascii=False, sort_keys=True), seed, prompt,
             json.dumps(list(refs), ensure_ascii=False), now(), now())).lastrowid


def set_task_state(store: Store, task_id: int, state: str, *, external_id: str | None = None,
                   host_id: int | None = None, result_path: str | None = None,
                   result_sha256: str | None = None, error: str | None = None) -> None:
    with store.write() as c:
        row = c.execute("SELECT state, started_at FROM task WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"task {task_id} 不存在")
        cur = row["state"]
        if state not in TRANSITIONS[cur]:
            raise ValueError(f"非法状态迁移:{cur} → {state}")
        if state == "failed" and not (error or "").strip():
            raise ValueError("失败必须写明原因(error),不许静默失败")
        if state == "done" and not result_path:
            raise ValueError("完成必须有产物路径(result_path)")

        sets: dict = {"state": state, "state_since": now()}
        for key, val in (("external_id", external_id), ("host_id", host_id),
                         ("result_path", result_path), ("result_sha256", result_sha256),
                         ("error", error)):
            if val is not None:
                sets[key] = val
        if state == "running" and row["started_at"] is None:
            sets["started_at"] = now()
        if state in ("done", "failed"):
            sets["finished_at"] = now()
        c.execute(f"UPDATE task SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                  (*sets.values(), task_id))


def tasks_for(store: Store, target_type: str, target_id: int) -> list[sqlite3.Row]:
    """某个目标的全部生成记录,旧的在前 —— 历史版本从这里挑。"""
    return store.conn.execute("SELECT * FROM task WHERE target_type = ? AND target_id = ? ORDER BY id",
                              (target_type, target_id)).fetchall()
