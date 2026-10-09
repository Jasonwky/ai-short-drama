# -*- coding: utf-8 -*-
"""提交把关。节点清单用真实清单的瘦身版(2026-10-08 抓自云机,只留我们会用到的节点)。"""
import copy
import json
from pathlib import Path

import pytest

from tools.gen import graph_checks
from tools.gen.workflows import build_h3, build_qwen

OBJECT_INFO = json.loads((Path(__file__).parents[2] / "tools/serve/fixtures/object_info_min.json").read_text(encoding="utf-8"))


@pytest.fixture
def qwen(tmp_path):
    (tmp_path / "assets").mkdir()
    for n in ("lin.png", "yard.png"):
        (tmp_path / "assets" / n).write_bytes(b"png")
    return build_qwen("Picture 1 Picture 2", ["assets/lin.png", "assets/yard.png"], 7, preset="fast", size=(928, 1664)), tmp_path


def _check(wf, root=None, **kw):
    args = {"object_info": OBJECT_INFO, "family": "qwen", "expect_ext": ".png", "size": (928, 1664), "data_root": root}
    return graph_checks.check(wf, **{**args, **kw})


def test_builtin_workflows_pass(qwen):
    wf, root = qwen
    assert _check(wf, root) == []
    h3 = build_h3("<Picture 1>", ["f.png"], None, 192, 1, size=(768, 1344), anchors=[("f.png", 0)])
    assert graph_checks.check(h3, object_info=OBJECT_INFO, family="h3", expect_ext=".mp4", size=(768, 1344),
                              frames=192) == []


def test_model_outside_the_whitelist_is_refused(qwen):
    """共用云机上的 flux1-dev 是非商用 —— 画布里选得到,提交时拦下。"""
    wf, root = qwen
    wf["1"] = {"class_type": "UNETLoader", "inputs": {"unet_name": "flux1-dev-fp8.safetensors", "weight_dtype": "default"}}
    problems = _check(wf, root)
    assert any("flux1-dev-fp8.safetensors" in p and "没有登记许可证" in p for p in problems)


def test_value_not_offered_by_the_cloud_is_refused(qwen):
    wf, root = qwen
    wf["27"]["inputs"]["sampler_name"] = "no_such_sampler"
    assert any("27.sampler_name" in p and "不在云机的可选项里" in p for p in _check(wf, root))


def test_exactly_one_output_of_the_right_kind(qwen):
    wf, root = qwen
    wf["90"] = {"class_type": "PreviewImage", "inputs": {"images": ["28", 0]}}
    assert any("要恰好 1 个输出节点" in p for p in _check(wf, root))
    del wf["90"], wf["29"]
    assert any("没有" in p for p in _check(wf, root))


def test_size_frames_and_seed(qwen):
    wf, root = qwen
    assert any("尺寸应为 1664x928" in p for p in _check(wf, root, size=(1664, 928)))
    two = copy.deepcopy(wf)
    two["91"] = {"class_type": "KSampler", "inputs": {**wf["27"]["inputs"], "seed": 8}}
    assert any("要恰好 1 个种子" in p for p in _check(two, root))
    h3 = build_h3("<Picture 1>", ["f.png"], None, 192, 1, size=(768, 1344))
    assert any("帧数应为 209" in p for p in graph_checks.check(h3, object_info=OBJECT_INFO, family="h3",
                                                              expect_ext=".mp4", size=(768, 1344), frames=209))


def test_reference_images_must_come_from_the_library(qwen):
    wf, root = qwen
    for bad in ("someone_elses_upload.png", "../escape.png", "C:/abs.png"):
        wf["18"]["inputs"]["image"] = bad
        assert any("不在素材库里" in p for p in _check(wf, root))
