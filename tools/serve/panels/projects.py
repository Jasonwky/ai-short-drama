# -*- coding: utf-8 -*-
"""立项:剧列表 + 新建剧(画幅建后不可改 —— 数据库触发器兜底;风格预设是数据)。"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from tools.drama import repo
from tools.drama.styles import STYLES, get_style
from tools.serve.panels.common import render, rt

router = APIRouter()


@router.get("/")
def page(request: Request):
    dramas = rt(request).store.conn.execute(
        "SELECT d.*, (SELECT COUNT(*) FROM episode e WHERE e.drama_id = d.id) AS n_episodes "
        "FROM drama d ORDER BY d.id DESC").fetchall()
    return render(request, "projects.html", "projects", dramas=dramas, style_list=STYLES)


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
