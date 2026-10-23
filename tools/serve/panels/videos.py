# -*- coding: utf-8 -*-
"""视频:H3 ref2va。就绪清单、预计耗时(来自实测)、队列状态、失败原因、历史版本可挑。

两档:定稿(20 步,或当前视频预设改过的步数)、草稿(10 步,约省一半时间)。少于 20 步出的视频(草稿档、预设、
改参重跑改的都算 —— `dispatch.steps_of` 读实际交出去的图)在卡片上标出来。

白屏检测命中的视频卡片上可以「确认不是白屏(放行)」:理由★必填,只对这条视频的 sha256 有效,换了视频自动失效。

看片(连续性保障,spec 2026-10-22):每段列出自动切镜的时刻与切后那一帧、上一段末帧与本段首帧的接缝;
人看过点「看过,没有穿帮」(只对这条视频有效),没确认的段落拦导出。

字幕也在这里:对着当前视频对齐台词(云机 whisper),逐句显示当前字幕的起止时间;判卷不过的原因写在对齐记录上。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from tools.cut import blankscan, checklist, cuts, keycheck, media, review, subtitles
from tools.drama import blocking, repo
from tools.llm import runs, vision_check
from tools.drama.tasks import producer, tasks_for
from tools.gen.capability import MINIMAX_H3
from tools.gen import estimate, presets
from tools.gen.align import align_problems, plan_align
from tools.gen.planner import plan_video
from tools.gen.workflows import DRAFT_STEPS, H3_STEPS
from tools.serve import aligner
from tools.serve.dispatch import FEW_STEPS_NOTE, sha256_file, steps_of
from tools.serve.panels.common import new_seed, readiness, render, rt

router = APIRouter()


def _override(store, root, seg) -> dict | None:
    """这段当前视频的白屏放行(按 sha256 对),带上溯源报告里那一行。"""
    o = blankscan.override_for(store, root, seg)
    return None if o is None else {**o, "note": blankscan.override_note(seg["number"], blankscan.snapshot(o))}


def _review(store, root, seg) -> dict | None:
    """这段当前视频的看片确认,带上勾了几项、是不是按现行清单勾全的(v13 的老确认不算)。"""
    r = review.review_for(store, root, seg)
    if r is None:
        return None
    return {**r, "checked": len(review.snapshot(r)["checks"]), "complete": review.complete(r)}


def _vision(store, seg, root) -> dict | None:
    """这条视频最近一次视觉抽检(参数里记着视频 sha256,换了视频就不认)。"""
    sha = sha256_file(root / seg["video_path"])
    for row in reversed(runs.history(store, "segment", seg["id"], task="vision_check")):
        if runs.params(row).get("video_sha256") != sha:
            continue
        if row["state"] in ("proposed", "approved"):
            out = runs.output(row)
            return {"run": row, "mismatches": vision_check.mismatches(blocking.parse(seg["blocking_json"]), out)}
        return {"run": row, "mismatches": None}
    return None


@router.get("/episode/{episode_id}/videos")
def page(request: Request, episode_id: int):
    runtime = rt(request)
    store = runtime.store
    ep = repo.get(store, "episode", episode_id)
    segs, b = [], estimate.basis(store)
    preset_steps = presets.video_steps(store)
    full = preset_steps or H3_STEPS
    prev = None                                     # 上一段的切镜报告(接缝要它的末帧)
    for s in store.conn.execute("SELECT * FROM segment WHERE episode_id = ? ORDER BY number", (episode_id,)):
        frames = MINIMAX_H3.frames_for(s["duration_sec"]) if s["duration_sec"] else None
        est = b.video(MINIMAX_H3, frames, full) if frames else None
        draft = b.video(MINIMAX_H3, frames, DRAFT_STEPS) if frames else None
        tasks = tasks_for(store, "segment", s["id"])
        videos = [t for t in tasks if t["kind"] == "video"]
        done = {t["id"]: steps_of(t) for t in videos if t["state"] == "done"}
        few = {tid: n for tid, n in done.items() if n is not None and n < H3_STEPS}
        tags = {t["id"]: ("草稿" if json.loads(t["params_json"]).get("steps") else "少步数") + f" · {few[t['id']]} 步"
                for t in videos if t["id"] in few}
        cur = producer(store, "video", s["video_path"]) if s["video_path"] else None
        sub = runtime.data_root / s["subtitle_path"] if s["subtitle_path"] else None
        has_video = bool(s["video_path"]) and (runtime.data_root / s["video_path"]).is_file()
        blank = blankscan.cached(runtime.data_root, s["video_path"]) if has_video else None   # 只读缓存:导出时才查
        cut, cut_error = None, None
        if has_video:                               # 与 keycheck 一样当场算、按 sha256 缓存(一条约 1 秒)
            try:
                cut = cuts.check(runtime.data_root, s["video_path"])
            except media.CutError as e:
                cut_error = str(e)
        segs.append({"row": s, "checks": readiness(runtime, s["id"]), "est_min": None if est is None else est / 60,
                     "draft_min": None if draft is None else draft / 60, "tags": tags,
                     "few_now": few.get(cur["id"]) if cur else None,
                     "blank": blankscan.problem(s["number"], blank) if blank else None,
                     "blank_override": _override(store, runtime.data_root, s) if blank else None,
                     "tasks": videos,
                     "align_tasks": [t for t in tasks if t["kind"] == "align"],
                     "align_problems": align_problems(store, s["id"]),
                     "keychecks": keycheck.check(store, runtime.data_root, s["id"]),
                     "cuts": cut, "cut_error": cut_error, "prev_last": prev.last_png if prev else None,
                     "blocking": blocking.phrase(blocking.parse(s["blocking_json"])),
                     "video_sha": sha256_file(runtime.data_root / s["video_path"]) if has_video else None,
                     "vision": _vision(store, s, runtime.data_root) if has_video else None,
                     "review": _review(store, runtime.data_root, s) if has_video else None,
                     "extra": aligner.extra_speech_for(store, runtime.data_root, s["id"]),
                     "cues": subtitles.parse_srt(sub.read_text(encoding="utf-8")) if sub and sub.is_file() else []})
        prev = cut
    return render(request, "videos.html", "videos", episode_id=episode_id, ep=ep, segs=segs, seed=new_seed(),
                  review_items=checklist.ITEMS,
                  est_note=b.note, key_ref=(keycheck.HIT, keycheck.OTHER), full_steps=full,
                  full_from_preset=preset_steps is not None, draft_steps=DRAFT_STEPS, few_note=FEW_STEPS_NOTE)


@router.post("/segment/{segment_id}/video")
def generate(request: Request, segment_id: int, seed: int = Form(...), steps: str = Form("")):
    store = rt(request).store
    plan_video(store, segment_id, seed=seed, steps=int(steps) if steps.strip() else None)
    ep_id = repo.get(store, "segment", segment_id)["episode_id"]
    return RedirectResponse(f"/episode/{ep_id}/videos#seg-{segment_id}", status_code=303)


@router.post("/segment/{segment_id}/blank-override")
def blank_override(request: Request, segment_id: int, reason: str = Form("")):
    """白屏命中、人看过确认不是白屏:填理由放行(★必填),只对这条视频的 sha256 有效。"""
    runtime = rt(request)
    blankscan.confirm(runtime.store, runtime.data_root, segment_id, reason)
    ep_id = repo.get(runtime.store, "segment", segment_id)["episode_id"]
    return RedirectResponse(f"/episode/{ep_id}/videos#seg-{segment_id}", status_code=303)


@router.post("/segment/{segment_id}/review")
async def confirm_review(request: Request, segment_id: int):
    """逐项勾过验收清单、确认没有穿帮:只对这条视频的 sha256 有效(spec 2026-10-23-不穿帮验收 第 1 节)。"""
    runtime = rt(request)
    form = await request.form()
    review.confirm(runtime.store, runtime.data_root, segment_id, [str(k) for k in form.getlist("check")],
                   str(form.get("note", "")))
    ep_id = repo.get(runtime.store, "segment", segment_id)["episode_id"]
    return RedirectResponse(f"/episode/{ep_id}/videos#seg-{segment_id}", status_code=303)


@router.post("/segment/{segment_id}/align")
def align(request: Request, segment_id: int):
    store = rt(request).store
    plan_align(store, segment_id)
    ep_id = repo.get(store, "segment", segment_id)["episode_id"]
    return RedirectResponse(f"/episode/{ep_id}/videos#seg-{segment_id}", status_code=303)
