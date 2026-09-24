# -*- coding: utf-8 -*-
"""资产:角色 / 场景 / 道具三栏。编辑描述、生成参考图(灰底整幅替换)、历史版本可挑。

生成走任务表:这里只规划(写一条参数定死的 task),由后台 worker 提交云机。
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from tools.drama import repo
from tools.drama.tasks import tasks_for
from tools.gen.assets import SPEC, ensure_canvas, plan_asset_image
from tools.serve.panels.common import new_seed, render, rt

router = APIRouter()

# 面板上可编辑的文字字段(都在 repo 的写入白名单里)
FIELDS = {
    "character": [("appearance", "外貌"), ("styling", "服装造型"), ("voice_desc", "声音描述(视频里的音色靠它)"),
                  ("final_prompt", "生成描述(英文效果更稳)")],
    "scene": [("location", "地点"), ("time", "时间"), ("lighting", "光线"), ("final_prompt", "生成描述")],
    "prop": [("description", "描述"), ("final_prompt", "生成描述")],
}
KIND_LABEL = {"character": "角色", "scene": "场景", "prop": "道具"}
_ADD = {"character": repo.add_character, "scene": repo.add_scene, "prop": repo.add_prop}


@router.get("/drama/{drama_id}/assets")
def page(request: Request, drama_id: int):
    store = rt(request).store
    repo.get(store, "drama", drama_id)
    cols = []
    for table in ("character", "scene", "prop"):
        items = []
        for row in store.conn.execute(f"SELECT * FROM {table} WHERE drama_id = ? ORDER BY id", (drama_id,)):
            items.append({"row": row, "current": row[SPEC[table][0]], "tasks": tasks_for(store, table, row["id"])})
        cols.append({"table": table, "label": KIND_LABEL[table], "entries": items, "fields": FIELDS[table]})
    return render(request, "assets.html", "assets", drama_id=drama_id, cols=cols, seed=new_seed())


@router.post("/drama/{drama_id}/assets")
def add(request: Request, drama_id: int, kind: str = Form(...), name: str = Form("")):
    if kind not in _ADD:
        raise ValueError(f"未知资产类型 {kind}")
    if not name.strip():
        raise ValueError("名字不能为空 —— 分镜里要用 @名字 引用它")
    try:
        _ADD[kind](rt(request).store, drama_id, name.strip())
    except sqlite3.IntegrityError as e:
        raise ValueError(f"这部剧里已经有叫「{name.strip()}」的{KIND_LABEL[kind]}了") from e
    return RedirectResponse(f"/drama/{drama_id}/assets", status_code=303)


@router.post("/asset/{table}/{row_id}")
async def edit(request: Request, table: str, row_id: int):
    if table not in FIELDS:
        raise ValueError(f"未知资产类型 {table}")
    form = await request.form()
    store = rt(request).store
    repo.update(store, table, row_id, **{k: str(form.get(k, "")) for k, _ in FIELDS[table]})
    return RedirectResponse(f"/drama/{repo.get(store, table, row_id)['drama_id']}/assets", status_code=303)


@router.post("/asset/{table}/{row_id}/generate")
def generate(request: Request, table: str, row_id: int, seed: int = Form(...)):
    runtime = rt(request)
    ensure_canvas(runtime.data_root)
    plan_asset_image(runtime.store, table, row_id, seed=seed)
    return RedirectResponse(f"/drama/{repo.get(runtime.store, table, row_id)['drama_id']}/assets", status_code=303)
