# -*- coding: utf-8 -*-
"""大纲:小说原文 → 分集大纲(LLM,人审批后建出各集);已有各集的大纲与原文片段并排可回溯。

原文先进「分批页」:按段落切成不超过 6 万字的几批、按字数分好集数,每批一个按钮(新标签页打开,分批页留着)。
逐批生成,集号接在已有最后一集之后;前一批批准前就跑的后一批,批准时会因输入变了被拒(要重跑)。
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request

from tools.drama import repo
from tools.llm import outline, runs
from tools.serve.panels.common import render, rt

router = APIRouter()


@router.get("/drama/{drama_id}/outline")
def page(request: Request, drama_id: int):
    store = rt(request).store
    repo.get(store, "drama", drama_id)
    eps = store.conn.execute("SELECT * FROM episode WHERE drama_id = ? ORDER BY number", (drama_id,)).fetchall()
    return render(request, "outline.html", "outline", drama_id=drama_id, eps=eps,
                  llm_runs=runs.history(store, "drama", drama_id, "outline"))


@router.post("/drama/{drama_id}/outline/batches")
def batches(request: Request, drama_id: int, novel: str = Form(""), n_episodes: int = Form(...)):
    store = rt(request).store
    repo.get(store, "drama", drama_id)
    parts = outline.batches(novel)
    if not parts:
        raise ValueError("小说原文是空的")
    counts = outline.split_episodes(n_episodes, [sum(len(p) for p in b) for b in parts])
    last = store.conn.execute("SELECT MAX(number) FROM episode WHERE drama_id = ?", (drama_id,)).fetchone()[0] or 0
    rows, start = [], last + 1
    for i, (b, n) in enumerate(zip(parts, counts), 1):
        rows.append({"i": i, "text": "\n".join(b), "paras": len(b), "chars": sum(len(p) for p in b), "n": n,
                     "head": b[0][:40], "tail": b[-1][-40:], "from_ep": start, "to_ep": start + n - 1})
        start += n
    return render(request, "outline_batches.html", "outline", drama_id=drama_id, rows=rows, last=last)
