# -*- coding: utf-8 -*-
"""大纲:小说原文 → 分集大纲(LLM,人审批后建出各集);已有各集的大纲与原文片段并排可回溯。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from tools.drama import repo
from tools.llm import runs
from tools.serve.panels.common import render, rt

router = APIRouter()


@router.get("/drama/{drama_id}/outline")
def page(request: Request, drama_id: int):
    store = rt(request).store
    repo.get(store, "drama", drama_id)
    eps = store.conn.execute("SELECT * FROM episode WHERE drama_id = ? ORDER BY number", (drama_id,)).fetchall()
    return render(request, "outline.html", "outline", drama_id=drama_id, eps=eps,
                  llm_runs=runs.history(store, "drama", drama_id, "outline"))
