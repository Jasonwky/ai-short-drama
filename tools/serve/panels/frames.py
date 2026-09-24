# -*- coding: utf-8 -*-
"""参考帧:按段落出首帧(多图参考合成,目标画幅由剧决定),逐条重绘保留历史。

本版只实现 `first` 参考模式。也可以用上一段视频的最后一帧作本段首帧(尾帧衔接,设计 §6.6):
本段已有视频时会让它作废,先报代价再确认。
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from tools.cut import tailframe
from tools.drama import repo
from tools.drama.tasks import tasks_for
from tools.gen.planner import plan_first_frame
from tools.serve.panels.common import confirm_page, needs_confirm, new_seed, render, rt

router = APIRouter()


@router.get("/episode/{episode_id}/frames")
def page(request: Request, episode_id: int):
    store = rt(request).store
    ep = repo.get(store, "episode", episode_id)
    rows = store.conn.execute("SELECT * FROM segment WHERE episode_id = ? ORDER BY number", (episode_id,)).fetchall()
    segs = [{"row": s, "prev": rows[i - 1] if i else None,
             "tasks": [t for t in tasks_for(store, "segment", s["id"]) if t["kind"] == "image"]}
            for i, s in enumerate(rows)]
    return render(request, "frames.html", "frames", episode_id=episode_id, ep=ep, segs=segs, seed=new_seed())


@router.post("/segment/{segment_id}/frame")
def generate(request: Request, segment_id: int, seed: int = Form(...)):
    store = rt(request).store
    plan_first_frame(store, segment_id, seed=seed)
    ep_id = repo.get(store, "segment", segment_id)["episode_id"]
    return RedirectResponse(f"/episode/{ep_id}/frames#seg-{segment_id}", status_code=303)


@router.post("/segment/{segment_id}/tailframe")
async def tail(request: Request, segment_id: int):
    """segment_id 是**上一段**:取它视频的最后一帧,作下一段的首帧。"""
    runtime = rt(request)
    form = await request.form()
    plan = tailframe.plan_tail(runtime.store, runtime.data_root, segment_id)
    ep_id = repo.get(runtime.store, "segment", segment_id)["episode_id"]
    if needs_confirm(plan.impact, form):
        return confirm_page(request, summary="换成上一段视频的尾帧:" + plan.summary(),
                            action=f"/segment/{segment_id}/tailframe", fields={}, back=f"/episode/{ep_id}/frames",
                            panel_key="frames", episode_id=ep_id)
    tailframe.confirm_tail(runtime.store, runtime.data_root, plan)
    return RedirectResponse(f"/episode/{ep_id}/frames#seg-{plan.target_id}", status_code=303)
