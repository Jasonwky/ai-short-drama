# -*- coding: utf-8 -*-
"""「工作流」面板与编辑页的服务端:前提检查、编辑令牌、改参重跑提交、预设两态保存、设为当前。
画布本身(iframe 里的 ComfyUI)由有头自测覆盖。"""
import copy
import time

import pytest

from tests.serve.fakes import FakeComfy
from tools.drama import repo
from tools.drama.tasks import tasks_for
from tools.gen import nodecache, presets
from tools.gen.align import plan_align
from tools.gen.planner import plan_first_frame
from tools.serve import comfy_shim, rerun
from tools.serve.dispatch import dispatch
from tools.serve.hosts import get_host


@pytest.fixture
def ready(runtime, tmp_path, monkeypatch):
    d = tmp_path / "fe" / "1.51.9" / "static"
    d.mkdir(parents=True)
    (d / "index.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(comfy_shim, "FRONTEND_ROOT", tmp_path / "fe")
    nodecache.refresh(runtime.data_root, FakeComfy(), "4080S")
    return runtime


@pytest.fixture
def frame_task(store, sample, host_id, ready):
    root = ready.data_root
    for rel in ("assets/lin.png", "assets/yard.png", "assets/jade.png"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"x")
    tid = plan_first_frame(store, sample["seg"], seed=7)
    dispatch(store, FakeComfy(), get_host(store, host_id), repo.get(store, "task", tid), data_root=root,
             run=lambda a, s: (0, ""))
    return tid


def test_panel_lists_categories_whitelist_and_editor_status(client, ready):
    page = client.get("/workflows").text
    for label in ("首帧 / 尾帧", "四视图", "资产图", "视频"):
        assert label in page
    assert 'data-preset-category="video"' in page and "当前:内置" in page
    assert "qwen-image-edit-2511-Q4_K_M.gguf" in page and "模型卡声明 apache-2.0" in page
    assert "已在本机" in page and "35 个节点类型" in page


def test_editor_needs_frontend_and_node_list(client, runtime, tmp_path, monkeypatch):
    monkeypatch.setattr(comfy_shim, "FRONTEND_ROOT", tmp_path / "none")
    r = client.get("/comfy-edit/preset/frame")
    assert r.status_code == 400 and "下载前端" in r.text
    assert "data-install-frontend" in client.get("/workflows").text
    d = tmp_path / "fe" / "1.51.9" / "static"
    d.mkdir(parents=True)
    (d / "index.html").write_text("x", encoding="utf-8")
    monkeypatch.setattr(comfy_shim, "FRONTEND_ROOT", tmp_path / "fe")
    r = client.get("/comfy-edit/preset/frame")
    assert r.status_code == 400 and "先开云机" in r.text


def test_install_button_runs_in_the_background(client, runtime, monkeypatch):
    from tools.gen import frontend
    calls = []
    monkeypatch.setattr(frontend, "install", lambda root: calls.append(root) or root)
    assert client.post("/workflows/frontend", follow_redirects=False).status_code == 303
    for _ in range(50):
        if runtime.frontend_job["state"] != "running":
            break
        time.sleep(0.05)
    assert runtime.frontend_job["state"] == "done" and len(calls) == 1


def test_history_offers_the_editor_only_for_comfyui_tasks(client, store, sample, frame_task):
    plan_align(store, sample["seg"])
    page = client.get(f"/episode/{sample['episode']}/frames").text
    assert f'href="/comfy-edit/task/{frame_task}"' in page
    assert all(rerun.editable(t) == (t["kind"] != "align") for t in tasks_for(store, "segment", sample["seg"]))


def test_task_editor_round_trip(client, store, sample, frame_task):
    page = client.get(f"/comfy-edit/task/{frame_task}").text
    assert "data-comfy-frame" in page and "assets/lin.png" in page and 'data-mode="task"' in page
    token = page.split('data-token="')[1].split('"')[0]
    graph, _ = rerun.source_graph(client.app.state.runtime.data_root, repo.get(store, "task", frame_task))
    edited = copy.deepcopy(graph)
    edited["27"]["inputs"]["seed"] = 4242
    wrong = client.post(f"/comfy-edit/task/{frame_task}", json={"graph": edited, "token": token, "tab": "Unsaved Workflow"})
    assert wrong.json()["ok"] is False and "不是本次打开的工作流" in wrong.json()["problems"][0]
    ok = client.post(f"/comfy-edit/task/{frame_task}", json={"graph": edited, "token": token, "tab": f"edit-{token}"}).json()
    assert ok["ok"] and repo.get(store, "task", ok["task_id"])["seed"] == 4242
    assert ok["back"] == f"/episode/{sample['episode']}/frames#seg-{sample['seg']}"
    edited["21"]["inputs"]["prompt"] = "改提示词"
    bad = client.post(f"/comfy-edit/task/{frame_task}", json={"graph": edited, "token": token, "tab": f"edit-{token}"}).json()
    assert any("21.prompt" in p for p in bad["problems"])


def test_preset_editor_is_two_state_and_activation_asks_first(client, store, ready):
    token = client.get("/comfy-edit/preset/frame").text.split('data-token="')[1].split('"')[0]
    edited = copy.deepcopy(presets.editing_graph(store, "frame"))
    edited["27"]["inputs"]["scheduler"] = "normal"
    body = {"graph": edited, "token": token, "tab": f"edit-{token}", "note": "试 normal"}
    first = client.post("/comfy-edit/preset/frame", json=body).json()
    assert first["confirm"] and first["lines"] == ["节点 27 KSampler.scheduler:'simple' → 'normal'"]
    assert presets.versions(store, "frame") == []                                    # 只看不存
    saved = client.post("/comfy-edit/preset/frame", json={**body, "confirm": True}).json()
    assert saved["ok"] and saved["version"] == 1
    page = client.get("/workflows").text
    assert "KSampler.scheduler" in page and "试 normal" in page

    r = client.post("/workflows/frame/activate", data={"preset_id": str(saved["preset_id"])})
    assert "之后新规划的「首帧 / 尾帧」任务都按 v1 拼" in r.text and presets.active(store, "frame") is None
    client.post("/workflows/frame/activate", data={"preset_id": str(saved["preset_id"]), "confirm": "1"})
    assert presets.active(store, "frame")["version"] == 1
    client.post("/workflows/frame/activate", data={"preset_id": "", "confirm": "1"})
    assert presets.active(store, "frame") is None
