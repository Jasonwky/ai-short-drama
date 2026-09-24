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
