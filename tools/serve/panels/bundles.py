# -*- coding: utf-8 -*-
"""工程导出 / 导入(立项面板上的入口)。打包与校验逻辑在 `tools.drama.bundle`。"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import RedirectResponse

from tools.drama import bundle
from tools.serve import journal
from tools.serve.panels.common import rt

router = APIRouter()


def listing(data_root: Path) -> list[str]:
    """已导出的工程包(相对 data_root),新的在前。"""
    d = Path(data_root) / "outputs" / "bundles"
    return [p.relative_to(data_root).as_posix() for p in sorted(d.glob("*.zip"), reverse=True)] if d.is_dir() else []


@router.post("/drama/{drama_id}/bundle")
def export(request: Request, drama_id: int):
    runtime = rt(request)
    rel = bundle.export_bundle(runtime.store, runtime.data_root, drama_id)
    journal.append(runtime.data_root, [f"工程导出 drama {drama_id} → {rel}"], prefix="console ")
    return RedirectResponse("/#bundles", status_code=303)


@router.post("/bundles/import")
def import_(request: Request, file: UploadFile = File(...)):
    runtime = rt(request)
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d) / "bundle.zip"
        with tmp.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        new = bundle.import_bundle(runtime.store, runtime.data_root, tmp)
    journal.append(runtime.data_root, [f"工程导入 {file.filename} → drama {new}"], prefix="console ")
    return RedirectResponse(f"/drama/{new}/assets", status_code=303)
