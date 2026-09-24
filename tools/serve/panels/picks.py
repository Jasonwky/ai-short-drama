# -*- coding: utf-8 -*-
"""「用这一版」:把某次生成设为当前版。下游会作废时先报代价再确认(`tools.drama.versions`)。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from tools.drama import repo
from tools.drama.versions import confirm_pick, plan_pick
from tools.gen.capability import MINIMAX_H3, QWEN_IMAGE_EDIT_2511
from tools.serve.panels.common import confirm_page, needs_confirm, rt

router = APIRouter()


@router.post("/task/{task_id}/pick")
async def pick(request: Request, task_id: int):
    store = rt(request).store
    form = await request.form()
    back = str(form.get("next") or "/")
    if not back.startswith("/") or back.startswith("//"):
        raise ValueError("非法的返回地址")
    p = plan_pick(store, task_id, video_cap=MINIMAX_H3, image_cap=QWEN_IMAGE_EDIT_2511)
    if needs_confirm(p.impact, form):
        if p.target_type == "segment":
            ctx = {"panel_key": "frames", "episode_id": repo.get(store, "segment", p.target_id)["episode_id"]}
        else:
            ctx = {"panel_key": "assets", "drama_id": repo.get(store, p.target_type, p.target_id)["drama_id"]}
        return confirm_page(request, summary=p.summary(), action=f"/task/{task_id}/pick",
                            fields={"next": back}, back=back, **ctx)
    confirm_pick(store, p)
    return RedirectResponse(back, status_code=303)
