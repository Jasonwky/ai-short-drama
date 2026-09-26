# -*- coding: utf-8 -*-
"""增量补全:参考帧 / 视频 / 资产面板上的「补全缺的」按钮都 POST 到这里。

先出确认页(每条的状态、原因、种子、估算总耗时),人点确认才建任务 —— 单条视频约 15 分钟,不许一键就交。
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from tools.drama import repo
from tools.gen.assets import ensure_canvas
from tools.gen.fill import KINDS, confirm_fill, plan_fill
from tools.serve.panels.common import render, rt

router = APIRouter()
STATUS = {"has": "已有", "in_flight": "在途", "blocked": "不能做", "todo": "要做"}


def _back(store, kind: str, scope_id: int) -> tuple[str, dict]:
    if KINDS[kind][0] == "drama":                              # 资产图、四视图都在资产面板上补
        return f"/drama/{scope_id}/assets", {"panel_key": "assets", "drama_id": scope_id}
    repo.get(store, "episode", scope_id)
    panel = {"subtitles": "videos", "lastframes": "frames"}.get(kind, kind)   # 字幕在视频面板、尾帧在参考帧面板上补
    return f"/episode/{scope_id}/{panel}", {"panel_key": panel, "episode_id": scope_id}


@router.post("/fill/{kind}/{scope_id}")
async def fill(request: Request, kind: str, scope_id: int):
    if kind not in KINDS:
        raise ValueError(f"未知的补全类型 {kind}")
    runtime = rt(request)
    form = await request.form()
    raw = str(form.get("base_seed", "")).strip()
    if not raw.lstrip("-").isdigit():
        raise ValueError(f"起始种子要填整数,填的是 {raw!r}")
    base_seed = int(raw)
    back, ctx = _back(runtime.store, kind, scope_id)
    if form.get("confirm") != "1":
        plan = plan_fill(runtime.store, kind, scope_id)
        seeds = {} if kind == "subtitles" else {        # 对齐是确定性的,没有种子
            (i.target_type, i.target_id): base_seed + n if i.seed is None else i.seed for n, i in enumerate(plan.todo)}
        return render(request, "fill_confirm.html", ctx.pop("panel_key"), plan=plan, seeds=seeds, status=STATUS,
                      base_seed=base_seed, action=f"/fill/{kind}/{scope_id}", back=back, **ctx)
    targets = []
    for t in form.getlist("t"):
        ttype, _, tid = str(t).partition(":")
        targets.append((ttype, int(tid)))
    if kind == "assets":
        ensure_canvas(runtime.data_root)
    confirm_fill(runtime.store, kind, scope_id, targets, base_seed)
    return RedirectResponse(back, status_code=303)
