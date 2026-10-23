# -*- coding: utf-8 -*-
"""剧本:按集编辑剧本。三个入口汇到同一份剧本:自带剧本(直接编辑)、梗概生成、小说大纲改写(后两者走 LLM,人审批才写回)。

改剧本 = 该集全部段落作废(`invalidation` 的 script 事件)—— 有已生成产物时先报代价再确认。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from tools.drama import invalidation, repo, rules
from tools.gen.capability import MINIMAX_H3, QWEN_IMAGE_EDIT_2511
from tools.llm import critique, rewrite, runs
from tools.serve.panels.common import confirm_page, needs_confirm, render, rt

router = APIRouter()
CAPS = {"video_cap": MINIMAX_H3, "image_cap": QWEN_IMAGE_EDIT_2511}


def _unsynced(store, episode_id: int) -> list[str]:
    """批准过的局部改写里没对上任何段落的(本集有段落时才有意义):提示人分镜没跟着改。"""
    descs = [r[0] for r in store.conn.execute("SELECT description FROM segment WHERE episode_id = ?", (episode_id,))]
    if not descs:
        return []
    out = []
    for r in runs.history(store, "episode", episode_id, "rewrite"):
        if r["state"] != "approved":
            continue
        text = runs.output(r)["candidates"][json.loads(r["decision_json"])["pick"]]["text"]
        lines = [line for _, line in rules.dialogue_lines(text)]
        if any(text.strip() in d for d in descs) or any(f"「{line}」" in d for d in descs for line in lines):
            continue
        out.append(text.strip()[:40])
    return out


@router.get("/drama/{drama_id}/script")
def page(request: Request, drama_id: int):
    store = rt(request).store
    repo.get(store, "drama", drama_id)
    eps = []
    for e in store.conn.execute("SELECT * FROM episode WHERE drama_id = ? ORDER BY number", (drama_id,)):
        n = store.conn.execute("SELECT COUNT(*) FROM segment WHERE episode_id = ?", (e["id"],)).fetchone()[0]
        lo, hi = rules.expected_segment_range(e["script"]) if e["script"].strip() else (0, 0)
        eps.append({"row": e, "n_segments": n, "range": (lo, hi),
                    "problems": rules.check_episode(e["script"], n) if e["script"].strip() and n else [],
                    "adopted": critique.adopted(store, e["id"]), "unsynced": _unsynced(store, e["id"]),
                    "llm_runs": [r for r in runs.history(store, "episode", e["id"])
                                 if r["task"].startswith("script_") or r["task"] in ("critique", "rewrite")]})
    return render(request, "script.html", "script", drama_id=drama_id, eps=eps, roles=list(critique.ROLES),
                  intents=rewrite.INTENTS, adoption=critique.adoption(store, drama_id))


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
