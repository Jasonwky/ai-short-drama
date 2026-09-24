# -*- coding: utf-8 -*-
"""参考帧:按段落出首帧(多图参考合成,目标画幅由剧决定),逐条重绘保留历史。

本版只实现 `first` 参考模式;尾帧从上一镜视频抽取在 P5。
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from tools.drama import repo
from tools.drama.tasks import tasks_for
from tools.gen.planner import plan_first_frame
from tools.serve.panels.common import new_seed, render, rt

router = APIRouter()


@router.get("/episode/{episode_id}/frames")
def page(request: Request, episode_id: int):
    store = rt(request).store
    ep = repo.get(store, "episode", episode_id)
    segs = [{"row": s, "tasks": [t for t in tasks_for(store, "segment", s["id"]) if t["kind"] == "image"]}
            for s in store.conn.execute("SELECT * FROM segment WHERE episode_id = ? ORDER BY number", (episode_id,))]
    return render(request, "frames.html", "frames", episode_id=episode_id, ep=ep, segs=segs, seed=new_seed())


@router.post("/segment/{segment_id}/frame")
def generate(request: Request, segment_id: int, seed: int = Form(...)):
    store = rt(request).store
    plan_first_frame(store, segment_id, seed=seed)
    ep_id = repo.get(store, "segment", segment_id)["episode_id"]
    return RedirectResponse(f"/episode/{ep_id}/frames#seg-{segment_id}", status_code=303)
