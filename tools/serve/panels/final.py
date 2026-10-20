# -*- coding: utf-8 -*-
"""成片:拼接 + 显式标识 + 隐式元数据 + 溯源报告,导出后读回自检(设计 §6.8–6.9)。

服务提供者编码是★必答:没有就不导出,不用默认值。导出在后台跑,页面有在途导出时自动刷新。
字幕在「视频」面板对齐,导出时烧录进两版;有台词却没有字幕的段落挡住导出。
配乐(specs/2026-10-20-配乐白屏与替换神情-design.md 一):人上传有许可的音乐,按整集多段挂;
每条一张卡,数字可改、音量曲线(台词区间打底色)、本机只混音频的试听。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

from tools.cut import export, media, music
from tools.drama import repo
from tools.drama.tasks import producer
from tools.gen.workflows import H3_STEPS
from tools.serve.dispatch import FEW_STEPS_NOTE, steps_of
from tools.serve.panels.common import render, rt

router = APIRouter()

CURVE_W, CURVE_H, CURVE_FLOOR = 640, 110, -50.0     # 音量曲线:横轴成片时间,纵轴 0 dB(顶)到 −50 dB(底)


def _curve(cue, speech: list[tuple[float, float]], axis: float) -> dict:
    """一条 cue 的音量曲线(SVG 坐标)。增益与导出混音用的是同一个包络(music.gain_at)。"""
    def x(t: float) -> float:
        return round(t / axis * CURVE_W, 1)

    def y(db: float) -> float:
        return round(db / CURVE_FLOOR * CURVE_H, 1)
    pts = [(0.0, CURVE_FLOOR), (cue["start_sec"], CURVE_FLOOR), *music.curve_db(cue, speech, CURVE_FLOOR),
           (cue["end_sec"], CURVE_FLOOR), (axis, CURVE_FLOOR)]
    return {"points": " ".join(f"{x(t)},{y(db)}" for t, db in pts),
            "speech": [(x(a), max(0.5, x(b) - x(a))) for a, b in speech if a < axis],
            "gain_y": y(cue["gain_db"]), "duck_y": y(cue["gain_db"] + cue["duck_db"])}


@router.get("/episode/{episode_id}/final")
def page(request: Request, episode_id: int):
    runtime = rt(request)
    store = runtime.store
    ep = repo.get(store, "episode", episode_id)
    segs = store.conn.execute("SELECT * FROM segment WHERE episode_id = ? ORDER BY number", (episode_id,)).fetchall()
    rows = [{"row": r, "checks": json.loads(r["checks_json"]) if r["checks_json"] else []}
            for r in export.list_for(store, episode_id)]
    few = []                   # 少步数的视频:提醒、不拦(用户 2026-10-15 定)
    for s in segs:
        t = producer(store, "video", s["video_path"]) if s["video_path"] else None
        n = steps_of(t) if t else None
        if n is not None and n < H3_STEPS:
            few.append(f"段落 {s['number']} 的视频只用了 {n} 步:{FEW_STEPS_NOTE}")
    total, speech, exact = music.timeline(store, runtime.data_root, episode_id)
    cues = repo.music_cues(store, episode_id)
    axis = max([total, *[c["end_sec"] for c in cues]]) or 1.0
    cards = []
    for c in cues:
        rel = music.preview_path(store, c, speech)
        cards.append({"row": c, "curve": _curve(c, speech, axis),
                      "preview": rel if (runtime.data_root / rel).is_file() else None})
    return render(request, "final.html", "final", episode_id=episode_id, ep=ep, segs=segs, few_steps=few,
                  problems=export.readiness(store, runtime.data_root, episode_id),
                  producer=export.saved_producer(store), exports=rows, label_text=media.LABEL_TEXT,
                  cues=cards, total=total, axis=axis, timeline_exact=exact, curve=(CURVE_W, CURVE_H, CURVE_FLOOR),
                  music_exts=",".join(music.EXTS), music_note=music.NOTE)


@router.post("/episode/{episode_id}/export")
def start(request: Request, episode_id: int, producer: str = Form("")):
    rt(request).start_export(episode_id, producer)
    return RedirectResponse(f"/episode/{episode_id}/final", status_code=303)


@router.post("/episode/{episode_id}/music")
def upload_music(request: Request, episode_id: int, file: UploadFile = File(...), license: str = Form(""),
                 start_sec: float = Form(0.0)):
    """上传即挂一条 cue:许可说明★必填(先查,不合格的文件不落盘);终点默认到音乐结束或成片结束。"""
    runtime = rt(request)
    repo.get(runtime.store, "episode", episode_id)
    if not license.strip():
        raise ValueError("许可说明是★必填:写清这首音乐的来源与许可(如曲库名 + 授权编号)")
    rel = music.stage(runtime.data_root, file.file.read(music.MAX_BYTES + 1), file.filename or "")
    total, _, exact = music.timeline(runtime.store, runtime.data_root, episode_id)
    music.add(runtime.store, runtime.data_root, episode_id, rel, license, start_sec=start_sec,
              until=total if exact and total > 0 else None)
    return RedirectResponse(f"/episode/{episode_id}/final#music", status_code=303)


@router.post("/music/{cue_id}")
def edit_music(request: Request, cue_id: int, license: str = Form(""), start_sec: float = Form(...),
               end_sec: float = Form(...), gain_db: float = Form(...), duck_db: float = Form(...),
               fade_in: float = Form(...), fade_out: float = Form(...)):
    store = rt(request).store
    music.edit(store, cue_id, license, start_sec=start_sec, end_sec=end_sec, gain_db=gain_db, duck_db=duck_db,
               fade_in=fade_in, fade_out=fade_out)
    return RedirectResponse(f"/episode/{repo.get(store, 'music_cue', cue_id)['episode_id']}/final#music-{cue_id}",
                            status_code=303)


@router.post("/music/{cue_id}/preview")
def preview_music(request: Request, cue_id: int):
    runtime = rt(request)
    music.preview(runtime.store, runtime.data_root, cue_id)
    return RedirectResponse(f"/episode/{repo.get(runtime.store, 'music_cue', cue_id)['episode_id']}/final#music-{cue_id}",
                            status_code=303)


@router.post("/music/{cue_id}/delete")
def delete_music(request: Request, cue_id: int):
    store = rt(request).store
    episode_id = repo.get(store, "music_cue", cue_id)["episode_id"]
    repo.delete_music_cue(store, cue_id)
    return RedirectResponse(f"/episode/{episode_id}/final#music", status_code=303)
