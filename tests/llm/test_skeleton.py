# -*- coding: utf-8 -*-
"""骨架 LLM 任务(spec 2026-10-24-原创分集-design.md 第 1 节):立项 → 2–3 个整部骨架方案,人挑一个,代码分集号。
FakeLLM 不进这里:只测确定性路径(前提 / schema / 判卷 / 选择 / 写回 / 审阅页)。共用纯函数的测试在 test_skeleton_base.py。"""
import json

import pytest

from tools.drama import repo
from tools.llm import gate, runs
from tools.llm import skeleton as sk

PREMISE = {"title": "替嫁王妃", "logline": "冲喜新娘一针救活王爷", "genre": "古装权谋", "payoff": "打脸",
           "conflict": "身份错位", "hook_type": "直接冲突",
           "protagonist": {"name": "沈青禾", "tag": "庶女神医", "contrast": "表面柔弱实则狠"},
           "antagonist": "嫡姐", "hook": "大婚夜她跪在堂中被人羞辱"}


def _drama(store, premise=True):
    d = repo.create_drama(store, "替嫁", "9:16")
    repo.add_character(store, d, "沈青禾")
    if premise:
        repo.update(store, "drama", d, premise_json=json.dumps(PREMISE, ensure_ascii=False))
    return d


def _act(title, weight):
    return {"title": title, "goal": f"{title}的目标", "climax": f"{title}的大事件", "weight": weight}


def _cand(arc="逆袭升级", rhythm="前密后疏", ending="圆满", weights=(0.5, 0.3, 0.2), typicality=0.5):
    return {"arc": arc, "rhythm": rhythm, "ending": ending,
            "acts": [_act(f"幕{i}", w) for i, w in enumerate(weights, 1)], "typicality": typicality}


def _three():
    return {"candidates": [_cand(), _cand("复仇清算", "均匀推进", "开放", typicality=0.9),
                           _cand("身份揭晓", "层层加码", "反转", weights=(0.25, 0.25, 0.25, 0.25), typicality=0.2)]}


def _ctx(store, d=None, total=30, n=3, constraint=""):
    return sk.context(store, d if d is not None else _drama(store), total=total, n=n, constraint=constraint)


# ---- 前提 ----

def test_context_needs_an_approved_premise(store):
    d = _drama(store, premise=False)
    with pytest.raises(ValueError, match="脑爆立项"):
        sk.context(store, d, total=30, n=3, constraint="")


def test_context_bounds_and_contents(store):
    d = _drama(store)
    ctx = sk.context(store, d, total=30, n=3, constraint=" 结局要大团圆 ")
    assert ctx["total"] == 30 and ctx["n"] == 3 and ctx["constraint"] == "结局要大团圆"
    assert ctx["characters"] == ["沈青禾"] and "片名:替嫁王妃" in ctx["premise"]
    for total in (0, 201):
        with pytest.raises(ValueError, match="1–200"):
            sk.context(store, d, total=total, n=3, constraint="")
    for n in (1, 4):
        with pytest.raises(ValueError, match="2–3"):
            sk.context(store, d, total=30, n=n, constraint="")


def test_schema_is_strict_with_closed_dims_and_count(store):
    sch = sk.schema(_ctx(store, n=2))
    assert gate.strict_schema_problems(sch) == []
    cands = sch["properties"]["candidates"]
    assert cands["minItems"] == cands["maxItems"] == 2
    item = cands["items"]["properties"]
    for dim, values in sk.DIMS.items():
        assert item[dim]["enum"] == list(values)
    assert set(item["acts"]["items"]["properties"]) == {"title", "goal", "climax", "weight"}


def test_user_message_wraps_premise_as_data(store):
    msg = sk.user_message(_ctx(store, constraint="不要穿越"))
    assert "<premise>" in msg and "</premise>" in msg and "片名:替嫁王妃" in msg
    assert "30 集" in msg and "3 个方案" in msg and "不要穿越" in msg and "沈青禾" in msg


# ---- 判卷 ----

def test_judge_accepts_good_and_counts(store):
    judge = sk.judge_factory(_ctx(store))
    assert judge(_three()) == []
    out = _three()
    out["candidates"] = out["candidates"][:2]
    assert any("要 3 个方案" in p for p in judge(out))


def test_judge_needs_two_dims_apart(store):
    judge = sk.judge_factory(_ctx(store))
    out = _three()
    out["candidates"][1] = _cand(ending="开放")                     # 和方案 1 只差 1 个维度
    assert any("方案 1 和 2" in p for p in judge(out))


def test_judge_act_count_bounds_and_total(store):
    judge = sk.judge_factory(_ctx(store))
    out = _three()
    out["candidates"][0]["acts"] = [_act("独幕", 1.0)]
    assert any("方案 1" in p and "2–8" in p for p in judge(out))
    out = _three()
    out["candidates"][0]["acts"] = [_act(f"幕{i}", 0.1) for i in range(9)]
    assert any("方案 1" in p and "2–8" in p for p in judge(out))
    small = sk.judge_factory(_ctx(store, total=3))
    out = _three()                                                  # 方案 3 有 4 幕,总共只有 3 集
    assert any("方案 3" in p and "3 集" in p for p in small(out))


def test_judge_blank_fields_weight_and_typicality(store):
    judge = sk.judge_factory(_ctx(store))
    out = _three()
    out["candidates"][1]["acts"][0]["goal"] = " "
    assert any("方案 2" in p and "第 1 幕" in p and "目标" in p for p in judge(out))
    out = _three()
    out["candidates"][0]["acts"][2]["climax"] = ""
    assert any("方案 1" in p and "第 3 幕" in p and "幕末大事件" in p for p in judge(out))
    out = _three()
    out["candidates"][0]["acts"][1]["title"] = ""
    assert any("方案 1" in p and "第 2 幕" in p and "标题" in p for p in judge(out))
    for w in (0, -0.1, 1.2):
        out = _three()
        out["candidates"][2]["acts"][0]["weight"] = w
        assert any("方案 3" in p and "权重" in p for p in judge(out))
    out = _three()
    out["candidates"][2]["acts"][0]["weight"] = 1                   # 1 本身合法
    assert judge(out) == []
    out = _three()
    out["candidates"][0]["typicality"] = 1.5
    assert any("典型度" in p for p in judge(out))


# ---- 人的选择 ----

def test_decision_problems():
    args = _three()
    assert sk.decision_problems(args, {"pick": 0}) == [] and sk.decision_problems(args, {"pick": 2}) == []
    for bad in ({}, {"pick": 3}, {"pick": -1}, {"pick": True}, {"pick": "1"}, {"pick": 1.0}):
        assert sk.decision_problems(args, bad) != []


# ---- 写回 ----

def test_apply_writes_picked_skeleton_with_code_ranges(store):
    d = _drama(store)
    ctx = _ctx(store, d, total=10)
    sk.apply(store, d, _three(), ctx, {"pick": 0})
    got = sk.load(store, d)
    assert (got["arc"], got["rhythm"], got["ending"], got["total"]) == ("逆袭升级", "前密后疏", "圆满", 10)
    assert [(a["start"], a["end"]) for a in got["acts"]] == [(1, 5), (6, 8), (9, 10)]
    assert got["acts"][0] == {**_act("幕1", 0.5), "start": 1, "end": 5}
    assert "typicality" not in got


def test_apply_rechecks_premise(store):
    d = _drama(store)
    ctx = _ctx(store, d)
    repo.update(store, "drama", d, premise_json="")
    with pytest.raises(ValueError, match="脑爆立项"):
        sk.apply(store, d, _three(), ctx, {"pick": 0})
    assert sk.load(store, d) is None


# ---- 审阅页 ----

def _row(store, d, out, total=30, state="proposed", decision=None):
    rid = runs.start(store, task=sk.TASK, target_type="drama", target_id=d, model="m", input_sha="x",
                     llm_mode="real", params={"total": total, "n": len(out["candidates"]), "constraint": ""})
    runs.propose(store, rid, out, "j")
    if state == "approved":
        runs.approve(store, rid, decision)
    return runs.get(store, rid)


def test_preview_ranges_routine_paywall_and_kept(store):
    d = _drama(store)
    repo.add_episode(store, d, 1, "script", outline="第一集大纲")
    repo.add_episode(store, d, 2, "script")                         # 没大纲的不算
    pv = sk.preview(store, _row(store, d, _three(), total=30))
    assert pv["kept"] == 1 and pv["total"] == 30 and pv["picked"] is None
    c1, c2, c3 = pv["candidates"]
    assert [(a["start"], a["end"]) for a in c1["acts"]] == [(1, 15), (16, 24), (25, 30)]
    assert [c["routine"] for c in pv["candidates"]] == [False, True, False]
    assert "第 1 幕" in c1["paywall"] and "第 9–10 集" in c1["paywall"]
    assert "第 2 幕" in c3["paywall"]                                # 4 幕均分:1–8 / 9–15 …,第 9 集在第 2 幕
    assert c1["acts"][0]["title"] == "幕1"


def test_preview_paywall_across_acts_and_short_drama(store):
    d = _drama(store)
    out = _three()
    out["candidates"][0]["acts"] = [_act("甲", 0.3), _act("乙", 0.7)]  # 30 集 → 1–9 / 10–30,跨两幕
    pv = sk.preview(store, _row(store, d, out, total=30))
    assert "第 1 幕" in pv["candidates"][0]["paywall"] and "第 2 幕" in pv["candidates"][0]["paywall"]
    pv = sk.preview(store, _row(store, d, _three(), total=6))
    assert "不到第 9 集" in pv["candidates"][0]["paywall"]
    assert pv["kept"] == 0


def test_preview_shows_pick_when_approved(store):
    d = _drama(store)
    pv = sk.preview(store, _row(store, d, _three(), state="approved", decision={"pick": 2}))
    assert pv["picked"] == 2


# ---- 假样例 ----

@pytest.mark.parametrize("n", [2, 3])
def test_fake_sample_passes_schema_and_judge(store, n):
    ctx = _ctx(store, n=n, total=30)
    sch = sk.schema(ctx)
    sample = sk.fake_sample(sch)
    assert gate.schema_errors(sample, sch) == []
    assert sk.judge_factory(ctx)(sample) == []
