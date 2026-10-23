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

from tools.drama import blocking, pacing, repo
from tools.llm import (describe, diverge, outline, outline_variants, premise, runs, script_writer, service, shot_options,
                       storyboard, vision_check, voices)
from tools.serve.panels.common import confirm_page, render, rt

router = APIRouter()

# 各任务从表单里取哪些参数、什么类型
PARAMS = {"outline": {"novel": str, "n_episodes": int}, "script_synopsis": {"synopsis": str, "minutes": int},
          "script_adapt": {"minutes": int}, "storyboard": {}, "voice": {},
          **{f"describe_{t}": {} for t in describe.FIELDS}, "vision_check": {"video_sha256": str},
          "critique": {"role": str}, "rewrite": {"span": str, "intent": str, "note": str},
          "premise": {"idea": str, "n": int, "constraint": str},
          "beats": {}, "outline_variants": {"n": int}, "shot_options": {"n": int}}


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
        if row["task"] == "premise":
            return f"/drama/{tid}/premise", {"panel_key": "projects", "drama_id": tid}
        return f"/drama/{tid}/outline", {"panel_key": "outline", "drama_id": tid}
    if t in ("character", "scene", "prop"):
        did = repo.get(store, t, tid)["drama_id"]
        return f"/drama/{did}/assets", {"panel_key": "assets", "drama_id": did}
    if t == "segment":
        eid = repo.get(store, "segment", tid)["episode_id"]
        if row["task"] == "shot_options":
            return f"/segment/{tid}/edit", {"panel_key": "storyboard", "episode_id": eid}
        return f"/episode/{eid}/videos#seg-{tid}", {"panel_key": "videos", "episode_id": eid}
    if row["task"] == "storyboard":
        return f"/episode/{tid}/storyboard", {"panel_key": "storyboard", "episode_id": tid}
    did = repo.get(store, "episode", tid)["drama_id"]
    if row["task"] == "outline_variants":
        return f"/drama/{did}/outline", {"panel_key": "outline", "drama_id": did}
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
    if task.startswith("describe_"):
        table = row["target_type"]
        cur = repo.get(store, table, row["target_id"])
        return {"describe": [{"field": k, "label": label.split(":")[0], "current": cur[k], "proposed": out[k]}
                             for k, label in describe.FIELDS[table].items()]}
    if task == "vision_check":
        seg = repo.get(store, "segment", row["target_id"])
        expected = blocking.parse(seg["blocking_json"])
        return {"vision": out["frames"], "mismatches": vision_check.mismatches(expected, out)}
    if task == "critique":
        stance = {}                     # 批准后的记录:序号 → adopt / reject(没批准为空)
        dec = runs.decision(row) if row["state"] == "approved" else None
        if dec:
            stance = {**{i: "adopt" for i in dec["adopt"]}, **{i: "reject" for i in dec["reject"]}}
        return {"issues": [{"n": i, "stance": stance.get(i), **it} for i, it in enumerate(out["issues"])],
                "role": params["role"]}
    if task == "rewrite":
        dec = runs.decision(row) if row["state"] == "approved" else None
        picked = dec["pick"] if dec else None
        return {"span": params["span"], "intent": params["intent"],
                "candidates": [{"n": i, "picked": i == picked, **c} for i, c in enumerate(out["candidates"])]}
    if task == "beats":
        beats = {e["number"]: e for e in out["episodes"]}
        return {"beats": sorted(beats.values(), key=lambda e: e["number"]), "hints": pacing.hints(beats)}
    if task in ("outline_variants", "shot_options"):
        dec = runs.decision(row) if row["state"] == "approved" else None
        picked = dec["pick"] if dec else None
        if task == "outline_variants":
            vs = out["variants"]
            routine = diverge.routine_index(vs)
            # 批准后这集的大纲就是选中的那版,批准前的原文已不在库里 —— 只在待审时并排显示现在的大纲
            original = repo.get(store, "episode", row["target_id"])["outline"] if row["state"] == "proposed" else ""
            return {"variants": [{"n": i, "picked": i == picked, "routine": i == routine, **v} for i, v in enumerate(vs)],
                    "original": original}
        return {"options": [{"n": i, "picked": i == picked, "phrase": blocking.phrase(o["blocking"]), **o}
                            for i, o in enumerate(out["options"])]}
    if task == "premise":
        cands = out["candidates"]
        picked = (runs.decision(row) or {}).get("assemble", {}) if row["state"] == "approved" else {}
        return {"premise": {"fields": [(f, premise.LABELS[f]) for f in premise.FIELDS], "candidates": cands,
                            "routine": diverge.routine_index(cands),
                            "overlap": round(diverge.overlap(cands, tuple(premise.DIMS)), 2), "picked": picked}}
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


def _decision(task: str, form) -> dict | None:
    """审阅页上人做的选择 → decision(service.approve 再按模块规则核一遍)。不要选择的任务返回 None。"""
    if task == "critique":
        stance = {}
        for k, v in form.items():
            idx = k.split("-", 1)[1] if k.startswith("issue-") else ""
            if idx.isdigit():                       # 序号不是整数的键直接忽略,缺的表态由 decision_problems 报
                stance[int(idx)] = str(v)
        return {"adopt": sorted(i for i, v in stance.items() if v == "adopt"),
                "reject": sorted(i for i, v in stance.items() if v == "reject")}
    if task in ("rewrite", "outline_variants", "shot_options"):
        raw = str(form.get("pick", ""))
        return {"pick": int(raw) if raw.isdigit() else -1}
    if task == "premise":
        picks = {}
        for f in premise.FIELDS:
            raw = str(form.get(f"pick-{f}", ""))
            if raw.isdecimal():
                picks[f] = int(raw)
        return {"assemble": picks}
    return None


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
async def approve(request: Request, run_id: int):
    """会删掉本集已有段落的(重拆分镜 / 重写剧本),先出确认页报代价;确认了才带 confirm_reset 批准。"""
    store = rt(request).store
    form = await request.form()
    cost = service.reset_cost(store, run_id)
    if cost is not None and form.get("confirm") != "1":
        n, imp = cost
        home, ctx = _home(store, runs.get(store, run_id))
        return confirm_page(request, summary=f"批准会删除本集已有的 {n} 个段落(生成历史保留,可在溯源里查到);"
                                             + imp.summary(),
                            action=f"/llm/run/{run_id}/approve", fields={}, back=f"/llm/run/{run_id}", **ctx)
    decision = _decision(runs.get(store, run_id)["task"], form)
    synced = service.approve(store, run_id, rt(request).data_root, confirm_reset=cost is not None, decision=decision)
    home = _home(store, runs.get(store, run_id))[0]
    if runs.get(store, run_id)["task"] == "rewrite":      # 改写写回后告诉剧本面板:台词同步到了第几段(没对上为 0)
        home += f"?rewrite_run={run_id}&synced={synced or 0}"
    return RedirectResponse(home, status_code=303)


@router.post("/llm/run/{run_id}/reject")
def reject(request: Request, run_id: int):
    store = rt(request).store
    runs.reject(store, run_id)
    return RedirectResponse(f"/llm/run/{run_id}", status_code=303)
