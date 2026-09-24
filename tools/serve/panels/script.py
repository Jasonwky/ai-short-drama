# -*- coding: utf-8 -*-
"""剧本:按集编辑剧本。本版只有「自带剧本」入口;小说改编 / 梗概生成要 LLM,在 P4。

改剧本 = 该集全部段落作废(`invalidation` 的 script 事件)—— 有已生成产物时先报代价再确认。
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from tools.drama import invalidation, repo, rules
from tools.gen.capability import MINIMAX_H3, QWEN_IMAGE_EDIT_2511
from tools.serve.panels.common import confirm_page, needs_confirm, render, rt

router = APIRouter()
CAPS = {"video_cap": MINIMAX_H3, "image_cap": QWEN_IMAGE_EDIT_2511}


@router.get("/drama/{drama_id}/script")
def page(request: Request, drama_id: int):
    store = rt(request).store
    repo.get(store, "drama", drama_id)
    eps = []
    for e in store.conn.execute("SELECT * FROM episode WHERE drama_id = ? ORDER BY number", (drama_id,)):
        n = store.conn.execute("SELECT COUNT(*) FROM segment WHERE episode_id = ?", (e["id"],)).fetchone()[0]
        lo, hi = rules.expected_segment_range(e["script"]) if e["script"].strip() else (0, 0)
        eps.append({"row": e, "n_segments": n, "range": (lo, hi),
                    "problems": rules.check_episode(e["script"], n) if e["script"].strip() and n else []})
    return render(request, "script.html", "script", drama_id=drama_id, eps=eps)


@router.post("/drama/{drama_id}/episodes")
def add_episode(request: Request, drama_id: int):
    store = rt(request).store
    n = store.conn.execute("SELECT COALESCE(MAX(number), 0) + 1 FROM episode WHERE drama_id = ?",
                           (drama_id,)).fetchone()[0]
    repo.add_episode(store, drama_id, n, "script")
    return RedirectResponse(f"/drama/{drama_id}/script", status_code=303)


@router.post("/episode/{episode_id}/script")
async def save(request: Request, episode_id: int):
    store = rt(request).store
    form = await request.form()
    ep = repo.get(store, "episode", episode_id)
    back = f"/drama/{ep['drama_id']}/script"
    text = str(form.get("script", ""))
    if text == ep["script"]:
        return RedirectResponse(back, status_code=303)
    imp = invalidation.impact(store, "script", episode_id, **CAPS)
    if needs_confirm(imp, form):
        return confirm_page(request, summary=imp.summary(), action=f"/episode/{episode_id}/script",
                            fields={"script": text}, back=back, panel_key="script", drama_id=ep["drama_id"])
    if imp.videos or imp.frames:
        invalidation.apply(store, imp)
    repo.update(store, "episode", episode_id, script=text)
    return RedirectResponse(back, status_code=303)
