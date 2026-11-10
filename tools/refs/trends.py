# -*- coding: utf-8 -*-
"""跨作品趋势(spec 2026-11-07-参考库-design.md 2.6):只用代码统计**已批准、未被取代**的拆解卡的闭集字段,
按卡片批准的月份分(批准时间 = ref_card.created_at),不让 LLM 参与。"""
from __future__ import annotations

import json
from collections import Counter

FIELDS = ("genre", "payoff", "conflict", "hook_type")      # 拆解卡上的闭集字段(ref_card 任务的 schema)


def by_month(refs, source: str | None = None) -> list[dict]:
    sql = ("SELECT c.created_at, c.review_json FROM ref_card c JOIN ref_work w ON w.id = c.work_id"
           " WHERE c.state = 'approved'")
    args: list = []
    if source:
        sql += " AND w.source = ?"
        args.append(source)
    months: dict[str, dict] = {}
    for r in refs.conn.execute(sql + " ORDER BY c.created_at", args):
        m = months.setdefault(r["created_at"][:7], {"month": r["created_at"][:7], "n": 0, **{k: Counter() for k in FIELDS}})
        review = json.loads(r["review_json"])
        m["n"] += 1
        for k in FIELDS:
            m[k][review[k]] += 1
    return [months[k] for k in sorted(months)]
