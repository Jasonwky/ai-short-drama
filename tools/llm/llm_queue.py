# -*- coding: utf-8 -*-
"""持久的 LLM 任务队列(spec 2026-10-24-逐集批量出剧本分镜-design.md 第 4、8 节)。

只管「排了什么、排到哪」这份数据;什么时候开下一条由 `serve/runtime.Runtime.pump_queue` 决定(加锁,一次只跑一条)。
- 重启后 `waiting` 的接着跑;重启时正在跑的那条由 `runs.fail_stale` 标失败,队列项保持 `started`,不自动重试;
- 暂停原因存 `llm_queue_state`(单行);暂停时不出队,人点「继续」才清掉。
"""
from __future__ import annotations

import json
import sqlite3

from tools.drama.db import Store, now

BREAK_AFTER = 3        # 连续几条队列项因 LLM 本身(不可用 / 连不上 / 截断 / 拒答)失败就暂停整个队列


def enqueue(store: Store, task: str, target_type: str, target_id: int, params: dict) -> int:
    with store.write() as c:
        return c.execute("INSERT INTO llm_queue (task, target_type, target_id, params_json, created_at)"
                         " VALUES (?, ?, ?, ?, ?)",
                         (task, target_type, target_id, json.dumps(params, ensure_ascii=False), now())).lastrowid


def params(row: sqlite3.Row) -> dict:
    return json.loads(row["params_json"])


def next_waiting(store: Store) -> sqlite3.Row | None:
    return store.conn.execute("SELECT * FROM llm_queue WHERE state = 'waiting' ORDER BY id LIMIT 1").fetchone()


def running_item(store: Store) -> sqlite3.Row | None:
    """已开始、而且它的 LLM 运行还在跑的那一条(一次最多一条)。"""
    return store.conn.execute("SELECT q.* FROM llm_queue q JOIN llm_run r ON r.id = q.run_id"
                              " WHERE q.state = 'started' AND r.state = 'running' LIMIT 1").fetchone()


def item_for_run(store: Store, run_id: int) -> sqlite3.Row | None:
    return store.conn.execute("SELECT * FROM llm_queue WHERE run_id = ?", (run_id,)).fetchone()


def mark_started(store: Store, item_id: int, run_id: int | None, reason: str = "") -> None:
    """开了运行(run_id),或当场开不了(run_id 为 None,reason 写原因)。"""
    with store.write() as c:
        c.execute("UPDATE llm_queue SET state = 'started', run_id = ?, reason = ?, started_at = ?"
                  " WHERE id = ? AND state = 'waiting'", (run_id, reason, now(), item_id))


def position(store: Store, item_id: int) -> int:
    return store.conn.execute("SELECT COUNT(*) FROM llm_queue WHERE state = 'waiting' AND id <= ?",
                              (item_id,)).fetchone()[0]


def waiting_count(store: Store, episode_ids: list[int]) -> int:
    if not episode_ids:
        return 0
    marks = ",".join("?" * len(episode_ids))
    return store.conn.execute(f"SELECT COUNT(*) FROM llm_queue WHERE state = 'waiting' AND target_type = 'episode'"
                              f" AND target_id IN ({marks})", episode_ids).fetchone()[0]


def cancel_waiting(store: Store, episode_ids: list[int]) -> int:
    """停掉这些集还没开始的;正在跑的那条不打断。"""
    if not episode_ids:
        return 0
    marks = ",".join("?" * len(episode_ids))
    with store.write() as c:
        return c.execute(f"UPDATE llm_queue SET state = 'cancelled' WHERE state = 'waiting' AND target_type = 'episode'"
                         f" AND target_id IN ({marks})", episode_ids).rowcount


def paused(store: Store) -> str:
    return store.conn.execute("SELECT paused_reason FROM llm_queue_state WHERE id = 1").fetchone()[0]


def pause(store: Store, reason: str) -> None:
    with store.write() as c:
        c.execute("UPDATE llm_queue_state SET paused_reason = ? WHERE id = 1", (reason,))


def resume(store: Store) -> None:
    pause(store, "")
