# -*- coding: utf-8 -*-
"""骨架的共用纯函数(spec 2026-10-24-原创分集-design.md 第 4 节)。任务部分的测试在 test_skeleton.py。"""
import json

from tools.drama import repo
from tools.llm import skeleton as sk


def _skel(total, weights):
    acts = [{"title": f"第{i}幕", "goal": "目标", "climax": "大事件", "weight": w} for i, w in enumerate(weights, 1)]
    for a, (s, e) in zip(acts, sk.allocate(total, weights)):
        a.update(start=s, end=e)
    return {"arc": "复仇清算", "rhythm": "层层加码", "ending": "反转", "total": total, "acts": acts}


def test_allocate_by_weight_with_at_least_one_each():
    assert sk.allocate(10, [0.6, 0.3, 0.1]) == [(1, 6), (7, 9), (10, 10)]
    assert sk.allocate(3, [0.9, 0.05, 0.05]) == [(1, 1), (2, 2), (3, 3)]
    assert sk.allocate(100, [0.25, 0.25, 0.25, 0.25])[-1] == (76, 100)


def test_batches_split_long_acts_into_at_most_max_episodes():
    skel = _skel(100, [0.7, 0.3])                    # 第 1 幕 70 集 → 3 批(24/23/23);第 2 幕 30 集 → 1 批
    got = sk.batches(skel)
    assert [b[0] for b in got] == [1, 1, 1, 2]
    assert got[0] == (1, 1, 24) and got[2][2] == 70 and got[3] == (2, 71, 100)
    assert all(e - s + 1 <= 30 for _, s, e in got)


def test_act_for_and_render():
    skel = _skel(10, [0.6, 0.4])
    assert sk.act_for(skel, 7)[0] == 2 and sk.act_for(skel, 11) is None
    text = sk.render(skel)
    assert "主线:复仇清算" in text and "第 1 幕「第1幕」第 1–6 集" in text and "幕末:大事件" in text


def test_load_reads_the_drama_column(store):
    d = repo.create_drama(store, "替嫁", "9:16")
    assert sk.load(store, d) is None
    repo.update(store, "drama", d, skeleton_json=json.dumps(_skel(10, [0.5, 0.5]), ensure_ascii=False))
    assert sk.load(store, d)["total"] == 10
