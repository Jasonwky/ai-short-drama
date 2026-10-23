# -*- coding: utf-8 -*-
"""方案发散的共用纯函数(spec 2026-10-23-剧集脑爆系统-design.md 第 0 节)。"""
import pytest

from tools.llm import diverge

DIMS = ("genre", "payoff")


def _c(genre, payoff, typicality=0.5, title="t"):
    return {"genre": genre, "payoff": payoff, "typicality": typicality, "title": title}


def test_candidates_must_differ_on_enough_dimensions():
    a, b = _c("古装", "打脸"), _c("都市", "逆袭")
    assert diverge.distinct_problems([a, b], DIMS, min_diff=2) == []
    (p,) = diverge.distinct_problems([a, _c("古装", "逆袭")], DIMS, min_diff=2)   # 只差爽点一个维度
    assert p.startswith("方案 1 和 2") and "题材" not in p and "genre" in p


def test_every_close_pair_is_reported():
    a, b, c = _c("古装", "打脸"), _c("都市", "逆袭"), _c("古装", "逆袭")
    got = diverge.distinct_problems([a, b, c], DIMS, min_diff=2)
    assert {p.split(":")[0] for p in got} == {"方案 1 和 3", "方案 2 和 3"}


def test_overlap_is_the_mean_share_of_equal_dimension_values():
    assert diverge.overlap([_c("古装", "打脸"), _c("都市", "逆袭")], DIMS) == 0.0
    assert diverge.overlap([_c("古装", "打脸"), _c("古装", "打脸")], DIMS) == 1.0
    assert diverge.overlap([_c("古装", "打脸"), _c("古装", "逆袭")], DIMS) == 0.5
    assert diverge.overlap([_c("古装", "打脸")], DIMS) == 0.0                 # 只有一个方案,谈不上趋同


def test_assemble_takes_each_field_from_the_chosen_candidate():
    cands = [_c("古装", "打脸", title="甲"), _c("都市", "逆袭", title="乙")]
    assert diverge.assemble(cands, {"title": 1, "genre": 0, "payoff": 1}) == \
        {"title": "乙", "genre": "古装", "payoff": "逆袭"}


def test_routine_is_the_most_typical_candidate():
    assert diverge.routine_index([_c("a", "b", 0.2), _c("c", "d", 0.9), _c("e", "f", 0.5)]) == 1
    assert diverge.routine_index([]) is None
