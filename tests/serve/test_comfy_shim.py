# -*- coding: utf-8 -*-
"""ComfyUI 前端的本机垫片:前端要的接口在本机回答,画布里运行 / 上传一律拒绝,数据不外发。"""
import pytest

from tools.gen import nodecache
from tools.serve import comfy_shim
from tools.serve.fakes import FakeComfy


@pytest.fixture
def fe(tmp_path, monkeypatch):
    d = tmp_path / "fe" / "1.51.9" / "static"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<html>comfy</html>", encoding="utf-8")
    (d / "assets" / "favicon.ico").write_bytes(b"ico")
    monkeypatch.setattr(comfy_shim, "FRONTEND_ROOT", tmp_path / "fe")
    return d


def test_frontend_is_served_under_comfy_with_csp(client, fe):
    r = client.get("/comfy/")
    assert r.text == "<html>comfy</html>"
    csp = r.headers["Content-Security-Policy"]
    assert "connect-src 'self' data: blob: ws://127.0.0.1:" in csp and "comfy.org" not in csp
    assert client.get("/assets/favicon.ico").content == b"ico"            # 图标仍请求根路径(实测)
    assert client.get("/assets/nothing.png").status_code == 204
    assert client.get("/comfy/../app.py").status_code == 404


def test_missing_frontend_is_a_readable_400(client, tmp_path, monkeypatch):
    monkeypatch.setattr(comfy_shim, "FRONTEND_ROOT", tmp_path / "none")
    r = client.get("/comfy/")
    assert r.status_code == 400 and "下载前端" in r.text


def test_remote_config_points_back_home_and_forced_settings(client, fe):
    f = client.get("/comfy/api/features").json()
    assert f["comfy_api_base_url"].endswith("/comfy/offline-api") and "comfy.org" not in str(f)
    assert client.get("/comfy/offline-api/releases").json() == []
    client.post("/comfy/api/settings/Comfy.Workflow.Persist", json=True)    # 想打开恢复 —— 不许
    client.post("/comfy/api/settings/Comfy.Locale", json="zh")
    s = client.get("/comfy/api/settings").json()
    assert s["Comfy.Workflow.Persist"] is False and s["Comfy.Notification.ShowVersionUpdates"] is False
    assert s["Comfy.Locale"] == "zh"                                         # 别的设置照存


def test_running_or_uploading_in_the_canvas_is_refused(client, fe):
    r = client.post("/comfy/api/prompt", json={"prompt": {}})
    assert r.status_code == 400 and "回控制台" in r.json()["error"]["message"]
    assert client.post("/comfy/api/upload/image").status_code == 400


def test_node_list_comes_from_the_local_cache(client, runtime, fe):
    assert client.get("/comfy/api/object_info").status_code == 400          # 没缓存:明确报错
    nodecache.refresh(runtime.data_root, FakeComfy(), "x")
    assert "MiniMaxH3AddGuide" in client.get("/comfy/api/object_info").json()
    assert client.get("/comfy/api/jobs").json()["jobs"] == []             # 任务列表要对象(前端 zod 校验)
    for path in ("extensions", "experiment/models", "embeddings"):
        assert client.get(f"/comfy/api/{path}").json() == []                 # 列表接口回数组(回对象前端初始化会崩)
    assert client.get("/comfy/api/userdata/user.css").text == "" == client.get("/comfy/user.css").text
    assert client.get("/comfy/api/userdata/comfy.templates.json").json() == []   # 没有文件也不回 404


def test_userdata_rejects_path_traversal(client, fe):
    assert client.post("/comfy/api/userdata/..%2F..%2Fevil.txt", content=b"x").status_code in (400, 404)
    assert client.post("/comfy/api/userdata/comfy.templates.json", content=b"{}").status_code == 200
    assert client.get("/comfy/api/userdata/comfy.templates.json").content == b"{}"


def test_websocket_says_queue_is_empty(client, fe):
    with client.websocket_connect("/comfy/ws") as ws:
        assert ws.receive_json()["data"]["status"]["exec_info"]["queue_remaining"] == 0


def test_view_serves_library_images_or_a_gray_placeholder(client, runtime, fe):
    (runtime.data_root / "assets").mkdir()
    (runtime.data_root / "assets" / "lin.png").write_bytes(b"real")
    assert client.get("/comfy/api/view", params={"filename": "lin.png", "subfolder": "assets", "type": "input"}).content == b"real"
    gray = client.get("/comfy/api/view", params={"filename": "ref1.png", "subfolder": "placeholder", "type": "input"})
    assert gray.status_code == 200 and gray.content[:4] == b"\x89PNG"
    escape = client.get("/comfy/api/view", params={"filename": "app.py", "subfolder": "../../..", "type": "input"})
    assert escape.content[:4] == b"\x89PNG"                                  # 跳出素材根:只给灰图
