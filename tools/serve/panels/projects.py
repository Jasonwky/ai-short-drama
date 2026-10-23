# -*- coding: utf-8 -*-
"""立项:剧列表 + 新建剧(画幅建后不可改 —— 数据库触发器兜底;风格预设是数据)+ 工程导出 / 导入。"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from tools.drama import repo
from tools.drama.styles import STYLES, get_style
from tools.llm import premise, runs
from tools.serve.panels.bundles import listing
from tools.serve.panels.common import render, rt

router = APIRouter()


@router.get("/")
def page(request: Request):
    runtime = rt(request)
    dramas = runtime.store.conn.execute(
        "SELECT d.*, (SELECT COUNT(*) FROM episode e WHERE e.drama_id = d.id) AS n_episodes "
        "FROM drama d ORDER BY d.id DESC").fetchall()
    return render(request, "projects.html", "projects", dramas=dramas, style_list=STYLES,
                  bundles=listing(runtime.data_root))


@router.get("/drama/{drama_id}/premise")
def premise_page(request: Request, drama_id: int):
    """脑爆立项(spec 2026-10-23-剧集脑爆系统-design.md 第 1 节):一句话想法 → 几个真不一样的方案,人逐字段挑 / 拼。"""
    store = rt(request).store
    drama = repo.get(store, "drama", drama_id)
    current = premise.load(store, drama_id)
    return render(request, "premise.html", "projects", drama_id=drama_id, d=drama,
                  current=premise.render(current) if current else "",
                  runs=runs.history(store, "drama", drama_id, "premise"), n_range=premise.N_RANGE)


@router.post("/dramas")
def create(request: Request, title: str = Form(""), aspect_ratio: str = Form(...), style: str = Form(...)):
    if not title.strip():
        raise ValueError("剧名不能为空")
    try:
        get_style(style)
    except KeyError as e:
        raise ValueError(str(e.args[0])) from e
    did = repo.create_drama(rt(request).store, title.strip(), aspect_ratio, style)
    return RedirectResponse(f"/drama/{did}/script", status_code=303)
