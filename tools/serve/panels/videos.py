# -*- coding: utf-8 -*-
"""视频:H3 ref2va(20 步档)。就绪清单、预计耗时(来自实测)、队列状态、失败原因、历史版本可挑。"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from tools.drama import repo
from tools.drama.tasks import tasks_for
from tools.gen.capability import MINIMAX_H3
from tools.gen.planner import plan_video
from tools.serve.panels.common import new_seed, readiness, render, rt

router = APIRouter()


@router.get("/episode/{episode_id}/videos")
def page(request: Request, episode_id: int):
    runtime = rt(request)
    store = runtime.store
    ep = repo.get(store, "episode", episode_id)
    segs = []
    for s in store.conn.execute("SELECT * FROM segment WHERE episode_id = ? ORDER BY number", (episode_id,)):
        est = (MINIMAX_H3.est_seconds(MINIMAX_H3.frames_for(s["duration_sec"])) / 60
               if s["duration_sec"] else None)
        segs.append({"row": s, "checks": readiness(runtime, s["id"]), "est_min": est,
                     "tasks": [t for t in tasks_for(store, "segment", s["id"]) if t["kind"] == "video"]})
    return render(request, "videos.html", "videos", episode_id=episode_id, ep=ep, segs=segs, seed=new_seed())


@router.post("/segment/{segment_id}/video")
def generate(request: Request, segment_id: int, seed: int = Form(...)):
    store = rt(request).store
    plan_video(store, segment_id, seed=seed)
    ep_id = repo.get(store, "segment", segment_id)["episode_id"]
    return RedirectResponse(f"/episode/{ep_id}/videos#seg-{segment_id}", status_code=303)
