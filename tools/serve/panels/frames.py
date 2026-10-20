# -*- coding: utf-8 -*-
"""参考帧:按段落出首帧 / 尾帧(多图参考合成,目标画幅由剧决定),逐条重绘保留历史。

段落用哪几张参考帧由参考模式定:首帧(first)、首尾帧(first_last)、尾帧(last)。尾帧用段落的「尾帧图提示词」合成。
中间关键帧叠加在任何模式上,一段 0–3 张,每张一个子卡(各自的图、生成按钮、历史版本)。
也可以用上一段视频的最后一帧作本段首帧(尾帧衔接,设计 §6.6):本段已有视频时会让它作废,先报代价再确认。
已有图的帧可以「局部替换」:在图上框一块、选本段绑定的素材,按它的参考图改那一块(新版本进历史,人挑了才替换)。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from tools.cut import tailframe
from tools.drama import repo, targets
from tools.drama.readiness import KIND_LABEL
from tools.drama.tasks import tasks_for
from tools.gen import compose
from tools.gen.planner import FRAMES, REGION_KINDS, plan_first_frame, plan_key_frame, plan_last_frame, plan_region
from tools.serve.panels.common import confirm_page, needs_confirm, new_seed, readiness, render, rt

router = APIRouter()


FRAME_NOTES = ("two_pass", "slots")      # 就绪清单里与出参考帧有关的提醒(两遍合成;槽位不足 → 怎么补)


def region_assets(store, segment_id: int, text: str) -> list[tuple[str, str, str]]:
    """局部替换可选的素材:本段绑定且有参考图的,(值形如 character:3, 显示名, 默认补充说明)。
    默认补充说明只给角色:这一格的图提示词 text 里写这个人的那段(compose.region_note)。"""
    a = repo.segment_assets(store, segment_id)
    rows = ([("character", c) for c in a["characters"]] + ([("scene", a["scene"])] if a["scene"] is not None else [])
            + [("prop", p) for p in a["props"]])
    names = [r["name"] for _, r in rows]
    return [(f"{k}:{r['id']}", f"{KIND_LABEL[k]} · {r['name']}",
             compose.region_note(text, r["name"], names) if k == "character" else "")
            for k, r in rows if r[REGION_KINDS[k][1]]]


@router.get("/episode/{episode_id}/frames")
def page(request: Request, episode_id: int):
    runtime = rt(request)
    store = runtime.store
    ep = repo.get(store, "episode", episode_id)
    rows = store.conn.execute("SELECT * FROM segment WHERE episode_id = ? ORDER BY number", (episode_id,)).fetchall()
    segs = []
    for i, s in enumerate(rows):
        images = [t for t in tasks_for(store, "segment", s["id"]) if t["kind"] == "image"]
        by_field = {f: [t for t in images if json.loads(t["params_json"]).get("writes") == f]
                    for f in (*FRAMES, targets.KEY_WRITES)}
        keys = [{"row": k, "tasks": [t for t in by_field[targets.KEY_WRITES] if targets.slot(store, t)[1] == k["id"]],
                 "region_assets": region_assets(store, s["id"], k["image_prompt"])}
                for k in repo.keyframes(store, s["id"])]
        segs.append({"row": s, "prev": rows[i - 1] if i else None, "tasks": by_field["first_frame_path"],
                     "last_tasks": by_field["last_frame_path"], "keyframes": keys,
                     "uses_first": s["reference_mode"] in FRAMES["first_frame_path"][0],
                     "uses_last": s["reference_mode"] in FRAMES["last_frame_path"][0],
                     "region_first": region_assets(store, s["id"], s["image_prompt"]),
                     "region_last": region_assets(store, s["id"], s["last_image_prompt"]),
                     "notes": [c for c in readiness(runtime, s["id"]) if c.key in FRAME_NOTES]})
    return render(request, "frames.html", "frames", episode_id=episode_id, ep=ep, segs=segs, seed=new_seed())


@router.post("/segment/{segment_id}/frame")
def generate(request: Request, segment_id: int, seed: int = Form(...)):
    store = rt(request).store
    plan_first_frame(store, segment_id, seed=seed)
    ep_id = repo.get(store, "segment", segment_id)["episode_id"]
    return RedirectResponse(f"/episode/{ep_id}/frames#seg-{segment_id}", status_code=303)


@router.post("/segment/{segment_id}/lastframe")
def generate_last(request: Request, segment_id: int, seed: int = Form(...)):
    store = rt(request).store
    plan_last_frame(store, segment_id, seed=seed)
    ep_id = repo.get(store, "segment", segment_id)["episode_id"]
    return RedirectResponse(f"/episode/{ep_id}/frames#seg-{segment_id}", status_code=303)


@router.post("/keyframe/{keyframe_id}/frame")
def generate_key(request: Request, keyframe_id: int, seed: int = Form(...)):
    store = rt(request).store
    plan_key_frame(store, keyframe_id, seed=seed)
    sid = repo.get(store, "segment_keyframe", keyframe_id)["segment_id"]
    ep_id = repo.get(store, "segment", sid)["episode_id"]
    return RedirectResponse(f"/episode/{ep_id}/frames#seg-{sid}", status_code=303)


@router.post("/segment/{segment_id}/region")
def region(request: Request, segment_id: int, slot: str = Form(...), source: str = Form(...), x: int = Form(...),
           y: int = Form(...), w: int = Form(...), h: int = Form(...), asset: str = Form(...), note: str = Form(""),
           seed: int = Form(...)):
    """框选局部替换。框是浏览器按原图像素换算的;原图尺寸在这里读文件头,不信表单。"""
    runtime = rt(request)
    root = runtime.data_root.resolve()
    path = (root / source).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"图片文件不存在:{source}")
    kind, _, row_id = asset.partition(":")
    if kind not in REGION_KINDS or not row_id.isdigit():
        raise ValueError(f"素材选得不对:{asset!r}")
    plan_region(runtime.store, segment_id, slot, source=source, size=compose.png_size(path.read_bytes()),
                box=[x, y, w, h], asset=(kind, int(row_id)), note=note.strip(), seed=seed)
    ep_id = repo.get(runtime.store, "segment", segment_id)["episode_id"]
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
