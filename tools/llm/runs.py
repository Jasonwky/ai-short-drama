# -*- coding: utf-8 -*-
"""LLM 产物的生命周期:running → proposed / failed;proposed → approved / rejected。

- 产物落盘即不可改、假 LLM 产物永远过不了审批 —— 由数据库触发器兜底(`db.LLM_RUN_DDL`);
- **人工批准即锁定**:同一任务、同一目标、同一输入(input_sha)已有批准版时,`locked()` 返回它,
  调用方直接用这一版,**不再问 LLM**(CLAUDE.md「锁定后路径纯代码,可逐字节复现」)。
"""
from __future__ import annotations

import json
import sqlite3

from tools.drama.db import Store, now

TRANSITIONS = {"running": {"proposed", "failed"}, "proposed": {"approved", "rejected"},
               "failed": set(), "approved": set(), "rejected": set()}


def start(store: Store, *, task: str, target_type: str, target_id: int, model: str, input_sha: str,
          llm_mode: str, params: dict | None = None) -> int:
    """params 是人给的输入(梗概原文、目标分钟数…);批准时要用它重建上下文,所以和产物一样不可改。"""
    with store.write() as c:
        return c.execute("INSERT INTO llm_run (task, target_type, target_id, model, input_sha, params_json,"
                         " llm_mode, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (task, target_type, target_id, model, input_sha,
                          json.dumps(params or {}, ensure_ascii=False, sort_keys=True), llm_mode, now())).lastrowid


def _move(store: Store, run_id: int, state: str, **fields) -> None:
    with store.write() as c:
        row = c.execute("SELECT state FROM llm_run WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"llm_run {run_id} 不存在")
        if state not in TRANSITIONS[row["state"]]:
            raise ValueError(f"LLM 产物状态不能从 {row['state']} 变成 {state}")
        sets = {"state": state, **fields}
        c.execute(f"UPDATE llm_run SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), run_id))


def propose(store: Store, run_id: int, output: dict, journal_path: str) -> None:
    _move(store, run_id, "proposed", output_json=json.dumps(output, ensure_ascii=False), journal_path=journal_path)


def fail(store: Store, run_id: int, error: str, journal_path: str | None = None) -> None:
    if not error.strip():
        raise ValueError("失败必须写明原因")
    _move(store, run_id, "failed", error=error, journal_path=journal_path)


def fail_stale(store: Store) -> int:
    """控制台重启时,上次还在跑的 LLM 运行不会再有人接着跑:标失败,别让它永远显示「生成中」(同 export.fail_stale)。"""
    ids = [r[0] for r in store.conn.execute("SELECT id FROM llm_run WHERE state = 'running'")]
    for i in ids:
        fail(store, i, "控制台重启时仍在运行,这次运行中断了 —— 请重新发起")
    return len(ids)


def approve(store: Store, run_id: int, decision: dict | None = None) -> None:
    """decision:人在批准时做的选择(评审团采纳了哪些问题、局部改写挑了哪个);不需要选择的任务为 None。"""
    fields = {"decided_at": now()}
    if decision is not None:
        fields["decision_json"] = json.dumps(decision, ensure_ascii=False)
    _move(store, run_id, "approved", **fields)


def reject(store: Store, run_id: int) -> None:
    _move(store, run_id, "rejected", decided_at=now())


def get(store: Store, run_id: int) -> sqlite3.Row:
    row = store.conn.execute("SELECT * FROM llm_run WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(f"llm_run {run_id} 不存在")
    return row


def output(row: sqlite3.Row) -> dict:
    return json.loads(row["output_json"])


def params(row: sqlite3.Row) -> dict:
    return json.loads(row["params_json"])


def locked(store: Store, *, task: str, target_type: str, target_id: int, input_sha: str) -> sqlite3.Row | None:
    return store.conn.execute(
        "SELECT * FROM llm_run WHERE task = ? AND target_type = ? AND target_id = ? AND input_sha = ?"
        " AND state = 'approved' ORDER BY id DESC LIMIT 1", (task, target_type, target_id, input_sha)).fetchone()


def history(store: Store, target_type: str, target_id: int, task: str | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM llm_run WHERE target_type = ? AND target_id = ?"
    args: list = [target_type, target_id]
    if task:
        sql += " AND task = ?"
        args.append(task)
    return store.conn.execute(sql + " ORDER BY id", args).fetchall()
