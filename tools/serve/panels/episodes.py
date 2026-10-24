# -*- coding: utf-8 -*-
"""逐集:一部剧每集的大纲 / 剧本 / 分镜到了哪一步;批量出剧本(两态)、停掉 / 继续队列、新建空集
(spec 2026-10-24-逐集批量出剧本分镜-design.md 第 1、2、9 节)。"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from tools.drama import repo
from tools.llm import batch, llm_queue, script_writer
from tools.serve.panels.common import render, rt

router = APIRouter()
LABELS = {"todo": "要出", "has": "跳过 · 已有剧本", "in_flight": "跳过 · 在途", "blocked": "跳过 · 缺内容"}
MAX_NEW = 200


def _ids(store, drama_id: int) -> list[int]:
    return [r[0] for r in store.conn.execute("SELECT id FROM episode WHERE drama_id = ?", (drama_id,))]


@router.get("/drama/{drama_id}/episodes")
def page(request: Request, drama_id: int):
    store = rt(request).store
    repo.get(store, "drama", drama_id)
    rows = batch.episode_rows(store, drama_id)
    cells = [r[k] for r in rows for k in ("script", "storyboard")]
    return render(request, "episodes.html", "episodes", drama_id=drama_id, rows=rows,
                  review=sum(c["state"] == "proposed" for c in cells),
                  inflight=any(c["state"] in ("queued", "running") for c in cells),
                  waiting=llm_queue.waiting_count(store, _ids(store, drama_id)), paused=llm_queue.paused(store))


@router.post("/drama/{drama_id}/episodes/batch")
async def batch_scripts(request: Request, drama_id: int):
    """两态:第一次出确认页(逐集归类 + 预计耗时);带 confirm=1 才入队,只认确认页列出的集。"""
    runtime = rt(request)
    form = await request.form()
    if form.get("confirm") == "1":
        raw = str(form.get("minutes", ""))
        if not raw.isdigit():
            raise ValueError(f"目标时长要填整数分钟,填的是 {raw!r}")
        ids = [int(x) for x in str(form.get("ids", "")).split(",") if x.strip().isdigit()]
        batch.confirm(runtime.store, drama_id, ids, int(raw))
        runtime.pump_queue()
        return RedirectResponse(f"/drama/{drama_id}/episodes", status_code=303)
    items = batch.plan(runtime.store, drama_id)
    todo = [i for i in items if i.status == "todo"]
    return render(request, "episodes_batch.html", "episodes", drama_id=drama_id, items=items, todo=todo,
                  est=batch.estimate_minutes(len(todo)), minutes=script_writer.MINUTES, labels=LABELS)


@router.post("/drama/{drama_id}/episodes/cancel")
def cancel(request: Request, drama_id: int):
    store = rt(request).store
    llm_queue.cancel_waiting(store, _ids(store, drama_id))
    return RedirectResponse(f"/drama/{drama_id}/episodes", status_code=303)


@router.post("/drama/{drama_id}/episodes/resume")
def resume(request: Request, drama_id: int):
    runtime = rt(request)
    llm_queue.resume(runtime.store)
    runtime.pump_queue()
    return RedirectResponse(f"/drama/{drama_id}/episodes", status_code=303)


@router.post("/drama/{drama_id}/episodes/new")
def new_episodes(request: Request, drama_id: int, n: int = Form(...)):
    """一次建 n 个空集(原创剧手写大纲用),集号接在已有最后一集之后。"""
    store = rt(request).store
    repo.get(store, "drama", drama_id)
    if not 1 <= n <= MAX_NEW:
        raise ValueError(f"一次建 1–{MAX_NEW} 集,填的是 {n}")
    last = store.conn.execute("SELECT COALESCE(MAX(number), 0) FROM episode WHERE drama_id = ?", (drama_id,)).fetchone()[0]
    for k in range(1, n + 1):
        repo.add_episode(store, drama_id, last + k, "script")
    return RedirectResponse(f"/drama/{drama_id}/episodes", status_code=303)
