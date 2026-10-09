# -*- coding: utf-8 -*-
"""验收脚本本身的测试:用假云机把整条链路跑一遍。真云机那一遍在开卡验收时跑。"""
import json

import pytest

from tools.serve.fakes import FakeAligner, FakeComfy, FakeTunnel, sample_mp4
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
    aligner = FakeAligner("这块玉佩是我母亲留给我的")
    return lambda store, host_id, root: Worker(store, host_id, data_root=root, client_for=lambda h: comfy,
                                               tunnel=FakeTunnel(), run_for=lambda h: None,
                                               align_for=lambda h: aligner)


def _gpu(comfy):
    """每次「睡一觉」,云机就把队列里的任务跑完:工作流里有 H3 采样器(noise_seed)的出 mp4,否则出 png。"""
    mp4 = sample_mp4()                     # 视频要能真解出音轨:字幕对齐在本机算能量曲线

    def sleep(_sec):
        for pid in list(comfy.pending):
            is_h3 = any("noise_seed" in n["inputs"] for n in comfy.submitted[pid].values())
            comfy.finish(pid, f"{pid}.mp4", mp4) if is_h3 else comfy.finish(pid, f"{pid}.png", pid.encode())
    return sleep


def test_full_chain_on_fake_gpu_writes_provenance(tmp_path, images):
    comfy = FakeComfy()
    root = tmp_path / "e2e"
    report = run(root, images, HOST, seed=5, timeout_sec=60, make_worker=_worker_on(comfy), sleep=_gpu(comfy))
    frame, fourview, video, align = report["tasks"]
    assert (frame["seed"], fourview["seed"], video["seed"]) == (5, 7, 6)
    assert report["first_frame_path"] == frame["result_path"] and report["video_path"] == video["result_path"]
    assert report["fourview_path"] == fourview["result_path"]
    assert json.loads(fourview["refs_json"]) == ["assets/character.png"]  # 四视图以正面图为输入
    # 视频吃的就是这次出的首帧(参考图 + 钉在第 0 帧)与四视图
    assert json.loads(video["refs_json"]) == [frame["result_path"], fourview["result_path"]]
    assert json.loads(video["params_json"])["anchors"] == [[frame["result_path"], 0]]
    assert json.loads(align["refs_json"]) == [video["result_path"]]     # 字幕对的就是这条视频
    assert report["subtitle_path"] == align["result_path"] and "这块玉佩" in (root / align["result_path"]).read_text(
        encoding="utf-8")
    assert json.loads((root / "provenance.json").read_text(encoding="utf-8"))["tasks"][2]["external_id"] == "p3"
    # 首帧用的是非内置模板预设:参数里绑定了它,实际提交的工作流叠上了改动
    assert report["preset"]["lines"] == ["节点 27 KSampler.scheduler:'simple' → 'normal'"]
    assert json.loads(frame["params_json"])["preset_id"] == report["preset"]["id"]
    assert comfy.submitted["p1"]["27"]["inputs"]["scheduler"] == "normal"
    assert "preset_id" not in json.loads(video["params_json"])                     # 视频没设预设:内置


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


class _SlowTunnel(FakeTunnel):
    """真隧道刚拉起时要过一会儿才通(2026-10-09 第 4 层真机:预设那一步第一次检查就判「隧道未通」退出)。"""
    def __init__(self, up_after):
        super().__init__()
        self.calls, self.up_after = 0, up_after

    def ensure(self, host):
        self.calls += 1
        return self.calls > self.up_after


def _worker_with_tunnel(comfy, tunnel):
    aligner = FakeAligner("这块玉佩是我母亲留给我的")
    return lambda store, host_id, root: Worker(store, host_id, data_root=root, client_for=lambda h: comfy,
                                               tunnel=tunnel, run_for=lambda h: None, align_for=lambda h: aligner)


def test_preset_step_waits_for_a_fresh_tunnel(tmp_path, images):
    comfy = FakeComfy()
    report = run(tmp_path / "e2e", images, HOST, seed=5, timeout_sec=60,
                 make_worker=_worker_with_tunnel(comfy, _SlowTunnel(up_after=3)), sleep=_gpu(comfy))
    assert report["preset"]["lines"] == ["节点 27 KSampler.scheduler:'simple' → 'normal'"]


def test_preset_step_gives_up_when_the_tunnel_never_comes_up(tmp_path, images):
    with pytest.raises(RuntimeError, match="隧道 60s 内没通"):
        run(tmp_path / "e2e", images, HOST, seed=5, timeout_sec=60,
            make_worker=_worker_with_tunnel(FakeComfy(), _SlowTunnel(up_after=10 ** 9)), sleep=lambda s: None)
