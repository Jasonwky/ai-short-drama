# -*- coding: utf-8 -*-
"""冷门对照(spec 2026-11-07-参考库-design.md 2.2):每个第一层特征在同平台、同题材的热门与冷门作品里各占多少。

- **只比第一层的闭集字段与节奏统计**(`ref_unit`);「为什么爆」的自由文本假设不对照;
- 冷门 = 同平台、同标签组(`genres.tag_group`)、**库里已有单元标注**的作品里,热度低于这一组中位数的
  (用户 2026-11-08 定:自动抽样以后再做);热度取最新一天的详情热度,热度为空的不进分组;
  没有详情热度的平台(`config.CONTRAST["by_rank"]`,七猫)按最新一天的最好榜单名次,名次小 = 热;
- 没有冷门样本 → 如实写「无对照」;区分度阈值在 `config.CONTRAST`(暂定)。
"""
from __future__ import annotations

import json
from statistics import median

from tools.drama import pacing
from tools.refs import config, genres, works

NO_COLD = "无对照:库里同平台同类作品还没有冷门样本(用户 2026-11-08 定:自动抽样以后再做,可在热榜手动加冷门作品)"
NO_GROUP = "无对照:这部作品的平台标签认不出题材,分不了组"


def latest_units(refs, work_id: int) -> list[dict]:
    """这部作品每条内容最新的一行单元标注(只许追加,重拆 = 新行),按单元号排。"""
    rows = refs.conn.execute(
        "SELECT * FROM ref_unit WHERE id IN (SELECT MAX(id) FROM ref_unit WHERE work_id = ? GROUP BY content_id)"
        " ORDER BY unit, id", (work_id,)).fetchall()
    return [dict(r) for r in rows]


def features(unit_rows: list[dict]) -> dict:
    """一部作品的第一层统计。flags 是对照用的是 / 否特征(None = 这部作品没法判,如小说没有画面钩子),键是给人看的说法。"""
    beats = {r["unit"]: json.loads(r["fields_json"]) for r in unit_rows}
    n = len(beats)
    st = pacing.stats(beats)
    count = lambda k, v: sum(b[k] == v for b in beats.values())
    visual = [json.loads(r["visual_json"]) for r in sorted(unit_rows, key=lambda r: r["unit"])]
    visual = [v for v in visual if v]
    limit = pacing.THRESHOLDS["max_reversal_gap"]["value"]
    gaps = [*st["reversal_gaps"], st["leading_gap"], *([st["trailing_gap"]] if st["trailing_gap"] is not None else [])]
    reversals = n - count("reversal", "无")
    flags = {
        "前 10 个单元结尾都有钩子": None if st["hook_coverage_first10"] is None else st["hook_coverage_first10"] == 1.0,
        f"每 {limit} 个单元内都有反转(含开头、结尾两段)": (reversals > 0 and max(gaps) <= limit) if n else None,
        "有大反转": count("reversal", "大") > 0 if n else None,
        "「放」多于「压」": count("emotion", "放") > count("emotion", "压") if n else None,
        "有适合放付费点的结尾 / 章末悬念": count("paywall", "是") > 0 if n else None,
        "开头画面有钩子": visual[0]["hook"] == "有" if visual else None,
    }
    return {"units": n, "hook_rate": count("hook", "有") / n if n else None, "reversals": reversals,
            "press_share": count("emotion", "压") / n if n else None, "release_share": count("emotion", "放") / n if n else None,
            "visual_hook_rate": sum(v["hook"] == "有" for v in visual) / len(visual) if visual else None,
            "pacing": st, "flags": flags}


def _score(refs, work_id: int, by_rank: bool) -> float | None:
    """分组用的热度:越大越热。按名次的平台取负的最好名次。没有数据返回 None(不进分组)。"""
    if by_rank:
        r = refs.conn.execute("SELECT MIN(rank) FROM ref_heat WHERE work_id = ? AND board != 'detail' AND rank IS NOT NULL"
                              " AND day = (SELECT MAX(day) FROM ref_heat WHERE work_id = ? AND board != 'detail' AND rank IS NOT NULL)",
                              (work_id, work_id)).fetchone()[0]
        return None if r is None else -float(r)
    r = refs.conn.execute("SELECT heat FROM ref_heat WHERE work_id = ? AND board = 'detail' AND heat IS NOT NULL"
                          " ORDER BY day DESC LIMIT 1", (work_id,)).fetchone()
    return None if r is None else float(r[0])


def groups(refs, source: str, group: str) -> tuple[list[int], list[int]]:
    """(热门, 冷门) 作品编号:同来源、同标签组、有单元标注、有热度的作品,低于中位数的是冷门。"""
    by_rank = source in config.CONTRAST["by_rank"]
    scored = {}
    for w in refs.conn.execute("SELECT * FROM ref_work WHERE source = ? AND id IN (SELECT work_id FROM ref_unit) ORDER BY id",
                               (source,)):
        if genres.tag_group(source, works.tags(dict(w))) != group:
            continue
        s = _score(refs, w["id"], by_rank)
        if s is not None:
            scored[w["id"]] = s
    if not scored:
        return [], []
    mid = median(scored.values())
    return [i for i, s in scored.items() if s >= mid], [i for i, s in scored.items() if s < mid]


def _distinct(a: float, b: float) -> str:
    d = abs(a - b)
    return "高" if d >= config.CONTRAST["high"] else "中" if d >= config.CONTRAST["mid"] else "低"


def table(refs, work_id: int) -> dict:
    w = works.get(refs, work_id)
    group = genres.tag_group(w["source"], works.tags(w))
    if group is None:
        return {"rows": [], "note": NO_GROUP}
    hot, cold = groups(refs, w["source"], group)
    if not cold:
        return {"rows": [], "note": NO_COLD}
    flags = {i: features(latest_units(refs, i))["flags"] for i in hot + cold}
    rows = []
    for name in flags[(hot + cold)[0]]:
        h = [flags[i][name] for i in hot if flags[i][name] is not None]
        c = [flags[i][name] for i in cold if flags[i][name] is not None]
        if not h or not c:
            rows.append({"feature": name, "hot": f"{sum(h)}/{len(h)}", "cold": f"{sum(c)}/{len(c)}", "distinct": "无样本"})
            continue
        rows.append({"feature": name, "hot": f"{sum(h)}/{len(h)}", "cold": f"{sum(c)}/{len(c)}",
                     "distinct": _distinct(sum(h) / len(h), sum(c) / len(c))})
    note = (f"同平台「{group}」里有标注的作品:热门 {len(hot)} 部、冷门 {len(cold)} 部(热度中位数分);"
            "只比第一层的闭集字段与节奏,「为什么爆」的假设不对照")
    return {"rows": rows, "note": note}
