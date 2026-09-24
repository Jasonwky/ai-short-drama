# -*- coding: utf-8 -*-
"""验收脚本本身的测试:用假云机把整条链路跑一遍。真云机那一遍在开卡验收时跑。"""
import json

import pytest

from tools.serve.fakes import FakeComfy, FakeTunnel
from tools.serve.e2e_first_segment import run
from tools.serve.worker import Worker

HOST = {"label": "fake", "ssh_host": "h", "ssh_port": 1, "ssh_user": "root", "key_ref": "k",
        "local_port": 8188, "vram_gb": 24.0, "models_dir": "/m"}


@pytest.fixture
def images(tmp_path):
    out = {}
    for kind in ("character", "scene", "prop"):
        out[kind] = tmp_path / "src" / f"{kind}.png"
        out[kind].parent.mkdir(exist_ok=True)
        out[kind].write_bytes(kind.encode())
    return out


def _worker_on(comfy):
    return lambda store, host_id, root: Worker(store, host_id, data_root=root, client_for=lambda h: comfy,
                                               tunnel=FakeTunnel(), run_for=lambda h: None)


def _gpu(comfy):
    """每次「睡一觉」,云机就把队列里的任务跑完:工作流里有 H3 采样器(noise_seed)的出 mp4,否则出 png。"""
    def sleep(_sec):
        for pid in list(comfy.pending):
            is_h3 = any("noise_seed" in n["inputs"] for n in comfy.submitted[pid].values())
            ext = ".mp4" if is_h3 else ".png"
            comfy.finish(pid, f"{pid}{ext}", pid.encode())
    return sleep


def test_full_chain_on_fake_gpu_writes_provenance(tmp_path, images):
    comfy = FakeComfy()
    root = tmp_path / "e2e"
    report = run(root, images, HOST, seed=5, timeout_sec=60, make_worker=_worker_on(comfy), sleep=_gpu(comfy))
    frame, video = report["tasks"]
    assert (frame["seed"], video["seed"]) == (5, 6)
    assert report["first_frame_path"] == frame["result_path"] and report["video_path"] == video["result_path"]
    assert json.loads(video["refs_json"]) == [frame["result_path"]]     # 视频吃的就是这次出的首帧
    assert json.loads((root / "provenance.json").read_text(encoding="utf-8"))["tasks"][1]["external_id"] == "p2"


def test_refuses_a_non_empty_data_root(tmp_path, images):
    (tmp_path / "e2e").mkdir()
    (tmp_path / "e2e" / "old.db").write_bytes(b"x")
    with pytest.raises(FileExistsError):
        run(tmp_path / "e2e", images, HOST, seed=1, timeout_sec=1)


def test_failed_task_stops_the_chain(tmp_path, images):
    comfy = FakeComfy()
    comfy.reject_next_submit = "unet_name not in list"
    with pytest.raises(RuntimeError, match="unet_name not in list"):
        run(tmp_path / "e2e", images, HOST, seed=1, timeout_sec=60, make_worker=_worker_on(comfy),
            sleep=_gpu(comfy))


def test_timeout_tells_not_to_resubmit(tmp_path, images):
    comfy = FakeComfy()
    with pytest.raises(TimeoutError, match="不要重新提交"):
        run(tmp_path / "e2e", images, HOST, seed=1, timeout_sec=-1, make_worker=_worker_on(comfy),
            sleep=lambda s: None)
