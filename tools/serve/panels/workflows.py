# -*- coding: utf-8 -*-
"""工作流:模板预设 + ComfyUI 画布编辑(设计 specs/2026-10-08-ComfyUI工作流编辑-design.md)。

- 「工作流」面板:每类模板(首帧 / 尾帧、四视图、资产图、视频)的预设各版本及相对内置的改动、设为当前(两态)、
  前端包与节点清单的状态、模型白名单;
- 编辑页:上方控制台操作栏 + 下方嵌入的 ComfyUI 画布(同源 iframe,本机离线可用)。
  改参重跑「提交为新版本」/ 模板「保存为预设」都由**控制台**把关后才落盘 —— 画布里的「运行」被垫片拒绝。
- 编辑令牌:打开时把工作流载入名为 `edit-<令牌>` 的标签;提交时核对当前标签,对不上(人在别的标签上改)就拒绝。
- 云机在线时提交前用实时节点清单再核一次,并核对前端版本(设计 §四 #10、#11)。
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from tools.drama import repo
from tools.gen import frontend, graphs, nodecache, presets
from tools.gen.capability import ALLOWED_MODELS
from tools.serve import journal, rerun
from tools.serve.panels.common import confirm_page, render, rt

router = APIRouter()


@router.get("/workflows")
def page(request: Request):
    store = rt(request).store
    cats = []
    for cat, label in presets.CATEGORIES.items():
        base, _ = presets.example(cat)
        cur = presets.active(store, cat)
        vers = [{"row": v, "lines": graphs.summary(presets.diff_of(v), base)} for v in presets.versions(store, cat)]
        cats.append({"key": cat, "label": label, "active": cur, "versions": vers})
    runtime = rt(request)
    return render(request, "workflows.html", "workflows", cats=cats, models=ALLOWED_MODELS,
                  frontend_dir=frontend.installed(_fe_root()), frontend_version=frontend.VERSION,
                  frontend_job=runtime.frontend_job, nodes=nodecache.meta(runtime.data_root))


def _fe_root():
    from tools.serve import comfy_shim      # 测试会把垫片的前端目录换成临时目录,两处保持一致
    return comfy_shim.FRONTEND_ROOT


@router.post("/workflows/frontend")
def install_frontend(request: Request):
    rt(request).start_frontend_install(_fe_root())
    return RedirectResponse("/workflows", status_code=303)


@router.post("/workflows/{category}/activate")
async def activate(request: Request, category: str):
    store = rt(request).store
    form = await request.form()
    raw = str(form.get("preset_id", ""))
    pid = int(raw) if raw.isdigit() else None
    label = presets.CATEGORIES.get(category)
    if label is None:
        raise ValueError(f"未知的模板类别 {category}")
    target = f"v{presets.get(store, pid)['version']}" if pid else "内置模板"
    if form.get("confirm") != "1":
        return confirm_page(request, summary=f"之后新规划的「{label}」任务都按 {target} 拼;已经排着的任务仍用它们绑定的那份",
                            action=f"/workflows/{category}/activate", fields={"preset_id": raw},
                            back="/workflows", panel_key="workflows")
    presets.activate(store, category, pid)
    journal.append(rt(request).data_root, [f"「{label}」当前模板改为 {target}"], prefix="workflows ")
    return RedirectResponse("/workflows", status_code=303)


# ── 编辑页 ─────────────────────────────────────────────────────

def _ready(request: Request) -> dict:
    """编辑页的前提:前端包已下载、节点清单抓过。不满足就 400,写明怎么办。"""
    frontend.require(_fe_root())
    m = nodecache.meta(rt(request).data_root)
    if m is None:
        nodecache.load(rt(request).data_root)          # 抛「先开云机刷新一次节点清单」
    return m


def _live_object_info(request: Request) -> tuple[dict, list[str]]:
    """云机在线:刷新节点清单再核(设计 §四 #11),并核对前端版本(#10,在线时硬拦);离线:用缓存。"""
    runtime = rt(request)
    online = [hid for hid, st in runtime.status.items() if st.comfy_ok]
    problems = []
    if online:
        try:
            m = runtime.refresh_object_info(online[0])
        except ValueError as e:
            return nodecache.load(runtime.data_root), [f"云机在线,但实时节点清单没取到:{e}"]
        if m["required_frontend_version"] != frontend.VERSION:
            problems.append(f"云机要求前端 {m['required_frontend_version']},本机是 {frontend.VERSION} —— 先升级前端再提交")
    return nodecache.load(runtime.data_root), problems


def _back_of(store, task) -> str:
    if task["target_type"] == "segment":
        ep = repo.get(store, "segment", task["target_id"])["episode_id"]
        panel = "videos" if task["kind"] == "video" else "frames"
        return f"/episode/{ep}/{panel}#seg-{task['target_id']}"
    return f"/drama/{repo.get(store, task['target_type'], task['target_id'])['drama_id']}/assets"


@router.get("/comfy-edit/task/{task_id}")
def edit_task(request: Request, task_id: int):
    runtime = rt(request)
    m = _ready(request)
    task = repo.get(runtime.store, "task", task_id)
    graph, rebuilt = rerun.source_graph(runtime.data_root, task)
    return render(request, "comfy_edit.html", "workflows", mode="task", title=f"改参重跑 · task #{task_id}",
                  graph=graph, token=secrets.token_hex(4), rebuilt=rebuilt, nodes=m, back=_back_of(runtime.store, task),
                  action=f"/comfy-edit/task/{task_id}")


@router.post("/comfy-edit/task/{task_id}")
async def submit_task(request: Request, task_id: int):
    runtime = rt(request)
    body = await request.json()
    problems = _token_problems(body)
    object_info, live = _live_object_info(request)
    problems += live
    if not problems:
        tid, problems = rerun.plan_rerun(runtime.store, runtime.data_root, task_id, body["graph"], object_info)
    if problems:
        return JSONResponse({"ok": False, "problems": problems})
    journal.append(runtime.data_root, [f"改参重跑:task {task_id} → 新任务 {tid}"], prefix="workflows ")
    return JSONResponse({"ok": True, "task_id": tid, "back": _back_of(runtime.store, repo.get(runtime.store, "task", tid))})


@router.get("/comfy-edit/preset/{category}")
def edit_preset(request: Request, category: str):
    store = rt(request).store
    m = _ready(request)
    if category not in presets.CATEGORIES:
        raise ValueError(f"未知的模板类别 {category}")
    cur = presets.active(store, category)
    title = f"模板 · {presets.CATEGORIES[category]} · 在{'v' + str(cur['version']) if cur else '内置模板'}的基础上改"
    return render(request, "comfy_edit.html", "workflows", mode="preset", title=title,
                  graph=presets.editing_graph(store, category), token=secrets.token_hex(4), rebuilt=False, nodes=m,
                  back="/workflows", action=f"/comfy-edit/preset/{category}")


@router.post("/comfy-edit/preset/{category}")
async def save_preset(request: Request, category: str):
    runtime = rt(request)
    body = await request.json()
    problems = _token_problems(body)
    object_info, live = _live_object_info(request)
    problems += live
    plan = presets.plan_save(runtime.store, category, body["graph"], object_info)
    problems += list(plan.problems)
    if problems:
        return JSONResponse({"ok": False, "problems": problems})
    if not body.get("confirm"):                      # 两态:先给人看改动清单
        return JSONResponse({"ok": False, "confirm": True, "lines": list(plan.lines)})
    pid = presets.confirm_save(runtime.store, plan, str(body.get("note", "")))
    v = presets.get(runtime.store, pid)["version"]
    journal.append(runtime.data_root, [f"保存模板预设「{presets.CATEGORIES[category]}」v{v}:" + ";".join(plan.lines)],
                   prefix="workflows ")
    return JSONResponse({"ok": True, "preset_id": pid, "version": v, "back": "/workflows"})


def _token_problems(body: dict) -> list[str]:
    if body.get("tab") != f"edit-{body.get('token')}":
        return [f"提交的不是本次打开的工作流(当前标签是「{body.get('tab')}」)—— 回到本次打开的那个标签再提交"]
    return []
