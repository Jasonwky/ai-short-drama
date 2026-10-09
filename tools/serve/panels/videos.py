# -*- coding: utf-8 -*-
"""视频:H3 ref2va(20 步档)。就绪清单、预计耗时(来自实测)、队列状态、失败原因、历史版本可挑。

字幕也在这里:对着当前视频对齐台词(云机 whisper),逐句显示当前字幕的起止时间;判卷不过的原因写在对齐记录上。
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from tools.cut import subtitles
from tools.drama import repo
from tools.drama.tasks import tasks_for
from tools.gen.capability import MINIMAX_H3
from tools.gen import presets
from tools.gen.align import align_problems, plan_align
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
        est = (MINIMAX_H3.est_seconds(MINIMAX_H3.frames_for(s["duration_sec"]), presets.video_steps(store)) / 60
               if s["duration_sec"] else None)
        tasks = tasks_for(store, "segment", s["id"])
        sub = runtime.data_root / s["subtitle_path"] if s["subtitle_path"] else None
        segs.append({"row": s, "checks": readiness(runtime, s["id"]), "est_min": est,
                     "tasks": [t for t in tasks if t["kind"] == "video"],
                     "align_tasks": [t for t in tasks if t["kind"] == "align"],
                     "align_problems": align_problems(store, s["id"]),
                     "cues": subtitles.parse_srt(sub.read_text(encoding="utf-8")) if sub and sub.is_file() else []})
    return render(request, "videos.html", "videos", episode_id=episode_id, ep=ep, segs=segs, seed=new_seed())


@router.post("/segment/{segment_id}/video")
def generate(request: Request, segment_id: int, seed: int = Form(...)):
    store = rt(request).store
    plan_video(store, segment_id, seed=seed)
    ep_id = repo.get(store, "segment", segment_id)["episode_id"]
    return RedirectResponse(f"/episode/{ep_id}/videos#seg-{segment_id}", status_code=303)


@router.post("/segment/{segment_id}/align")
def align(request: Request, segment_id: int):
    store = rt(request).store
    plan_align(store, segment_id)
    ep_id = repo.get(store, "segment", segment_id)["episode_id"]
    return RedirectResponse(f"/episode/{ep_id}/videos#seg-{segment_id}", status_code=303)
