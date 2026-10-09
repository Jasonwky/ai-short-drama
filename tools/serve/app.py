# -*- coding: utf-8 -*-
"""控制台 FastAPI 应用。

- 只听 127.0.0.1(见 `tools.serve.console`),纯本地,绝不上传;
- 领域层抛的 `ValueError` 是「给人看的原因」(缺什么、为什么不行),统一渲染成 400 页面,不是 500;
  LLM 不可用(没有 key、连不上)渲染成 503,明确说不可用 —— 不静默切假 LLM;
- `/media/...` 只读地提供 data_root 里的素材与产物,拒绝跳出 data_root 的路径。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from tools.llm.client import LLMError
from tools.serve.panels import (assets, bundles, common, fill, final, frames, llm, machines, outline, picks, projects,
                                  script, storyboard, videos)
from tools.serve import comfy_shim
from tools.serve.runtime import Runtime

STATIC = Path(__file__).resolve().parent / "static"


def create_app(runtime: Runtime) -> FastAPI:
    app = FastAPI(title="AI 短剧控制台", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runtime = runtime
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
    for mod in (projects, outline, script, assets, storyboard, frames, videos, final, machines, picks, llm, fill,
                bundles, comfy_shim):
        app.include_router(mod.router)

    @app.exception_handler(ValueError)
    async def refused(request: Request, exc: ValueError):
        return common.render(request, "error.html", "", message=str(exc),
                             back=request.headers.get("referer") or "/", status_code=400)

    @app.exception_handler(LLMError)
    async def llm_down(request: Request, exc: LLMError):
        return common.render(request, "error.html", "", message=f"LLM 不可用:{exc}",
                             back=request.headers.get("referer") or "/", status_code=503)

    @app.exception_handler(KeyError)
    async def missing(request: Request, exc: KeyError):
        return common.render(request, "error.html", "", message=f"找不到:{exc.args[0] if exc.args else exc}",
                             back="/", status_code=404)

    @app.get("/media/{rel:path}")
    def media(rel: str):
        root = runtime.data_root.resolve()
        path = (root / rel).resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(404, "没有这个文件")
        return FileResponse(path)

    return app
