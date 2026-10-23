# -*- coding: utf-8 -*-
"""分镜:段落表 + 段落编辑(实时代码校验、reference_mode、绑定)。

校验与保存用**同一套**代码规则(`rules.check_segment`),前端只是提前调用它,不另写一份。
改动已出过图/视频的段落 → `segment_prompt` 事件,先报代价再确认。
中间关键帧是一张列表(一段 0–3 张):表单带每张的行号,靠行号认「改了哪张、删了哪张、加了哪张」——
只动关键帧时只作废本段视频,改了提示词的那几张图一并作废,别的图不动。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from tools.drama import blocking, framing, invalidation, repo, rules
from tools.drama.readiness import KEY_GAP_SEC, KEY_MARGIN_SEC, KEY_MAX, key_frame_index
from tools.gen.capability import MINIMAX_H3, QWEN_IMAGE_EDIT_2511
from tools.llm import runs
from tools.serve.panels.common import confirm_page, needs_confirm, readiness, render, rt

router = APIRouter()
CAPS = {"video_cap": MINIMAX_H3, "image_cap": QWEN_IMAGE_EDIT_2511}
MODES = [("first", "首帧(钉住第一帧)"), ("first_last", "首尾帧(两头都钉住)"), ("last", "尾帧(钉住最后一帧)"),
         ("text_only", "纯文字(不支持:实测场景和道具守不住)")]
TEXT_FIELDS = ("description", "image_prompt", "last_image_prompt", "video_prompt", "reference_mode")
KEY_FORM = ("key_id", "key_time", "key_prompt")      # 每张关键帧一组(新加的那组行号为空);要删的勾 key_delete
VIDEO_ONLY_FIELDS = {"video_prompt", "duration_sec"}  # 只动这些:作废视频,参考帧保留(描述里的台词决定首帧槽位,不在此列)


def _seconds(text: str, what: str = "时长") -> float | None:
    text = text.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError as e:
        raise ValueError(f"{what}要填数字(秒),填的是 {text!r}") from e


def _key_form(form, existing: dict) -> tuple[list[tuple[int | None, float, str]], set[int]]:
    """表单里的关键帧 [(行号,新加的为 None;秒;提示词)] 与要删的行号。空白的「加一张」不算。"""
    ids, times, prompts = ([str(v) for v in form.getlist(k)] for k in KEY_FORM)
    if not len(ids) == len(times) == len(prompts):
        raise ValueError("关键帧表单不完整(行号 / 时刻 / 提示词的个数对不上)")
    rows = []
    for kid, t, prompt in zip(ids, times, prompts):
        if not kid and not t.strip() and not prompt.strip():
            continue
        sec = _seconds(t, "关键帧时刻")
        if sec is None:
            raise ValueError("关键帧要填第几秒(不要这张就勾「删除这张」)")
        if kid and int(kid) not in existing:
            raise ValueError(f"关键帧 {kid} 不是本段的")
        rows.append((int(kid) if kid else None, sec, prompt))
    drop = {int(x) for x in form.getlist("key_delete")}
    if not drop <= set(existing):
        raise ValueError(f"关键帧 {sorted(drop - set(existing))} 不是本段的")
    return rows, drop


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
    frames = MINIMAX_H3.frames_for(seg["duration_sec"]) if seg["duration_sec"] else None
    keys = repo.keyframes(store, segment_id)
    marks = []                  # 时间轴示意:各锚点落在第几帧(占全片的百分比)
    if frames:
        if seg["reference_mode"] in ("first", "first_last"):
            marks.append(("首帧", 0, 0))
        if seg["reference_mode"] in ("last", "first_last"):
            marks.append(("尾帧", frames - 1, 100))
        for n, k in enumerate(keys, 1):
            idx = key_frame_index(k["time_sec"], MINIMAX_H3)
            marks.append((f"关键帧 {n}" if len(keys) > 1 else "关键帧", idx, min(100, round(100 * idx / (frames - 1)))))
    saved = {e["character"]: e for e in blocking.parse(seg["blocking_json"])}
    names = [c["name"] for c in assets["characters"]]
    rows = [{"character": n, **{"pose": "站", "facing": "镜头", "side": "中", "holding": blocking.NONE}, **saved.get(n, {}),
             "facing_options": [*[m for m in names if m != n], *blocking.FIXED_FACING],
             "holding_options": [blocking.NONE, *[p["name"] for p in assets["props"]]]} for n in names]
    return render(request, "segment_edit.html", "storyboard", episode_id=seg["episode_id"], seg=seg, modes=MODES,
                  bk=blocking, shot_types=framing.SHOT_TYPES, blocking_rows=rows, blocking_phrase=blocking.phrase(list(saved.values())) if saved else "",
                  frames=frames, marks=marks, margin=KEY_MARGIN_SEC, gap=KEY_GAP_SEC, keys=keys, key_max=KEY_MAX,
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
    existing = {k["id"]: k for k in repo.keyframes(store, segment_id)}
    rows, drop = _key_form(form, existing)
    added = [r for r in rows if r[0] is None]
    kept = [r for r in rows if r[0] is not None and r[0] not in drop]
    moved = [r for r in kept if r[1] != existing[r[0]]["time_sec"]]
    reprompted = [r for r in kept if r[2] != existing[r[0]]["image_prompt"]]

    bindings_changed = (scene_id != seg["scene_id"] or chars != [c["id"] for c in assets["characters"]]
                        or props != [p["id"] for p in assets["props"]])
    fields_changed = {k for k in new if new[k] != seg[k]}
    keys_changed = bool(added or drop or moved or reprompted)
    back = f"/segment/{segment_id}/edit"
    if not fields_changed and not bindings_changed and not keys_changed:
        return RedirectResponse(back, status_code=303)
    if not fields_changed and not bindings_changed:   # 只动了关键帧:作废本段视频(改了提示词的连那几张图);首帧 / 尾帧不动
        imp = invalidation.keyframe_edit(store, seg, images=sum(1 for r in reprompted if existing[r[0]]["frame_path"]),
                                         **CAPS)
    elif fields_changed <= VIDEO_ONLY_FIELDS and not bindings_changed and not keys_changed:
        imp = invalidation.impact(store, "segment_video", segment_id, **CAPS)
    else:
        imp = invalidation.impact(store, "segment_prompt", segment_id, **CAPS)
    if needs_confirm(imp, form):
        fields = {k: str(form.get(k, "")) for k in (*TEXT_FIELDS, "duration_sec", "scene_id")}
        fields |= {"character_ids": [str(c) for c in chars], "prop_ids": [str(p) for p in props]}
        fields |= {k: [str(v) for v in form.getlist(k)] for k in (*KEY_FORM, "key_delete")}
        return confirm_page(request, summary=imp.summary(), action=back, fields=fields, back=back,
                            panel_key="storyboard", episode_id=seg["episode_id"])
    if imp.videos or imp.frames:
        invalidation.apply(store, imp)
    repo.update(store, "segment", segment_id, **new)
    repo.set_scene(store, segment_id, scene_id)
    repo.bind_characters(store, segment_id, chars)
    repo.bind_props(store, segment_id, props)
    for kid in drop:
        repo.delete_keyframe(store, kid)
    for kid, sec, prompt in kept:
        changes = {"time_sec": sec} if sec != existing[kid]["time_sec"] else {}
        if prompt != existing[kid]["image_prompt"]:
            changes |= {"image_prompt": prompt, "frame_path": None}       # 旧图是按旧提示词出的
        if changes:
            repo.update(store, "segment_keyframe", kid, **changes)
    for _, sec, prompt in added:
        repo.add_keyframe(store, segment_id, sec, prompt)
    return RedirectResponse(back, status_code=303)


@router.post("/segment/{segment_id}/blocking")
async def save_blocking(request: Request, segment_id: int):
    """保存场记表:本段每个角色一行(表单按行同序提交)。闭集值、覆盖本段角色由 blocking.problems 判。"""
    store = rt(request).store
    form = await request.form()
    cols = [form.getlist(k) for k in ("character", "pose", "facing", "side", "holding")]
    entries = [dict(zip(("character", "pose", "facing", "side", "holding"), map(str, row))) for row in zip(*cols)]
    assets = repo.segment_assets(store, segment_id)
    bad = blocking.problems(entries, [c["name"] for c in assets["characters"]], [p["name"] for p in assets["props"]])
    if bad:
        raise ValueError("场记表:" + ";".join(bad))
    fields = {"blocking_json": json.dumps(entries, ensure_ascii=False)}
    if "shot_type" in form:                     # 景别(spec 2026-10-23-不穿帮验收 第 2 节),空 = 没填
        shot = str(form["shot_type"])
        if shot and shot not in framing.SHOT_TYPES:
            raise ValueError(f"景别「{shot}」不在 {'/'.join(framing.SHOT_TYPES)} 里")
        fields["shot_type"] = shot
    repo.update(store, "segment", segment_id, **fields)
    return RedirectResponse(f"/segment/{segment_id}/edit", status_code=303)


@router.post("/api/segment/check")
def live_check(description: str = Form(""), duration_sec: str = Form("")):
    try:
        dur = _seconds(duration_sec)
    except ValueError as e:
        return JSONResponse({"problems": [str(e)], "ok_msg": ""})
    problems = rules.check_segment(description, dur, MINIMAX_H3)
    return JSONResponse({"problems": problems, "ok_msg": "时长与子镜头结构合规"})
