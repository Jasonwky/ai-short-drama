# -*- coding: utf-8 -*-
"""ComfyUI 前端包(本机离线编辑工作流用,设计 §2.1)。

- **版本钉成云机要求的版本**(云机 `requirements.txt` 与 `/system_stats.required_frontend_version` 都是 1.51.9);
- **核对 PyPI 公布的 sha256**,对不上拒绝使用(防下到被换过的包);
- **GPL-3.0,不进仓库**:下载到 gitignore 的 `thirdparty/comfyui-frontend/<版本>/`,只在本机使用。
  wheel 里不带 LICENSE 文件,许可证原文见 github.com/Comfy-Org/ComfyUI_frontend(v1.51.9 标签,已核)。
"""
from __future__ import annotations

import hashlib
import io
import shutil
import urllib.request
import zipfile
from pathlib import Path

VERSION = "1.51.9"
URL = ("https://files.pythonhosted.org/packages/48/30/b7d9c1df063e56588ab11011fbf190ce7eac313437f48db5a937986c3b71/"
       "comfyui_frontend_package-1.51.9-py3-none-any.whl")
SHA256 = "979aa59bb1c3f972596d540b4efa437941d37cca3646b46018c96c01698ff085"   # PyPI 公布值,2026-10-08 核对
ROOT = Path(__file__).resolve().parents[2] / "thirdparty" / "comfyui-frontend"
_PREFIX = "comfyui_frontend_package/static/"


def static_dir(root: Path = ROOT, version: str = VERSION) -> Path:
    return root / version / "static"


def installed(root: Path = ROOT, version: str = VERSION) -> Path | None:
    d = static_dir(root, version)
    return d if (d / "index.html").is_file() else None


def require(root: Path = ROOT) -> Path:
    d = installed(root)
    if d is None:
        raise ValueError(f"本机还没有 ComfyUI 前端 {VERSION}(约 22 MB,GPL-3.0,不进仓库)—— "
                         "在「工作流」面板点「下载前端」,或运行 python -X utf8 -m tools.gen.frontend")
    return d


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=300) as r:
        return r.read()


def install(root: Path = ROOT, *, url: str = URL, sha256: str = SHA256, version: str = VERSION, fetch=_fetch) -> Path:
    """下载 → 核哈希 → 解出 static/ → 原子换上。哈希不对就什么都不写。"""
    data = fetch(url)
    got = hashlib.sha256(data).hexdigest()
    if got != sha256:
        raise ValueError(f"前端包 sha256 不对:期望 {sha256},实际 {got} —— 拒绝使用(可能被换过包)")
    tmp = root / f"{version}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            if name.startswith(_PREFIX) and not name.endswith("/"):
                dest = tmp / "static" / name[len(_PREFIX):]
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(z.read(name))
    if not (tmp / "static" / "index.html").is_file():
        shutil.rmtree(tmp, ignore_errors=True)
        raise ValueError("前端包里没有 static/index.html —— 包的结构变了,拒绝使用")
    final = root / version
    shutil.rmtree(final, ignore_errors=True)
    tmp.replace(final)
    return static_dir(root, version)


if __name__ == "__main__":
    print(f"已安装到 {install()}")
