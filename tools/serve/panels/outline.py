# -*- coding: utf-8 -*-
"""大纲:小说原文 → 分集大纲(LLM,人审批后建出各集);已有各集的大纲与原文片段并排可回溯。

原文先进「分批页」:按段落切成不超过 6 万字的几批、按字数分好集数,每批一个按钮(新标签页打开,分批页留着)。
逐批生成,集号接在已有最后一集之后;前一批批准前就跑的后一批,批准时会因输入变了被拒(要重跑)。
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from tools.drama import pacing, repo
from tools.llm import beats, outline, runs
from tools.serve.panels.common import render, rt

router = APIRouter()
LLM_TASKS = ("outline", "beats", "outline_variants")


@router.get("/drama/{drama_id}/outline")
def page(request: Request, drama_id: int):
    store = rt(request).store
    repo.get(store, "drama", drama_id)
    eps = store.conn.execute("SELECT * FROM episode WHERE drama_id = ? ORDER BY number", (drama_id,)).fetchall()
    beat_map = beats.load(store, drama_id)
    rows = [r for t, tid in (("drama", drama_id), *(("episode", e["id"]) for e in eps))
            for r in runs.history(store, t, tid) if r["task"] in LLM_TASKS]
    return render(request, "outline.html", "outline", drama_id=drama_id, eps=eps, beat_map=beat_map,
                  pacing_hints=pacing.hints(beat_map), llm_runs=sorted(rows, key=lambda r: r["id"]))


@router.post("/drama/{drama_id}/outline/batches")
def batches(request: Request, drama_id: int, novel: str = Form(""), n_episodes: int = Form(...)):
    store = rt(request).store
    repo.get(store, "drama", drama_id)
    parts = outline.plan(novel, n_episodes)
    if not parts:
        raise ValueError("小说原文是空的")
    last = store.conn.execute("SELECT MAX(number) FROM episode WHERE drama_id = ?", (drama_id,)).fetchone()[0] or 0
    rows, start = [], last + 1
    for i, (b, n) in enumerate(parts, 1):
        rows.append({"i": i, "text": "\n".join(b), "paras": len(b), "chars": sum(len(p) for p in b), "n": n,
                     "head": b[0][:40], "tail": b[-1][-40:], "from_ep": start, "to_ep": start + n - 1})
        start += n
    return render(request, "outline_batches.html", "outline", drama_id=drama_id, rows=rows, last=last,
                  max_episodes=outline.MAX_EPISODES)


@router.post("/episode/{episode_id}/outline")
def save_outline(request: Request, episode_id: int, outline: str = Form("")):
    """手写本集大纲(原创剧):这集已有剧本就拒(大纲改了剧本会悄悄过期,同单集变体);改了就清掉这集的节拍。"""
    store = rt(request).store
    ep = repo.get(store, "episode", episode_id)
    if ep["script"].strip():
        raise ValueError(f"第 {ep['number']} 集已有剧本 —— 大纲改了剧本会悄悄过期;要改大纲先清掉这集剧本")
    if outline.strip() != ep["outline"]:
        repo.update(store, "episode", episode_id, outline=outline.strip(), beats_json="")
    return RedirectResponse(f"/drama/{ep['drama_id']}/outline", status_code=303)
