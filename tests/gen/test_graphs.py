# -*- coding: utf-8 -*-
"""工作流图的纯函数:改动提取 / 叠加 / 锁定比对。用我们自己拼出来的真实工作流做测试数据。"""
import copy

import pytest

from tools.gen import graphs
from tools.gen.workflows import build_h3, build_qwen


def _qwen(n_images=2, seed=7):
    return build_qwen(" ".join(f"Picture {i + 1}" for i in range(n_images)), [f"ref{i}.png" for i in range(n_images)],
                      seed, preset="fast", size=(928, 1664))


def _h3(n_images=2, anchors=1):
    return build_h3(" ".join(f"<Picture {i + 1}>" for i in range(n_images)), [f"r{i}.png" for i in range(n_images)],
                    None, 192, 9, size=(768, 1344), anchors=[("r0.png", 0), ("last.png", -1)][:anchors])


def test_changed_sampler_and_added_lora_are_extracted():
    base = _qwen()
    edited = copy.deepcopy(base)
    edited["27"]["inputs"]["sampler_name"] = "euler_ancestral"
    edited["31"] = {"class_type": "LoraLoaderModelOnly",
                    "inputs": {"model": ["9", 0], "lora_name": "x.safetensors", "strength_model": 0.8}}
    edited["27"]["inputs"]["model"] = ["31", 0]                      # 采样器改接到新 LoRA 上
    d, problems = graphs.diff(base, edited, "qwen")
    assert problems == []
    assert d["set"] == {"27": {"sampler_name": "euler_ancestral", "model": ["u1", 0]}}
    assert d["add"]["u1"]["inputs"]["model"] == ["9", 0] and d["remove"] == [] and d["unset"] == {}
    lines = graphs.summary(d, base)
    assert "节点 27 KSampler.sampler_name:'euler' → 'euler_ancestral'" in lines and "新增节点 u1 LoraLoaderModelOnly" in lines


def test_console_fields_are_ignored_and_owned_nodes_cannot_be_deleted():
    base = _qwen()
    edited = copy.deepcopy(base)
    edited["21"]["inputs"]["prompt"] = "画布里改的提示词"
    edited["27"]["inputs"]["seed"] = 99
    edited["30"]["inputs"]["width"] = 512
    d, problems = graphs.diff(base, edited, "qwen")
    assert graphs.is_empty(d) and problems == []                      # 提示词 / 种子 / 尺寸归控制台,改了不算
    del edited["18"]
    _, problems = graphs.diff(base, edited, "qwen")
    assert problems == ["节点 18(LoadImage)归控制台,不能删"]


def test_frontend_defaults_are_not_counted_as_edits():
    base = _h3()
    edited = copy.deepcopy(base)
    edited["23"]["inputs"]["color_space"] = "sRGB"                   # 实测:前端给没写的可选输入补默认值
    d, _ = graphs.diff(base, edited, "h3", default_of=lambda cls, name: "sRGB" if name == "color_space" else None)
    assert graphs.is_empty(d)


def test_diff_applies_to_other_slot_counts():
    """在 2 张参考图的标准例子上改的,叠到 1 张图 / 3 张图的拼法上照样成立。"""
    base = _qwen(2)
    edited = copy.deepcopy(base)
    edited["27"]["inputs"]["scheduler"] = "normal"
    d, _ = graphs.diff(base, edited, "qwen")
    for n in (1, 3):
        wf = graphs.apply(_qwen(n), d)
        assert wf["27"]["inputs"]["scheduler"] == "normal" and wf["27"]["inputs"]["seed"] == 7
        assert graphs.dangling(wf) == []


def test_apply_fails_loudly_when_it_cannot_land():
    with pytest.raises(ValueError, match="预设要改的节点 99 这次不存在"):
        graphs.apply(_qwen(), {"set": {"99": {"x": 1}}, "unset": {}, "add": {}, "remove": []})
    with pytest.raises(ValueError, match="连线断了"):
        graphs.apply(_qwen(), {"set": {}, "unset": {}, "add": {}, "remove": ["9"]})


def test_lock_allows_seed_but_not_prompt_size_or_anchors():
    src = _h3()
    edited = copy.deepcopy(src)
    edited["19"]["inputs"]["noise_seed"] = 123
    edited["18"]["inputs"]["steps"] = 30
    assert graphs.lock_problems(src, edited, "h3") == []
    edited["12"]["inputs"]["width"] = 512
    edited["41"]["inputs"]["frame_idx"] = 5
    problems = graphs.lock_problems(src, edited, "h3")
    assert any("12.width" in p for p in problems) and any("节点 41" in p for p in problems)


def test_models_seeds_images_and_steps_are_found():
    wf = _h3()
    assert {v for _, _, v in graphs.model_files(wf)} == {
        "minimax_h3_ref2va_pruned_fp8_scaled.safetensors", "qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
        "minimax_h3_video_vae_fp16.safetensors", "minimax_h3_audio_vae_fp32.safetensors"}
    assert graphs.seeds(wf) == [("19", "noise_seed", 9)]
    assert graphs.load_images(wf) == {"8": "r0.png", "9": "r1.png", "40": "r0.png"}
    assert graphs.with_images(wf, {"r0.png": "up/r0.png"})["40"]["inputs"]["image"] == "up/r0.png"
    assert graphs.steps(wf) == 20 and graphs.steps(_qwen()) is None
