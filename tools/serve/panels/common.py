# -*- coding: utf-8 -*-
"""面板共用:模板、导航上下文、确认页、种子。"""
from __future__ import annotations

import json
import random
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from tools.drama import repo
from tools.drama.invalidation import Impact
from tools.drama.readiness import segment_readiness
from tools.drama.styles import STYLES
from tools.gen.capability import MINIMAX_H3, QWEN_IMAGE_EDIT_2511
from tools.serve.panels import REGISTRY, url_for
from tools.serve.runtime import Runtime

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
TEMPLATES.env.filters["fromjson"] = json.loads
STATE_LABEL = {"pending": "待提交", "queued": "已提交", "running": "生成中", "waiting_host": "等云机",
               "done": "完成", "failed": "失败"}
IN_FLIGHT = ("pending", "queued", "running", "waiting_host")
LLM_STATE = {"running": "生成中", "proposed": "待审", "failed": "失败", "approved": "已批准", "rejected": "已驳回"}


def rt(request: Request) -> Runtime:
    return request.app.state.runtime


def new_seed() -> int:
    """表单里预填的种子。它显示在页面上、随表单提交 —— 对任务来说仍是显式种子,人可以改。"""
    return random.randint(1, 2**31 - 1)


def first_episode(runtime: Runtime, drama_id: int | None) -> int | None:
    if drama_id is None:
        return None
    row = runtime.store.conn.execute("SELECT id FROM episode WHERE drama_id = ? ORDER BY number LIMIT 1",
                                     (drama_id,)).fetchone()
    return row[0] if row else None


def render(request: Request, template: str, panel_key: str, *, drama_id: int | None = None,
           episode_id: int | None = None, status_code: int = 200, **ctx):
    runtime = rt(request)
    if drama_id is None and episode_id is None and request.query_params.get("d", "").isdigit():
        drama_id = int(request.query_params["d"])    # 全局面板从剧里点过来时带的上下文
    if episode_id is not None and drama_id is None:
        drama_id = repo.get(runtime.store, "episode", episode_id)["drama_id"]
    ep = episode_id if episode_id is not None else first_episode(runtime, drama_id)
    nav = [{"key": p.key, "title": p.title, "url": url_for(p, drama_id, ep), "active": p.key == panel_key}
           for p in REGISTRY]
    drama = repo.get(runtime.store, "drama", drama_id) if drama_id is not None else None
    episodes = (runtime.store.conn.execute("SELECT id, number FROM episode WHERE drama_id = ? ORDER BY number",
                                           (drama_id,)).fetchall() if drama_id is not None else [])
    return TEMPLATES.TemplateResponse(request, template, {
        "panel_key": panel_key, "nav": nav, "drama": drama, "episodes": episodes, "episode_id": ep,
        "backend": runtime.backend, "styles": {s.key: s.label for s in STYLES},
        "state_label": STATE_LABEL, "llm_state": LLM_STATE, **ctx}, status_code=status_code)


def confirm_page(request: Request, *, summary: str, action: str, fields: dict, back: str, panel_key: str,
                 drama_id: int | None = None, episode_id: int | None = None):
    """plan/confirm 的确认页:把原表单字段原样带回去,多加一个 confirm=1。"""
    return render(request, "confirm.html", panel_key, drama_id=drama_id, episode_id=episode_id,
                  summary=summary, action=action, fields=fields, back=back)


def needs_confirm(imp: Impact, form) -> bool:
    return bool(imp.videos or imp.frames) and form.get("confirm") != "1"


def readiness(runtime: Runtime, segment_id: int) -> list:
    """段落 readiness,外部依赖取运行时的真实判活结果。模型文件对不对,由提交时的 LoRA 预检与 ComfyUI 校验把关。"""
    host_ok, host_msg = runtime.any_host_ready()
    return segment_readiness(runtime.store, segment_id, video_cap=MINIMAX_H3, image_cap=QWEN_IMAGE_EDIT_2511,
                             host_ok=host_ok, host_msg=host_msg,
                             model_ok=True, model_msg="模型在提交时由 LoRA 预检与 ComfyUI 校验")
