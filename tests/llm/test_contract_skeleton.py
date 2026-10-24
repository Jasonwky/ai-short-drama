# -*- coding: utf-8 -*-
"""骨架任务的**真 LLM 契约测试**(spec 2026-10-24-原创分集-design.md 第 1 节;默认 `pytest -q` 不跑)。"""
import pytest

from tests.llm.test_contract_real import _gate, client  # noqa: F401
from tools.llm import diverge, skeleton

pytestmark = pytest.mark.real_llm

PREMISE = ("片名:替嫁王妃\n卖点:替嫁冲喜的庶女一针救活王爷,也救了自己\n"
           "题材:古装权谋 · 核心爽点:打脸 · 核心冲突:身份错位 · 开场钩子:直接冲突\n"
           "主角:沈青禾(庶女神医;反差:表面柔弱实则狠)\n对手:嫡姐沈玉瑶\n"
           "开场 3 秒:大婚夜她跪在堂中被人羞辱")


def test_skeleton_candidates_really_differ(client):  # noqa: F811
    """真模型给的骨架方案:方案数对、两两至少 2 个维度不同、幕数与权重过判卷(由 _gate 的判卷保证)。"""
    ctx = {"premise": PREMISE, "characters": ["沈青禾", "沈玉瑶", "萧珩"], "total": 30, "n": 3, "constraint": ""}
    res = _gate(client, "skeleton", skeleton, ctx)
    cands = res.args["candidates"]
    assert len(cands) == 3 and diverge.distinct_problems(cands, tuple(skeleton.DIMS), min_diff=2) == []
    assert all(skeleton.ACT_RANGE[0] <= len(c["acts"]) <= skeleton.ACT_RANGE[1] for c in cands)
