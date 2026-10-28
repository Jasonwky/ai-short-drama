# -*- coding: utf-8 -*-
"""全剧流程:剧本 → ①全剧审·剧本 → ②AI 出资产表 → ③全剧审·资产 → 出图 / 声音 / 拆分镜 → ④全剧审·分镜
(spec 2026-10-25-全剧审与资产表-design.md 第 0、3、4 节)。

- `status` 算各关现在在哪(纯读),「逐集」面板顶上那一栏就是把它画出来;
- `advance` 只**排**任务(LLM 进 `llm_queue`,GPU 建 task),从不替人批准;按剧开启(`drama.flow_auto`);
- 防空转:一条 LLM 任务对**同一份输入**已经有过运行(不管成败、批没批)就不再自动排;失败的不自动重试;
  资产的某张图**从没出过任务**才自动排 —— 否则「修改没生效」「判卷一直不过」会被每轮 worker 反复排。
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from tools.drama import art, repo
from tools.drama.db import Store
from tools.gen.align import align_problems, plan_align
from tools.gen.assets import SPEC as ASSET_SPEC, asset_problems, fourview_problems, locked_seed, plan_asset_image, plan_fourview
from tools.llm import asset_review, batch, llm_queue, runs, service
from tools.llm.batch import busy

REVIEW = "drama_review"
SCRIPT_FIX = (*batch.SCRIPT_TASKS, "script_revise", "rewrite")
BOARD_FIX = ("storyboard", "storyboard_revise")
STATE_LABEL = {"blocked": "未就绪", "ready": "该跑了", "queued": "排队中", "running": "生成中", "proposed": "待你审",
               "fixing": "修改处理中", "stuck": "卡住了", "failed": "失败", "passed": "通过", "done": "完成",
               "working": "进行中"}
IN_FLIGHT = ("pending", "queued", "running", "waiting_host")


@dataclass
class Stage:
    key: str
    title: str
    state: str
    detail: str = ""
    run: int | None = None
    changed: bool = False             # 通过之后输入又变了(不自动重审,给「再审一次」)
    extra: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return STATE_LABEL[self.state]


def _episodes(store: Store, d: int) -> list:
    return store.conn.execute("SELECT * FROM episode WHERE drama_id = ? ORDER BY number", (d,)).fetchall()


def _drama_runs(store: Store, task: str, d: int, stage: str | None = None) -> list:
    rows = store.conn.execute("SELECT * FROM llm_run WHERE task = ? AND target_type = 'drama' AND target_id = ?"
                              " ORDER BY id", (task, d)).fetchall()
    return [r for r in rows if stage is None or runs.params(r).get("stage") == stage]


def _queued(store: Store, task: str, target_type: str, target_id: int, params: dict | None = None) -> bool:
    for r in store.conn.execute("SELECT * FROM llm_queue WHERE state = 'waiting' AND task = ? AND target_type = ?"
                                " AND target_id = ?", (task, target_type, target_id)):
        if params is None or llm_queue.params(r) == params:
            return True
    return False


def _sha(store: Store, task: str, target_id: int, params: dict) -> str | None:
    """这条任务现在的输入指纹;前提不满足为 None。"""
    try:
        return service.prepare(store, task, target_id, params).sha
    except (ValueError, KeyError):
        return None


def tried(store: Store, task: str, target_type: str, target_id: int, params: dict) -> bool:
    """对现在这份输入已经跑过(任何状态)。"""
    sha = _sha(store, task, target_id, params)
    return sha is None or store.conn.execute(
        "SELECT 1 FROM llm_run WHERE task = ? AND target_type = ? AND target_id = ? AND input_sha = ?",
        (task, target_type, target_id, sha)).fetchone() is not None


def _busy_any(store: Store, eps: list, tasks: tuple[str, ...]) -> bool:
    return any(busy(store, e["id"], tasks) for e in eps)


def _scripts(store: Store, eps: list) -> Stage:
    missing = [e["number"] for e in eps if not e["script"].strip()]
    if not eps:
        return Stage("scripts", "逐集剧本", "blocked", "还没有集")
    if missing:
        return Stage("scripts", "逐集剧本", "blocked", f"第 {'、'.join(map(str, missing))} 集还没剧本")
    if _busy_any(store, eps, SCRIPT_FIX):
        return Stage("scripts", "逐集剧本", "working", "有剧本或剧本修改在生成 / 待你审")
    return Stage("scripts", "逐集剧本", "done", f"{len(eps)} 集都有剧本")


def _review(store: Store, d: int, stage: str, key: str, title: str, blocked: str, fixing: bool,
            after: int = 0) -> Stage:
    """全剧审一关。after:只看这条运行编号之后的(资产审只认最近一次资产表批准之后的)。"""
    params = {"stage": stage}
    if _queued(store, REVIEW, "drama", d, params):
        return Stage(key, title, "queued")
    rows = [r for r in _drama_runs(store, REVIEW, d, stage) if r["id"] > after]
    last = rows[-1] if rows else None
    if last is not None and last["state"] in ("running", "proposed"):
        return Stage(key, title, last["state"], run=last["id"])
    approved = [r for r in rows if r["state"] == "approved"]
    sha = _sha(store, REVIEW, d, params)
    if last is not None and last["state"] == "failed" and (not approved or last["id"] > approved[-1]["id"]):
        return Stage(key, title, "failed", (last["error"] or "")[:200], run=last["id"])
    if approved:
        a = approved[-1]
        n = len(runs.decision(a)["adopt"])
        if n == 0:          # 通过就是通过:之后前一关又有改动(比如统一称呼的修改)不把它打回「未就绪」
            return Stage(key, title, "passed", "没有采纳的问题", run=a["id"], changed=sha is not None and sha != a["input_sha"])
        if fixing:
            return Stage(key, title, "fixing", f"采纳了 {n} 条,修改还在处理", run=a["id"])
        if blocked:
            return Stage(key, title, "blocked", blocked, run=a["id"])
        if sha == a["input_sha"]:
            return Stage(key, title, "stuck", f"采纳了 {n} 条,但修改都没写进去 —— 看看修改记录,或手动再审一次", run=a["id"])
        return Stage(key, title, "ready", f"采纳的 {n} 条已处理,再审一次", run=a["id"])
    return Stage(key, title, "blocked", blocked) if blocked else Stage(key, title, "ready")


def status(store: Store, d: int) -> list[Stage]:
    eps = _episodes(store, d)
    s1 = _scripts(store, eps)
    r1 = _review(store, d, "script", "r1", "① 全剧审·剧本", "" if s1.state == "done" else "等逐集剧本都完成",
                 fixing=_busy_any(store, eps, SCRIPT_FIX))
    casts = _drama_runs(store, "cast", d)
    approved_cast = [r for r in casts if r["state"] == "approved"]
    if _queued(store, "cast", "drama", d):
        c2 = Stage("cast", "② AI 出资产表", "queued")
    elif casts and casts[-1]["state"] in ("running", "proposed"):
        c2 = Stage("cast", "② AI 出资产表", casts[-1]["state"], run=casts[-1]["id"])
    elif r1.state != "passed" and not approved_cast:
        c2 = Stage("cast", "② AI 出资产表", "blocked", "等①通过(一条都不采纳)")
    elif not approved_cast and not art.of(repo.get(store, "drama", d)).era:      # 待定但不可跳过(spec 2026-10-28 第 2 节)
        c2 = Stage("cast", "② AI 出资产表", "blocked", "先填美术设定(时代必填)")
    elif casts and casts[-1]["state"] == "failed":
        c2 = Stage("cast", "② AI 出资产表", "failed", (casts[-1]["error"] or "")[:200], run=casts[-1]["id"])
    elif approved_cast:
        c2 = Stage("cast", "② AI 出资产表", "done", f"批准过 {len(approved_cast)} 次", run=approved_cast[-1]["id"])
    else:
        c2 = Stage("cast", "② AI 出资产表", "ready")
    cast_busy = c2.state in ("queued", "running", "proposed")
    last_cast = approved_cast[-1]["id"] if approved_cast else 0
    # 资产审拿剧本当参照:统一称呼等剧本修改没处理完就先等,否则批完修改、剧本一变,这一轮就批不了(2026-10-27 实战)
    r3_wait = ("等资产表批准" if not approved_cast or cast_busy
               else "等剧本修改处理完(资产审拿剧本当参照)" if _busy_any(store, eps, SCRIPT_FIX) else "")
    r3 = _review(store, d, "assets", "r3", "③ 全剧审·资产", r3_wait, fixing=cast_busy, after=last_cast)
    final = r3.state == "passed"
    media = _media(store, d, final)
    state, detail = asset_review.summary(store, d)
    a5 = Stage("a5", "⑤ 资产图审", state if final else "blocked", detail if final else "等资产定稿(③一条都不采纳)")
    boards = _boards(store, d, eps, final)
    r4 = _review(store, d, "storyboard", "r4", "④ 全剧审·分镜", "" if boards.state == "done" else "等全集分镜完成",
                 fixing=_busy_any(store, eps, BOARD_FIX))
    return [s1, r1, c2, r3, media, a5, boards, r4]


def _never_tried(store: Store, table: str, row_id: int, writes: str) -> bool:
    return store.conn.execute("SELECT 1 FROM task WHERE target_type = ? AND target_id = ?"
                              " AND json_extract(params_json, '$.writes') = ?", (table, row_id, writes)).fetchone() is None


def _in_flight(store: Store, table: str, row_id: int) -> bool:
    return store.conn.execute(f"SELECT 1 FROM task WHERE target_type = ? AND target_id = ? AND state IN"
                              f" ({','.join('?' * len(IN_FLIGHT))})", (table, row_id, *IN_FLIGHT)).fetchone() is not None


def _media(store: Store, d: int, final: bool) -> Stage:
    total = have = flying = 0
    for table in ("character", "scene", "prop"):
        for r in store.conn.execute(f"SELECT * FROM {table} WHERE drama_id = ?", (d,)):
            total += 1 + (table == "character")
            have += bool(r[ASSET_SPEC[table][0]]) + (table == "character" and bool(r["fourview_path"]))
            flying += _in_flight(store, table, r["id"])
    voices = store.conn.execute("SELECT COUNT(*), SUM(voice_desc != '') FROM character WHERE drama_id = ?", (d,)).fetchone()
    detail = f"图 {have}/{total}(在出 {flying})· 声音 {voices[1] or 0}/{voices[0]}"
    if not final:
        return Stage("media", "出图 / 声音", "blocked", "等资产定稿(③一条都不采纳)· " + detail)
    if flying:
        return Stage("media", "出图 / 声音", "working", detail)
    return Stage("media", "出图 / 声音", "done" if have == total and voices[1] == voices[0] else "ready", detail)


def _boards(store: Store, d: int, eps: list, final: bool) -> Stage:
    have = [e for e in eps if store.conn.execute("SELECT 1 FROM segment WHERE episode_id = ?", (e["id"],)).fetchone()]
    detail = f"{len(have)}/{len(eps)} 集有分镜"
    if eps and len(have) == len(eps) and not _busy_any(store, eps, BOARD_FIX):
        return Stage("boards", "逐集分镜", "done", detail)
    if _busy_any(store, eps, BOARD_FIX):
        return Stage("boards", "逐集分镜", "working", detail)
    if not final:
        return Stage("boards", "逐集分镜", "blocked", "等资产定稿 · " + detail)
    gaps = {e["number"]: batch.storyboard_gaps(store, e["id"]) for e in eps if e not in have}
    gaps = {n: g for n, g in gaps.items() if g}
    if gaps:
        return Stage("boards", "逐集分镜", "blocked",
                     detail + " · 缺资产:" + ";".join(f"第 {n} 集 {'、'.join(g)}" for n, g in list(gaps.items())[:3]))
    return Stage("boards", "逐集分镜", "ready", detail)


def advance(store: Store, d: int) -> list[str]:
    """按现在的状态排下一步(幂等)。返回做了什么(进 journal)。没开自动推进就什么都不做。"""
    if not repo.get(store, "drama", d)["flow_auto"]:
        return []
    st = {s.key: s for s in status(store, d)}
    did: list[str] = []

    def llm(task: str, target_type: str, target_id: int, params: dict, what: str) -> None:
        if not _queued(store, task, target_type, target_id, params) and not tried(store, task, target_type, target_id, params):
            llm_queue.enqueue(store, task, target_type, target_id, params)
            did.append(f"drama {d} 排了{what}")

    for key, stage in (("r1", "script"), ("r3", "assets"), ("r4", "storyboard")):
        if st[key].state == "ready":
            llm(REVIEW, "drama", d, {"stage": stage}, f"全剧审·{stage}")
    if st["cast"].state == "ready":
        llm("cast", "drama", d, {"notes": []}, "AI 出资产表")
    if st["media"].state == "ready":
        did += plan_media(store, d)
        for (cid,) in store.conn.execute("SELECT id FROM character WHERE drama_id = ? AND voice_desc = ''", (d,)):
            llm("voice", "character", cid, {}, f"角色 {cid} 的声音")
    if st["boards"].state in ("ready", "blocked") and st["r3"].state == "passed":
        for e in _episodes(store, d):
            if store.conn.execute("SELECT 1 FROM segment WHERE episode_id = ?", (e["id"],)).fetchone():
                continue
            if busy(store, e["id"], BOARD_FIX) or batch.storyboard_gaps(store, e["id"]):
                continue
            llm("storyboard", "episode", e["id"], {}, f"第 {e['number']} 集拆分镜")
    did += plan_checks(store, d)
    if st["r3"].state == "passed":                       # 资产图出来就排 AI 检查;单张都查完再排全剧对照(spec 2026-10-28 第 6.5 节)
        did += asset_review.plan_checks(store, d) + asset_review.plan_drama_check(store, d)
    return did


def plan_checks(store: Store, d: int) -> list[str]:
    """出完的视频排一次对齐 / 语音检查(spec 2026-10-28 第 3 节;导出要它)。同一条视频排过(任何状态)就不再排。"""
    did = []
    rows = store.conn.execute("SELECT s.id, s.number, s.video_path, e.number AS ep FROM segment s"
                              " JOIN episode e ON e.id = s.episode_id WHERE e.drama_id = ? AND s.video_path IS NOT NULL"
                              " ORDER BY e.number, s.number", (d,)).fetchall()
    for r in rows:
        seen = store.conn.execute("SELECT 1 FROM task WHERE kind = 'align' AND target_type = 'segment' AND target_id = ?"
                                  " AND json_extract(params_json, '$.video') = ?", (r["id"], r["video_path"])).fetchone()
        if seen is None and not align_problems(store, r["id"]):
            plan_align(store, r["id"])
            did.append(f"drama {d} 排了第 {r['ep']} 集段落 {r['number']} 的语音检查")
    return did


def plan_media(store: Store, d: int) -> list[str]:
    """缺图且**从没出过**的资产排正面图 / 空镜;有正面图、从没出过四视图的角色排四视图。出过(含失败)的交给人。"""
    did = []
    for table in ("character", "scene", "prop"):
        writes = ASSET_SPEC[table][0]
        for r in store.conn.execute(f"SELECT * FROM {table} WHERE drama_id = ? ORDER BY id", (d,)).fetchall():
            if not r[writes] and _never_tried(store, table, r["id"], writes) and not asset_problems(store, table, r["id"]):
                locked = locked_seed(store, table, r["id"])
                plan_asset_image(store, table, r["id"], seed=locked if locked is not None else random.randrange(1, 2**31))
                did.append(f"drama {d} 排了 {table}「{r['name']}」的图")
            if (table == "character" and r[writes] and not r["fourview_path"]
                    and _never_tried(store, table, r["id"], "fourview_path") and not fourview_problems(store, r["id"])):
                plan_fourview(store, r["id"], seed=random.randrange(1, 2**31))
                did.append(f"drama {d} 排了角色「{r['name']}」的四视图")
    return did


def wall(store: Store, d: int) -> list[dict]:
    """资产图墙:每个资产的正面图 / 四视图,没图的写最近一次任务的状态。"""
    out = []
    for table, label in (("character", "角色"), ("scene", "场景"), ("prop", "道具")):
        for r in store.conn.execute(f"SELECT * FROM {table} WHERE drama_id = ? ORDER BY id", (d,)):
            slots = [("正面" if table == "character" else "图", ASSET_SPEC[table][0])]
            if table == "character":
                slots.append(("四视图", "fourview_path"))
            cells = []
            for name, writes in slots:
                t = store.conn.execute("SELECT state, error FROM task WHERE target_type = ? AND target_id = ?"
                                       " AND json_extract(params_json, '$.writes') = ? ORDER BY id DESC LIMIT 1",
                                       (table, r["id"], writes)).fetchone()
                why = "" if r[writes] or t else "、".join(
                    (fourview_problems(store, r["id"]) if writes == "fourview_path" else asset_problems(store, table, r["id"])))
                cells.append({"slot": name, "path": r[writes] or "", "task": t["state"] if t else "",
                              "error": (t["error"] or "")[:120] if t else "", "why": why})
            out.append({"table": table, "label": label, "id": r["id"], "name": r["name"], "cells": cells})
    return out

