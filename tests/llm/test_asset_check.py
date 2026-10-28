# -*- coding: utf-8 -*-
"""资产图 AI 检查的确定性部分(spec 2026-10-28-成片质量闸门-第一期-design.md 第 6.3 节):上下文、判卷、全剧对照拼图。
FakeLLM 不进来 —— LLM 契约在 test_contract_asset_check.py。"""
import subprocess

import pytest

from tools.drama import repo
from tools.llm import asset_check, service


def _png(path, color="0xF4F4F4", size=(464, 832)):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={size[0]}x{size[1]}",
                    "-vf", "drawbox=x=150:y=100:w=160:h=600:color=0x333333:t=fill", "-frames:v", "1", str(path)], check=True)


@pytest.fixture
def world(store, sample, tmp_path):
    for rel, size in (("assets/lin.png", (464, 832)), ("assets/lin_4v.png", (1664, 928)), ("assets/shen.png", (464, 832)),
                      ("assets/yard.png", (464, 832))):
        _png(tmp_path / rel, size=size)
    store.conn.execute("UPDATE drama SET art_json = ? WHERE id = ?",
                       ('{"era": "架空古代", "costume": "交领右衽", "banned": ["uniform"]}', sample["drama"]))
    return tmp_path


def _good(ctx):
    return {"check_number": str(ctx["check_number"]),
            "items": [{"dim": d, "verdict": "通过", "evidence": "", "fix": "无", "suggestion": ""} for d in ctx["dims"]]}


def test_context_carries_the_rule_art_and_dims(store, sample, world):
    ctx = asset_check.context(store, "character", sample["lin"], "image", world)
    assert ctx["dims"] == list(asset_check.DIMS) and ctx["art"]["era"] == "架空古代" and "全身" in ctx["rule"]
    msg = asset_check.user_message(ctx)
    assert "<art>\n画风:写实真人\n时代:架空古代" in msg and str(ctx["check_number"]) not in msg
    four = asset_check.context(store, "character", sample["lin"], "fourview", world)
    assert four["dims"][-1] == asset_check.SAME_PERSON and "正面图" in four["labels"][0]
    with pytest.raises(ValueError, match="已经换了"):
        asset_check.context(store, "character", sample["lin"], "image", world, path="assets/old.png")


def test_judge_wants_every_dim_evidence_and_the_check_number(store, sample, world):
    ctx = asset_check.context(store, "character", sample["lin"], "image", world)
    judge = asset_check.judge_factory(ctx)
    assert judge(_good(ctx)) == []
    bad = _good(ctx)
    bad["check_number"] = "000"
    bad["items"] = bad["items"][1:] + [{"dim": "瑕疵", "verdict": "不通过", "evidence": "", "fix": "无", "suggestion": ""}]
    problems = judge(bad)
    assert any("check_number 读错" in p for p in problems) and any("漏了维度:时代与服饰" in p for p in problems)
    assert any("维度重复:瑕疵" in p for p in problems) and any("没写证据" in p for p in problems)
    assert any("要选修法" in p for p in problems)
    fix = _good(ctx)
    fix["items"][0] = {"dim": "时代与服饰", "verdict": "不通过", "evidence": "胸前两个贴袋", "fix": "改描述",
                       "suggestion": "换成交领短褐"}
    assert any("完整的英文生成描述" in p for p in judge(fix))
    fix["items"][0]["suggestion"] = "Elderly camp veteran in a cross-collared hemp tunic tied with a sash."
    assert judge(fix) == [] and asset_check.failed(fix) == ["时代与服饰"]
    fix["items"][0]["suggestion"] = "Elderly camp veteran in a cross-collared hemp tunic, no uniform."
    assert any("含禁用词 uniform" in p for p in judge(fix))                            # 否定写法也拦:图像模型看到词就会画


def test_drama_sheet_numbers_cells_and_judge_checks_them(store, sample, world):
    repo.update(store, "character", sample["shen"], turnaround_path="assets/shen.png")
    ctx = asset_check.drama_context(store, sample["drama"], world)
    (sheet,) = ctx["sheets"]                                       # 场景只有一张,不拼
    assert sheet["cells"] == [(1, "林小雨"), (2, "沈墨")] and len(ctx["images"]) == 1
    judge = asset_check.judge_factory(ctx)
    ok = {"check_numbers": [str(ctx["check_numbers"][0])],
          "issues": [{"sheet": "角色正面图", "dim": "角色撞脸", "cells": [1, 2], "evidence": "两人脸型发型一样"}]}
    assert judge(ok) == []
    ok["issues"][0]["cells"] = [1, 7]
    assert any("没有格子 [7]" in p for p in judge(ok))
    ok["issues"][0]["cells"] = [1]
    assert any("至少两个不同的格子" in p for p in judge(ok))


def test_registered_as_vision_tasks_that_need_the_image():
    for t in ("character", "scene", "prop", "drama"):
        sp = service.spec(f"asset_check_{t}")
        assert sp.needs_root and sp.model == service.VISION_MODEL and sp.module is asset_check
