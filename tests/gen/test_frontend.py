# -*- coding: utf-8 -*-
"""前端包安装:用一个现造的小 wheel 测,不联网。"""
import hashlib
import io
import zipfile

import pytest

from tools.gen import frontend


def _wheel(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_installs_only_after_the_hash_matches(tmp_path):
    whl = _wheel({"comfyui_frontend_package/static/index.html": "<html>", "comfyui_frontend_package/static/assets/a.js": "x",
                  "comfyui_frontend_package/__init__.py": ""})
    with pytest.raises(ValueError, match="本机还没有 ComfyUI 前端"):
        frontend.require(tmp_path)
    with pytest.raises(ValueError, match="sha256 不对"):
        frontend.install(tmp_path, sha256="0" * 64, fetch=lambda url: whl)
    assert frontend.installed(tmp_path) is None and not any(tmp_path.iterdir())     # 哈希不对什么都不写
    d = frontend.install(tmp_path, sha256=hashlib.sha256(whl).hexdigest(), fetch=lambda url: whl)
    assert (d / "index.html").read_text() == "<html>" and (d / "assets/a.js").is_file()
    assert frontend.require(tmp_path) == d and not (d.parent / "__init__.py").exists()


def test_a_wheel_without_index_is_refused(tmp_path):
    whl = _wheel({"comfyui_frontend_package/other.txt": "x"})
    with pytest.raises(ValueError, match="包的结构变了"):
        frontend.install(tmp_path, sha256=hashlib.sha256(whl).hexdigest(), fetch=lambda url: whl)
    assert frontend.installed(tmp_path) is None
