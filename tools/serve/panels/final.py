# -*- coding: utf-8 -*-
"""成片:拼接 + 显式标识 + 隐式元数据 + 溯源报告,导出后读回自检(设计 §6.8–6.9)。

服务提供者编码是★必答:没有就不导出,不用默认值。导出在后台跑,页面有在途导出时自动刷新。
字幕在「视频」面板对齐,导出时烧录进两版;有台词却没有字幕的段落挡住导出。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from tools.cut import export, media
from tools.drama import repo
from tools.serve.panels.common import render, rt

router = APIRouter()


@router.get("/episode/{episode_id}/final")
def page(request: Request, episode_id: int):
    runtime = rt(request)
    store = runtime.store
    ep = repo.get(store, "episode", episode_id)
    segs = store.conn.execute("SELECT * FROM segment WHERE episode_id = ? ORDER BY number", (episode_id,)).fetchall()
    rows = [{"row": r, "checks": json.loads(r["checks_json"]) if r["checks_json"] else []}
            for r in export.list_for(store, episode_id)]
    return render(request, "final.html", "final", episode_id=episode_id, ep=ep, segs=segs,
                  problems=export.readiness(store, runtime.data_root, episode_id),
                  producer=export.saved_producer(store), exports=rows, label_text=media.LABEL_TEXT)


@router.post("/episode/{episode_id}/export")
def start(request: Request, episode_id: int, producer: str = Form("")):
    rt(request).start_export(episode_id, producer)
    return RedirectResponse(f"/episode/{episode_id}/final", status_code=303)
