# -*- coding: utf-8 -*-
import json
import os
import struct
import subprocess
import sys

from tools.gen.preflight import check, lora_pairs
from tools.gen.workflows import build_h3, build_qwen


def _safetensors(path, shapes: dict):
    """只写头的假 safetensors:lora_compat 只读文件头,不碰权重。"""
    header = json.dumps({k: {"dtype": "F32", "shape": v, "data_offsets": [0, 0]} for k, v in shapes.items()})
    path.write_bytes(struct.pack("<Q", len(header)) + header.encode())


def _wf(base, lora):
    return {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": base}},
            "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": lora, "model": ["1", 0]}}}


def _local_runner(args, stdin):
    """在本机用同样的方式执行:把程序源码从 stdin 喂给 `python -`。验证的就是远端那条路。"""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run([sys.executable, "-", *args], input=stdin, capture_output=True, env=env)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def _fail_if_called(args, stdin):
    raise AssertionError("不该去云机执行")


def test_h3_build_has_no_lora_so_nothing_to_check():
    wf = build_h3("<Picture 1> x", ["f.png"], None, 192, 1, size=(768, 1344))
    assert lora_pairs(wf) == ("minimax_h3_ref2va_pruned_fp8_scaled.safetensors", [])
    assert check(wf, "/m", _fail_if_called).ok


def test_gguf_base_is_reported_as_unverifiable_not_as_passed():
    wf = build_qwen("Picture 1", ["a.png"], 1)   # 模板里带 Lightning 与四视图两个 LoRA
    r = check(wf, "/m", _fail_if_called)
    assert r.ok and len(r.warnings) == 2
    assert all("无法核验" in w for w in r.warnings)


def test_real_shape_check_through_stdin_program(tmp_path):
    (tmp_path / "diffusion_models").mkdir()
    (tmp_path / "loras").mkdir()
    _safetensors(tmp_path / "diffusion_models" / "base.safetensors",
                 {"blocks.0.adaln_proj.linear.weight": [96768, 8]})
    _safetensors(tmp_path / "loras" / "bad.safetensors",
                 {"diffusion_model.blocks.0.adaln_proj.linear.lora_A.weight": [64, 2688],
                  "diffusion_model.blocks.0.adaln_proj.linear.lora_B.weight": [96768, 64]})
    _safetensors(tmp_path / "loras" / "good.safetensors",
                 {"diffusion_model.blocks.0.adaln_proj.linear.lora_A.weight": [64, 8],
                  "diffusion_model.blocks.0.adaln_proj.linear.lora_B.weight": [96768, 64]})
    root = tmp_path.as_posix()

    bad = check(_wf("base.safetensors", "bad.safetensors"), root, _local_runner)
    assert not bad.ok and "形状不兼容" in bad.problems[0] and "[96768, 2688]" in bad.problems[0]
    assert check(_wf("base.safetensors", "good.safetensors"), root, _local_runner).ok


def test_preflight_that_could_not_run_blocks():
    r = check(_wf("base.safetensors", "x.safetensors"), "/m", lambda a, s: (255, "Connection refused"))
    assert not r.ok and "不能核验就不放行" in r.problems[0]


def test_a_crash_is_not_mistaken_for_incompatible():
    # Python 未捕获异常退出码是 1 —— 它必须落到「没跑成」,不能被当成「核验出了不兼容」
    r = check(_wf("base.safetensors", "x.safetensors"), "/m", lambda a, s: (1, "Traceback ... UnicodeEncodeError"))
    assert not r.ok and "没跑成" in r.problems[0] and "形状不兼容" not in r.problems[0]


def test_remote_paths_follow_models_dir():
    seen = []
    check(_wf("base.safetensors", "x.safetensors"), "/root/models", lambda a, s: (seen.append(a), (0, ""))[1])
    assert seen == [["/root/models/diffusion_models/base.safetensors", "/root/models/loras/x.safetensors"]]
