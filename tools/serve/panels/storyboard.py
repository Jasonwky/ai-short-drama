# -*- coding: utf-8 -*-
"""分镜:段落表 + 段落编辑(实时代码校验、reference_mode、绑定)。

校验与保存用**同一套**代码规则(`rules.check_segment`),前端只是提前调用它,不另写一份。
改动已出过图/视频的段落 → `segment_prompt` 事件,先报代价再确认。
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from tools.drama import invalidation, repo, rules
from tools.gen.capability import MINIMAX_H3, QWEN_IMAGE_EDIT_2511
from tools.llm import runs
from tools.serve.panels.common import confirm_page, needs_confirm, readiness, render, rt

router = APIRouter()
CAPS = {"video_cap": MINIMAX_H3, "image_cap": QWEN_IMAGE_EDIT_2511}
MODES = [("first", "首帧(钉住第一帧)"), ("first_last", "首尾帧(两头都钉住)"), ("last", "尾帧(钉住最后一帧)"),
         ("text_only", "纯文字(视频生成未实现)")]
TEXT_FIELDS = ("description", "image_prompt", "last_image_prompt", "video_prompt", "reference_mode")


def _seconds(text: str) -> float | None:
    text = text.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError as e:
        raise ValueError(f"时长要填数字(秒),填的是 {text!r}") from e


def _names(store, sql, sid):
    return [r[0] for r in store.conn.execute(sql, (sid,))]


@router.get("/episode/{episode_id}/storyboard")
def page(request: Request, episode_id: int):
    runtime = rt(request)
    store = runtime.store
    ep = repo.get(store, "episode", episode_id)
    segs = []
    for s in store.conn.execute("SELECT * FROM segment WHERE episode_id = ? ORDER BY number", (episode_id,)):
        checks = readiness(runtime, s["id"])
        segs.append({
            "row": s,
            "scene": repo.get(store, "scene", s["scene_id"])["name"] if s["scene_id"] else "",
            "chars": _names(store, "SELECT c.name FROM segment_character sc JOIN character c ON c.id = sc.character_id "
                                   "WHERE sc.segment_id = ? ORDER BY sc.position", s["id"]),
            "props": _names(store, "SELECT p.name FROM segment_prop sp JOIN prop p ON p.id = sp.prop_id "
                                   "WHERE sp.segment_id = ? ORDER BY sp.position", s["id"]),
            "missing": [c.msg for c in checks if not c.ok],
        })
    return render(request, "storyboard.html", "storyboard", episode_id=episode_id, ep=ep, segs=segs,
                  llm_runs=runs.history(store, "episode", episode_id, "storyboard"))


@router.post("/episode/{episode_id}/segments")
def add(request: Request, episode_id: int):
    store = rt(request).store
    n = store.conn.execute("SELECT COALESCE(MAX(number), 0) + 1 FROM segment WHERE episode_id = ?",
                           (episode_id,)).fetchone()[0]
    sid = repo.add_segment(store, episode_id, n)
    return RedirectResponse(f"/segment/{sid}/edit", status_code=303)


@router.get("/segment/{segment_id}/edit")
def edit_page(request: Request, segment_id: int):
    runtime = rt(request)
    store = runtime.store
    seg = repo.get(store, "segment", segment_id)
    drama_id = repo.get(store, "episode", seg["episode_id"])["drama_id"]
    assets = repo.segment_assets(store, segment_id)
    all_of = {t: store.conn.execute(f"SELECT id, name FROM {t} WHERE drama_id = ? ORDER BY id", (drama_id,)).fetchall()
              for t in ("character", "scene", "prop")}
    return render(request, "segment_edit.html", "storyboard", episode_id=seg["episode_id"], seg=seg, modes=MODES,
                  all_of=all_of, bound_chars={c["id"] for c in assets["characters"]},
                  bound_props={p["id"] for p in assets["props"]}, checks=readiness(runtime, segment_id))


@router.post("/segment/{segment_id}/edit")
async def save(request: Request, segment_id: int):
    store = rt(request).store
    form = await request.form()
    seg = repo.get(store, "segment", segment_id)
    assets = repo.segment_assets(store, segment_id)
    new = {k: str(form.get(k, "")) for k in TEXT_FIELDS}
    new["duration_sec"] = _seconds(str(form.get("duration_sec", "")))
    scene_id = int(form["scene_id"]) if form.get("scene_id") else None
    chars = [int(x) for x in form.getlist("character_ids")]
    props = [int(x) for x in form.getlist("prop_ids")]

    changed = (any(new[k] != seg[k] for k in new) or scene_id != seg["scene_id"]
               or chars != [c["id"] for c in assets["characters"]] or props != [p["id"] for p in assets["props"]])
    back = f"/segment/{segment_id}/edit"
    if not changed:
        return RedirectResponse(back, status_code=303)
    imp = invalidation.impact(store, "segment_prompt", segment_id, **CAPS)
    if needs_confirm(imp, form):
        fields = {k: str(form.get(k, "")) for k in (*TEXT_FIELDS, "duration_sec", "scene_id")}
        fields |= {"character_ids": [str(c) for c in chars], "prop_ids": [str(p) for p in props]}
        return confirm_page(request, summary=imp.summary(), action=back, fields=fields, back=back,
                            panel_key="storyboard", episode_id=seg["episode_id"])
    if imp.videos or imp.frames:
        invalidation.apply(store, imp)
    repo.update(store, "segment", segment_id, **new)
    repo.set_scene(store, segment_id, scene_id)
    repo.bind_characters(store, segment_id, chars)
    repo.bind_props(store, segment_id, props)
    return RedirectResponse(back, status_code=303)


@router.post("/api/segment/check")
def live_check(description: str = Form(""), duration_sec: str = Form("")):
    try:
        dur = _seconds(duration_sec)
    except ValueError as e:
        return JSONResponse({"problems": [str(e)], "ok_msg": ""})
    problems = rules.check_segment(description, dur, MINIMAX_H3)
    return JSONResponse({"problems": problems, "ok_msg": "时长与子镜头结构合规"})
