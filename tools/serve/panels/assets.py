# -*- coding: utf-8 -*-
"""资产:角色 / 场景 / 道具三栏。编辑描述、生成参考图(灰底整幅替换)、上传参考图、从图反推描述、历史版本可挑。

- **音色锁定**:角色的声音描述逐字进视频提示词,改它会让绑定段落的视频作废 → 先报代价再确认(参考帧不受影响);
- **种子锁定**:锁定某一版参考图的种子后,重出参考图只用它(服务端强制,补全也一样),解锁才能换。

生成走任务表:这里只规划(写一条参数定死的 task),由后台 worker 提交云机。
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

from tools.drama import invalidation, repo, uploads
from tools.drama.tasks import tasks_for
from tools.gen.assets import SPEC, ensure_canvas, lock_seed, plan_asset_image, unlock_seed
from tools.gen.capability import MINIMAX_H3, QWEN_IMAGE_EDIT_2511
from tools.llm import runs
from tools.serve.panels.common import confirm_page, needs_confirm, new_seed, render, rt

router = APIRouter()

# 面板上可编辑的文字字段(都在 repo 的写入白名单里)
FIELDS = {
    "character": [("appearance", "外貌"), ("styling", "服装造型"), ("voice_desc", "声音描述(视频里的音色靠它)"),
                  ("final_prompt", "生成描述(英文效果更稳)")],
    "scene": [("location", "地点"), ("time", "时间"), ("lighting", "光线"), ("final_prompt", "生成描述")],
    "prop": [("description", "描述"), ("final_prompt", "生成描述")],
}
KIND_LABEL = {"character": "角色", "scene": "场景", "prop": "道具"}
_ADD = {"character": repo.add_character, "scene": repo.add_scene, "prop": repo.add_prop}


@router.get("/drama/{drama_id}/assets")
def page(request: Request, drama_id: int):
    store = rt(request).store
    repo.get(store, "drama", drama_id)
    cols = []
    for table in ("character", "scene", "prop"):
        items = []
        for row in store.conn.execute(f"SELECT * FROM {table} WHERE drama_id = ? ORDER BY id", (drama_id,)):
            items.append({"row": row, "current": row[SPEC[table][0]], "tasks": tasks_for(store, table, row["id"]),
                          "llm_runs": runs.history(store, table, row["id"])})
        cols.append({"table": table, "label": KIND_LABEL[table], "entries": items, "fields": FIELDS[table]})
    return render(request, "assets.html", "assets", drama_id=drama_id, cols=cols, seed=new_seed())


@router.post("/drama/{drama_id}/assets")
def add(request: Request, drama_id: int, kind: str = Form(...), name: str = Form("")):
    if kind not in _ADD:
        raise ValueError(f"未知资产类型 {kind}")
    if not name.strip():
        raise ValueError("名字不能为空 —— 分镜里要用 @名字 引用它")
    try:
        _ADD[kind](rt(request).store, drama_id, name.strip())
    except sqlite3.IntegrityError as e:
        raise ValueError(f"这部剧里已经有叫「{name.strip()}」的{KIND_LABEL[kind]}了") from e
    return RedirectResponse(f"/drama/{drama_id}/assets", status_code=303)


@router.post("/asset/{table}/{row_id}")
async def edit(request: Request, table: str, row_id: int):
    if table not in FIELDS:
        raise ValueError(f"未知资产类型 {table}")
    form = await request.form()
    store = rt(request).store
    fields = {k: str(form.get(k, "")) for k, _ in FIELDS[table]}
    row = repo.get(store, table, row_id)
    back = f"/drama/{row['drama_id']}/assets"
    if table == "character" and fields["voice_desc"] != row["voice_desc"]:
        imp = invalidation.impact(store, "voice", row_id, video_cap=MINIMAX_H3, image_cap=QWEN_IMAGE_EDIT_2511)
        if needs_confirm(imp, form):
            return confirm_page(request, summary="改声音描述:" + imp.summary(), action=f"/asset/{table}/{row_id}",
                                fields=fields, back=back, panel_key="assets", drama_id=row["drama_id"])
        invalidation.apply(store, imp)
    repo.update(store, table, row_id, **fields)
    return RedirectResponse(back, status_code=303)


@router.post("/asset/character/{row_id}/seed")
async def seed(request: Request, row_id: int):
    """锁定某一版参考图的种子(task_id),或解锁(unlock=1)。"""
    store = rt(request).store
    form = await request.form()
    if form.get("unlock") == "1":
        unlock_seed(store, row_id)
    else:
        lock_seed(store, row_id, int(str(form.get("task_id", "0")) or 0))
    return RedirectResponse(f"/drama/{repo.get(store, 'character', row_id)['drama_id']}/assets", status_code=303)


@router.post("/asset/{table}/{row_id}/generate")
def generate(request: Request, table: str, row_id: int, seed: int = Form(...)):
    runtime = rt(request)
    ensure_canvas(runtime.data_root)
    plan_asset_image(runtime.store, table, row_id, seed=seed)
    return RedirectResponse(f"/drama/{repo.get(runtime.store, table, row_id)['drama_id']}/assets", status_code=303)


@router.post("/asset/{table}/{row_id}/upload")
def upload(request: Request, table: str, row_id: int, file: UploadFile = File(...)):
    """上传即暂存(按内容寻址);会让下游作废就先出确认页,否则直接换上。"""
    runtime = rt(request)
    rel = uploads.stage(runtime.data_root, file.file.read(uploads.MAX_BYTES + 1))   # 多读 1 字节才判得出超限
    return _use(request, table, row_id, rel, file.filename or "upload", {})


@router.post("/asset/{table}/{row_id}/upload/confirm")
async def upload_confirm(request: Request, table: str, row_id: int):
    form = await request.form()
    return _use(request, table, row_id, str(form.get("rel", "")), str(form.get("filename", "upload")), form)


def _use(request: Request, table: str, row_id: int, rel: str, filename: str, form):
    runtime = rt(request)
    imp = uploads.plan_use(runtime.store, table, row_id)
    drama_id = repo.get(runtime.store, table, row_id)["drama_id"]
    if needs_confirm(imp, form):
        return confirm_page(request, summary="换成上传的参考图:" + imp.summary(),
                            action=f"/asset/{table}/{row_id}/upload/confirm", fields={"rel": rel, "filename": filename},
                            back=f"/drama/{drama_id}/assets", panel_key="assets", drama_id=drama_id)
    uploads.confirm_use(runtime.store, runtime.data_root, table, row_id, rel, filename, imp)
    return RedirectResponse(f"/drama/{drama_id}/assets", status_code=303)
