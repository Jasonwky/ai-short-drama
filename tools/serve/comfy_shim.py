# -*- coding: utf-8 -*-
"""ComfyUI 前端的本机轻量后端垫片(设计 §2.1–2.2;实测见 spike-results/2026-10-08-ComfyUI前端本机离线编辑实测.md)。

前端挂在 `/comfy/`(与控制台同源,编辑页用 iframe 嵌入、经 `contentWindow.app` 载入 / 导出工作流)。
它要的接口在这里就地回答 —— **不连云机**,所以云机关着也能编辑:

- 节点清单用本机缓存(`nodecache`);设置、用户数据存 `<data_root>/comfy_cache/`;
- 强制设置:不恢复上次打开的工作流(`Comfy.Workflow.Persist`,配合编辑令牌)、不显示版本更新;
- **数据不外发**:前端的「远程配置」(`/api/features`)把 comfy.org 的地址指回本机 `/comfy/offline-api/*`;
  `index.html` 另带 CSP 兜底(只许连本机)—— 实测前端否则会请求 api.comfy.org;
- 画布里「运行」(`/api/prompt`)、上传图片(`/api/upload/image`)一律拒绝:提交只走控制台任务表。
"""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse, Response

from tools.gen import frontend, nodecache
from tools.gen.assets import _png

router = APIRouter()
FRONTEND_ROOT = frontend.ROOT          # 测试里换成临时目录
FORCED_SETTINGS = {"Comfy.Workflow.Persist": False, "Comfy.Notification.ShowVersionUpdates": False,
                   "Comfy.TutorialCompleted": True}
# 这些接口前端要的是**数组**:回对象会在初始化时崩(实测 extensions 回 {} → loadExtensions 里 r.filter is not a function)
_LISTS = {"extensions", "workflow_templates", "global_subgraphs", "experiment/models", "userdata", "embeddings"}
_NO_JOBS = {"jobs": [], "pagination": {"total": 0, "offset": 0, "limit": 0, "has_more": False}}   # 前端用 zod 校验:要对象
# 文件不存在时真 ComfyUI 回 404;这里回空内容 —— 404 会进有头自测的 console 报错
_EMPTY_USERDATA = {"user.css": ("", "text/css"), "comfy.templates.json": ("[]", "application/json")}
_SAFE_NAME = re.compile(r"^[\w.\-]+$")
_GRAY = _png(64, 64, 128)
REFUSE_RUN = "画布里不能直接运行 —— 回控制台点「提交为新版本」/「保存为预设」(提交要过把关、进任务表才有溯源)"
REFUSE_UPLOAD = "画布里不能上传图片 —— 参考图只认素材库里的,请去资产面板上传"


def _cache_dir(request: Request):
    d = request.app.state.runtime.data_root / nodecache.DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _settings(request: Request) -> dict:
    f = _cache_dir(request) / "settings.json"
    stored = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    return {**stored, **FORCED_SETTINGS}


def _refuse(message: str) -> JSONResponse:
    # ComfyUI 后端的报错格式:前端会把 message 原样弹给人看
    return JSONResponse({"error": {"type": "refused", "message": message, "details": ""}, "node_errors": {}},
                        status_code=400)


def _csp(request: Request) -> str:
    port = request.url.port or 80
    return ("default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; "
            f"connect-src 'self' data: blob: ws://127.0.0.1:{port} ws://localhost:{port}")


@router.websocket("/comfy/ws")
async def ws(sock: WebSocket):
    """空连接:先报一条「队列为 0」再挂着 —— 前端就不会一直重连刷报错。"""
    await sock.accept()
    await sock.send_json({"type": "status", "data": {"status": {"exec_info": {"queue_remaining": 0}}, "sid": "local"}})
    try:
        while True:
            await sock.receive_text()
    except Exception:          # 对端关掉页面:正常结束,没有要处理的
        return


@router.api_route("/comfy/offline-api/{rest:path}", methods=["GET", "POST"])
def offline_api(rest: str):
    """前端以为在访问 comfy.org(版本更新、节点注册表……),一律空数组。"""
    return JSONResponse([])


@router.api_route("/comfy/api/{rest:path}", methods=["GET", "POST"])
async def api(rest: str, request: Request):
    method = request.method
    if rest == "prompt" and method == "POST":
        return _refuse(REFUSE_RUN)
    if rest.startswith("upload/"):
        return _refuse(REFUSE_UPLOAD)
    if rest == "object_info":
        return JSONResponse(nodecache.load(request.app.state.runtime.data_root))
    if rest == "features":
        local = str(request.base_url).rstrip("/") + "/comfy/offline-api"
        return JSONResponse({"supports_preview_metadata": True, "comfy_api_base_url": local,
                             "comfy_platform_base_url": local})
    if rest == "users":
        return JSONResponse({"storage": "server", "migrated": True})
    if rest == "system_stats":
        m = nodecache.meta(request.app.state.runtime.data_root) or {}
        return JSONResponse({"system": {"os": "local", "comfyui_version": m.get("comfyui_version", ""),
                                        "required_frontend_version": frontend.VERSION, "argv": []}, "devices": []})
    if rest == "settings":
        if method == "POST":
            _save_settings(request, await request.json())
        return JSONResponse(_settings(request))
    if rest.startswith("settings/"):
        if method == "POST":
            _save_settings(request, {rest[len("settings/"):]: await request.json()})
            return JSONResponse({})
        return JSONResponse(_settings(request).get(rest[len("settings/"):]))
    if rest.startswith("userdata/"):
        name = rest[len("userdata/"):]
        if not _SAFE_NAME.match(name):
            return JSONResponse({"error": "文件名不合法"}, status_code=400)       # 防路径穿越:只许单层普通文件名
        f = _cache_dir(request) / "userdata" / name
        if method == "POST":
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(await request.body())
            return JSONResponse({})
        if f.is_file():
            return Response(f.read_bytes())
        if name in _EMPTY_USERDATA:
            content, media = _EMPTY_USERDATA[name]
            return Response(content=content, media_type=media)
        return Response(status_code=404)
    if rest in _LISTS:
        return JSONResponse([])
    if rest == "jobs":
        return JSONResponse(_NO_JOBS)
    if rest == "view":
        return _view(request)
    return JSONResponse({})


def _view(request: Request) -> Response:
    """「加载图像」节点的缩略图:素材库里有这张图就给真图(改参重跑时看得见用的是哪张),
    没有(模板编辑的占位图)就给一张灰图 —— 404 会进有头自测的 console 报错。"""
    root = request.app.state.runtime.data_root.resolve()
    q = request.query_params
    f = (root / q.get("subfolder", "") / q.get("filename", "")).resolve()
    if root in f.parents and f.is_file():
        return FileResponse(f)
    return Response(content=_GRAY, media_type="image/png")


def _save_settings(request: Request, values: dict) -> None:
    f = _cache_dir(request) / "settings.json"
    stored = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    stored.update({k: v for k, v in values.items() if k not in FORCED_SETTINGS})
    f.write_text(json.dumps(stored, ensure_ascii=False, indent=1), encoding="utf-8")


@router.get("/assets/{rest:path}")
def root_assets(rest: str):
    """前端挂在子路径下时,只有两个图标仍请求根路径 /assets/(实测)—— 接住,别让 404 进有头自测。"""
    d = frontend.installed(FRONTEND_ROOT)
    f = d / "assets" / rest if d else None
    return FileResponse(f) if f and f.is_file() else Response(status_code=204)


@router.get("/comfy/user.css")
def user_css():
    return Response("", media_type="text/css")


@router.get("/comfy/{rest:path}")
def static(rest: str, request: Request):
    d = frontend.require(FRONTEND_ROOT)
    f = (d / (rest or "index.html")).resolve()
    if d.resolve() not in f.parents or not f.is_file():
        return Response(status_code=404)
    r = FileResponse(f)
    if f.name == "index.html":
        r.headers["Content-Security-Policy"] = _csp(request)
    return r
