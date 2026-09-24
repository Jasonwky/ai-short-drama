# -*- coding: utf-8 -*-
"""LLM 任务:开始 → 审阅页(每轮判卷问题 + 产物预览)→ 人批准写回 / 驳回。

面板上的「LLM 生成」按钮都 POST 到这里;审阅页在任务跑着时自动刷新。
**批准才写回**(CLAUDE.md「人工审批通过即锁定」);假 LLM 的产物按钮是灰的,并写明原因。
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from tools.drama import repo
from tools.llm import outline, runs, script_writer, service, storyboard, voices
from tools.serve.panels.common import render, rt

router = APIRouter()

# 各任务从表单里取哪些参数、什么类型
PARAMS = {"outline": {"novel": str, "n_episodes": int}, "script_synopsis": {"synopsis": str, "minutes": int},
          "script_adapt": {"minutes": int}, "storyboard": {}, "voice": {}}


def _params(task: str, form) -> dict:
    service.spec(task)                     # 未知任务当场报错
    out = {}
    for key, typ in PARAMS[task].items():
        raw = str(form.get(key, "")).strip()
        try:
            out[key] = typ(raw)
        except ValueError as e:
            raise ValueError(f"「{key}」要填{'整数' if typ is int else '文字'},填的是 {raw!r}") from e
    return out


def _home(store, row) -> tuple[str, dict]:
    """这条 LLM 任务回到哪个面板,以及渲染导航用的上下文。"""
    t, tid = row["target_type"], row["target_id"]
    if t == "drama":
        return f"/drama/{tid}/outline", {"panel_key": "outline", "drama_id": tid}
    if t == "character":
        did = repo.get(store, "character", tid)["drama_id"]
        return f"/drama/{did}/assets", {"panel_key": "assets", "drama_id": did}
    if row["task"] == "storyboard":
        return f"/episode/{tid}/storyboard", {"panel_key": "storyboard", "episode_id": tid}
    did = repo.get(store, "episode", tid)["drama_id"]
    return f"/drama/{did}/script", {"panel_key": "script", "drama_id": did}


def _preview(store, row) -> dict:
    """把产物渲染成人能审的样子(与写回时用同一套代码)。"""
    if row["output_json"] is None:
        return {}
    out, params = runs.output(row), runs.params(row)
    task = row["task"]
    if task == "storyboard":
        segs = []
        for n, seg in enumerate(out["segments"], 1):
            desc = storyboard.describe(seg)
            segs.append({"n": n, "scene": seg["scene"], "characters": seg["characters"], "props": seg.get("props", []),
                         "description": desc, "duration": storyboard.duration(desc, seg["pace"]),
                         "image_prompt": seg["image_prompt"], "video_prompt": seg["video_prompt"]})
        return {"segments": segs}
    if task == "voice":
        return {"voice": voices.compose(out),
                "current": repo.get(store, "character", row["target_id"])["voice_desc"]}
    if task.startswith("script_"):
        return {"script": script_writer.render(out), "current": repo.get(store, "episode", row["target_id"])["script"]}
    if task == "outline":
        paras = outline.paragraphs(params["novel"])
        return {"episodes": [{**ep, "outline": outline.render_outline(ep), "source": outline.episode_text(paras, ep)}
                             for ep in out["episodes"]]}
    return {}


def _rounds(row) -> list[dict]:
    if not row["journal_path"] or not Path(row["journal_path"]).exists():
        return []
    doc = json.loads(Path(row["journal_path"]).read_text(encoding="utf-8"))
    return [{"n": r["n"], "problems": r["problems"], "seconds": r["seconds"],
             "tokens": (r.get("usage") or {}).get("total_tokens")} for r in doc["rounds"]]


@router.post("/llm/start/{task}/{target_id}")
async def start(request: Request, task: str, target_id: int):
    service.spec(task)
    form = await request.form()
    rid, _ = rt(request).start_llm(task, target_id, _params(task, form))
    return RedirectResponse(f"/llm/run/{rid}", status_code=303)


@router.get("/llm/run/{run_id}")
def review(request: Request, run_id: int):
    store = rt(request).store
    row = runs.get(store, run_id)
    home, ctx = _home(store, row)
    return render(request, "llm_run.html", ctx.pop("panel_key"), run=row, label=service.spec(row["task"]).label,
                  home=home, preview=_preview(store, row), rounds=_rounds(row), **ctx)


@router.post("/llm/run/{run_id}/approve")
def approve(request: Request, run_id: int):
    store = rt(request).store
    service.approve(store, run_id)
    return RedirectResponse(_home(store, runs.get(store, run_id))[0], status_code=303)


@router.post("/llm/run/{run_id}/reject")
def reject(request: Request, run_id: int):
    store = rt(request).store
    runs.reject(store, run_id)
    return RedirectResponse(f"/llm/run/{run_id}", status_code=303)
