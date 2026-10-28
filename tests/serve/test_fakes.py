# -*- coding: utf-8 -*-
from tools.gen.workflows import build_h3, build_qwen
from tools.serve.fakes import FakeComfy, FakeGpu


def test_fake_gpu_outputs_real_media():
    comfy = FakeComfy()
    img = comfy.submit(build_qwen("a", ["x.png"], 1))
    vid = comfy.submit(build_h3("<Picture 1> a", ["x.png"], None, 192, 1, size=(768, 1344)))
    assert FakeGpu(comfy).run_pending() == 2
    assert comfy.files[f"{img}.png"][:8] == b"\x89PNG\r\n\x1a\n"
    assert comfy.files[f"{vid}.mp4"][4:8] == b"ftyp"        # 可被 <video> 播放的 mp4 容器
    assert comfy.pending == [] and comfy.history(vid) is not None


def test_fake_gpu_hands_out_demo_products_in_turn():
    """自测给了真产物:视频轮流交,横版图当四视图、竖版当首帧。"""
    comfy = FakeComfy()
    gpu = FakeGpu(comfy, {"videos": [b"v1", b"v2"], "frames": [b"f1"], "fourview": b"fv"})
    a = comfy.submit({"1": {"inputs": {"noise_seed": 1}}})
    b = comfy.submit({"1": {"inputs": {"width": 1664, "height": 928}}})
    c = comfy.submit({"1": {"inputs": {"width": 768, "height": 1344}}})
    d = comfy.submit({"1": {"inputs": {"noise_seed": 2}}})
    assert gpu.run_pending() == 4
    got = [comfy.files[f"{a}.mp4"], comfy.files[f"{b}.png"], comfy.files[f"{c}.png"], comfy.files[f"{d}.mp4"]]
    assert got == [b"v1", b"fv", b"f1", b"v2"]
