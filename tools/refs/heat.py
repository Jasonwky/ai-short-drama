# -*- coding: utf-8 -*-
"""热度时间序列(spec 1.2 ref_heat、1.1 第 6 条):同一作品同一榜单一天一行,写入后不改;
曲线按天补齐,缺的天标 gap(控制台关着的日子);「涨得快」由代码按 config 阈值判。"""
from __future__ import annotations

import json
from datetime import date, timedelta

from tools.refs import clock, config


def record(store, wid: int, board: str, day: str, rank: int | None, value: float | None, raw: dict) -> bool:
    with store.write() as c:
        cur = c.execute("INSERT OR IGNORE INTO ref_heat (work_id, board, day, at, rank, heat, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (wid, board, day, clock.now_iso(), rank, value, json.dumps(raw, ensure_ascii=False)))
        return cur.rowcount == 1


def series(store, wid: int, board: str) -> list[dict]:
    rows = {r["day"]: dict(r) for r in store.conn.execute(
        "SELECT day, rank, heat FROM ref_heat WHERE work_id = ? AND board = ? ORDER BY day", (wid, board))}
    if not rows:
        return []
    d, end, out = date.fromisoformat(min(rows)), date.fromisoformat(max(rows)), []
    while d <= end:
        k = d.isoformat()
        out.append({**rows[k], "gap": False} if k in rows else {"day": k, "rank": None, "heat": None, "gap": True})
        d += timedelta(days=1)
    return out


def boards_of(store, wid: int) -> list[str]:
    return [r[0] for r in store.conn.execute("SELECT DISTINCT board FROM ref_heat WHERE work_id = ? ORDER BY board", (wid,))]


def rising(store, wid: int, board: str, day: str) -> str | None:
    t = store.conn.execute("SELECT rank, heat FROM ref_heat WHERE work_id = ? AND board = ? AND day = ?", (wid, board, day)).fetchone()
    if t is None:
        return None
    y_day = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    y = store.conn.execute("SELECT rank, heat FROM ref_heat WHERE work_id = ? AND board = ? AND day = ?", (wid, board, y_day)).fetchone()
    if y is None:
        earlier = store.conn.execute("SELECT 1 FROM ref_heat WHERE work_id = ? AND board = ? AND day < ?", (wid, board, day)).fetchone()
        # 这个来源的这个榜单在 day 之前一条数据都没有(刚开始盯)时,榜上每部都「新」—— 不算新上榜
        board_seen = store.conn.execute(
            "SELECT 1 FROM ref_heat h JOIN ref_work w ON w.id = h.work_id WHERE w.source = (SELECT source FROM ref_work WHERE id = ?)"
            " AND h.board = ? AND h.day < ? LIMIT 1", (wid, board, day)).fetchone()
        if earlier is None and board_seen is not None and t["rank"] is not None and t["rank"] <= config.NEW_TOP:
            return f"新上榜第 {t['rank']} 名"
        return None
    if y["heat"] and t["heat"] is not None and (t["heat"] - y["heat"]) / y["heat"] >= config.GROWTH:
        return f"热度涨 {(t['heat'] - y['heat']) / y['heat']:.0%}"
    if y["rank"] is not None and t["rank"] is not None and y["rank"] - t["rank"] >= config.RANK_JUMP:
        return f"名次升 {y['rank'] - t['rank']} 名"
    return None
