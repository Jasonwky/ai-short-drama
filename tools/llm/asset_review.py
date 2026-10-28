# -*- coding: utf-8 -*-
"""资产图审(spec 2026-10-28-成片质量闸门-第一期-design.md 第 6.4、6.5 节):每张资产图 = 代码检查 + AI 逐维度检查 + 人定稿。

`items` 汇总一部剧所有资产图的现状(纯读库);`plan_checks` 给还没查过的图排 AI 检查(同一张图只排一次,失败不重试);
`stage` 是全剧流程栏里的「⑤ 资产图审」。代码检查要读图,只在页面上算(`assetscan.cached`)。
"""
from __future__ import annotations

from tools.drama import approval
from tools.drama.db import Store
from tools.llm import asset_check, llm_queue, runs

SLOTS = (("character", "image"), ("character", "fourview"), ("scene", "image"), ("prop", "image"))
IN_FLIGHT = ("pending", "queued", "running", "waiting_host")


def task_of(table: str) -> str:
    return f"asset_check_{table}"


def _run(store: Store, table: str, row_id: int, slot: str, path: str):
    """这张图最近一次 AI 检查(参数里的路径就是这张图)。"""
    for r in reversed(runs.history(store, table, row_id, task=task_of(table))):
        p = runs.params(r)
        if p.get("slot") == slot and p.get("path") == path:
            return r
    return None


def _queued(store: Store, task: str, target_type: str, target_id: int, params: dict) -> bool:
    return any(llm_queue.params(r) == params for r in store.conn.execute(
        "SELECT * FROM llm_queue WHERE state = 'waiting' AND task = ? AND target_type = ? AND target_id = ?",
        (task, target_type, target_id)))


def items(store: Store, d: int) -> list[dict]:
    out = []
    for table, slot in SLOTS:
        col = approval.field(table, slot)
        for r in store.conn.execute(f"SELECT * FROM {table} WHERE drama_id = ? ORDER BY id", (d,)).fetchall():
            flying = store.conn.execute(
                f"SELECT 1 FROM task WHERE target_type = ? AND target_id = ? AND json_extract(params_json, '$.writes') = ?"
                f" AND state IN ({','.join('?' * len(IN_FLIGHT))})", (table, r["id"], col, *IN_FLIGHT)).fetchone()
            if not r[col]:
                if flying or (table, slot) != ("character", "fourview") or r["turnaround_path"]:
                    out.append({"table": table, "id": r["id"], "name": r["name"], "slot": slot, "path": None,
                                "flying": bool(flying)})
                continue
            run = _run(store, table, r["id"], slot, r[col])
            out.append({"table": table, "id": r["id"], "name": r["name"], "slot": slot, "path": r[col],
                        "label": approval.LABEL[(table, slot)], "approval": approval.current(store, table, r["id"], slot),
                        "run": run, "out": runs.output(run) if run is not None and run["state"] in ("proposed", "approved") else None,
                        "queued": _queued(store, task_of(table), table, r["id"], {"slot": slot, "path": r[col]}),
                        "flying": bool(flying)})
    return out


def ai_failed(item: dict) -> list[str]:
    return asset_check.failed(item["out"]) if item.get("out") else []


def plan_checks(store: Store, d: int) -> list[str]:
    """有图、没定稿、这张图还没查过(任何状态)也没在排的,排一次 AI 检查。"""
    did = []
    for it in items(store, d):
        if not it["path"] or it["approval"] is not None or it["run"] is not None or it["queued"]:
            continue
        llm_queue.enqueue(store, task_of(it["table"]), it["table"], it["id"], {"slot": it["slot"], "path": it["path"]})
        did.append(f"drama {d} 排了「{it['name']}」{it['label']}的 AI 检查")
    return did


def drama_run(store: Store, d: int):
    """当前这组资产图最近一次全剧对照(参数 key 就是这组图)。"""
    key = asset_check.sheet_key(store, d)
    for r in reversed(runs.history(store, "drama", d, task="asset_check_drama")):
        if runs.params(r).get("key") == key:
            return r
    return None


def plan_drama_check(store: Store, d: int) -> list[str]:
    """单张都查完(没有在查 / 待查的)之后,这组图排一次全剧对照;排过(任何状态)就不再排。"""
    state, _ = summary(store, d)
    key = asset_check.sheet_key(store, d)
    if state not in ("ready", "done") or drama_run(store, d) is not None or _queued(store, "asset_check_drama", "drama", d, {"key": key}):
        return []
    n = sum(1 for t, col in (("character", "turnaround_path"), ("scene", "image_path"))
            for _ in store.conn.execute(f"SELECT 1 FROM {t} WHERE drama_id = ? AND {col} IS NOT NULL", (d,)))
    if n < 2:
        return []
    llm_queue.enqueue(store, "asset_check_drama", "drama", d, {"key": key})
    return [f"drama {d} 排了资产图全剧对照"]


def drama_issues(store: Store, d: int) -> dict[tuple[str, int], list[str]]:
    """全剧对照挑出的毛病,挂到相关资产上:{(table, id): ["角色撞脸:……"]}。"""
    run = drama_run(store, d)
    if run is None or run["state"] not in ("proposed", "approved"):
        return {}
    ctx_sheets = {"角色正面图": "character", "场景空镜": "scene"}
    out: dict[tuple[str, int], list[str]] = {}
    names = {(t, r["id"]): r["name"] for t in ctx_sheets.values()
             for r in store.conn.execute(f"SELECT id, name FROM {t} WHERE drama_id = ? ORDER BY id", (d,))}
    order = {t: [r[0] for r in store.conn.execute(
        f"SELECT id FROM {t} WHERE drama_id = ? AND {col} IS NOT NULL ORDER BY id", (d,))]
        for t, col in (("character", "turnaround_path"), ("scene", "image_path"))}
    for it in runs.output(run)["issues"]:
        table = ctx_sheets[it["sheet"]]
        ids = [order[table][c - 1] for c in it["cells"] if 0 < c <= len(order[table])]
        who = "、".join(names[(table, i)] for i in ids)
        for i in ids:
            out.setdefault((table, i), []).append(f"{it['dim']}({who}):{it['evidence']}")
    return out


def summary(store: Store, d: int) -> tuple[str, str]:
    """(状态, 说明) 给流程栏用:blocked 还有图没出 / working AI 在查 / ready 等人定稿 / done 全部定稿。"""
    its = items(store, d)
    if not its:
        return "blocked", "还没有资产"
    missing = [it for it in its if not it["path"]]
    have = [it for it in its if it["path"]]
    done = [it for it in have if it["approval"] is not None]
    checking = [it for it in have if it["approval"] is None and (it["queued"] or (it["run"] is not None and it["run"]["state"] == "running")
                                                                or it["run"] is None)]
    detail = f"定稿 {len(done)}/{len(its)}" + (f" · 还没出图 {len(missing)}" if missing else "")
    if checking:
        return "working", detail + f" · AI 在查 / 待查 {len(checking)}"
    if len(done) < len(have):
        return "ready", detail + f" · 等你定稿 {len(have) - len(done)} 张"
    if missing:
        return "blocked", detail
    return "done", detail
