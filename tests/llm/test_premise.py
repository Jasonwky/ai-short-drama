# -*- coding: utf-8 -*-
"""立项发散(spec 2026-10-23-剧集脑爆系统-design.md 第 1 节):一句话想法 → N 个维度上真不一样的方案,人逐字段挑 / 拼。"""
import json

import pytest

from tools.drama import repo
from tools.llm import premise as pm


def _drama(store):
    d = repo.create_drama(store, "替嫁", "9:16")
    repo.add_character(store, d, "沈青禾")
    return d


def _cand(genre="古装权谋", payoff="打脸", conflict="身份错位", hook_type="直接冲突", name="顾长歌",
          title="替嫁王妃", logline="冲喜新娘一针救活王爷", typicality=0.5):
    return {"title": title, "logline": logline, "genre": genre, "payoff": payoff, "conflict": conflict,
            "hook_type": hook_type, "protagonist": {"name": name, "tag": "庶女神医", "contrast": "表面柔弱实则狠"},
            "antagonist": "嫡姐", "hook": "大婚夜她跪在堂中被人羞辱", "typicality": typicality}


def _three():
    return {"candidates": [_cand(),
                           _cand("都市逆袭", "逆袭", "利益争夺", "强悬念", name="林知夏", title="甲", logline="乙"),
                           _cand("悬疑", "身份揭晓", "生死危机", "极致反差", name="苏念", title="丙", logline="丁")]}


def test_context_needs_an_idea_and_a_sane_count(store):
    d = _drama(store)
    ctx = pm.context(store, d, idea="替嫁冲喜的庶女其实是神医", n=3, constraint="")
    assert ctx["n"] == 3 and ctx["characters"] == ["沈青禾"]
    with pytest.raises(ValueError, match="想法"):
        pm.context(store, d, idea=" ", n=3, constraint="")
    with pytest.raises(ValueError, match="3–6"):
        pm.context(store, d, idea="一个想法够长了", n=9, constraint="")


def test_schema_dimensions_are_closed_sets(store):
    sch = pm.schema(pm.context(store, _drama(store), idea="替嫁冲喜的庶女其实是神医", n=3, constraint=""))
    item = sch["properties"]["candidates"]["items"]["properties"]
    for dim, values in pm.DIMS.items():
        assert item[dim]["enum"] == list(values)


def test_judge_counts_distinctness_compliance_and_name_clashes(store):
    judge = pm.judge_factory(pm.context(store, _drama(store), idea="替嫁冲喜的庶女其实是神医", n=3, constraint=""))
    assert judge(_three()) == []
    out = _three()
    out["candidates"] = out["candidates"][:2]
    assert any("要 3 个方案" in p for p in judge(out))
    out = _three()
    out["candidates"][1] = _cand(name="林知夏", title="甲", logline="乙")            # 和方案 1 维度全同
    assert any("方案 1 和 2" in p for p in judge(out))
    out = _three()
    out["candidates"][0]["title"] = "霸总的替嫁王妃"
    assert any("霸总" in p for p in judge(out))
    out = _three()
    out["candidates"][2]["protagonist"]["name"] = "沈青禾"                            # 和本剧已有角色重名
    assert any("沈青禾" in p and "重名" in p for p in judge(out))
    out = _three()
    out["candidates"][0]["typicality"] = 1.5
    assert any("典型度" in p for p in judge(out))


def test_judge_rejects_blank_fields_and_duplicate_protagonists(store):
    judge = pm.judge_factory(pm.context(store, _drama(store), idea="替嫁冲喜的庶女其实是神医", n=3, constraint=""))
    out = _three()
    out["candidates"][1]["protagonist"]["name"] = " "
    assert any("方案 2" in p and "主角名字" in p and "空" in p for p in judge(out))
    out = _three()
    out["candidates"][2]["logline"] = ""
    assert any("方案 3" in p and "一句话卖点" in p and "空" in p for p in judge(out))
    out = _three()
    out["candidates"][0]["antagonist"] = ""
    assert any("方案 1" in p and "对手" in p for p in judge(out))
    out = _three()
    out["candidates"][2]["protagonist"]["name"] = "林知夏"                            # 和方案 2 主角同名
    assert any("方案 2 和 3 的主角同名" in p for p in judge(out))


def test_decision_rejects_malformed_picks():
    out = _three()
    full = {f: 0 for f in pm.FIELDS}
    assert pm.decision_problems(out, {"assemble": {**full, "nope": 0}}) != []
    assert pm.decision_problems(out, {"assemble": {**full, "title": 1.0}}) != []
    assert pm.decision_problems(out, {"assemble": {**full, "title": -1}}) != []
    assert pm.decision_problems(out, {"assemble": [0]}) != []
    assert pm.decision_problems(out, {}) != []


def test_decision_picks_one_candidate_per_field():
    out = _three()
    full = {f: 0 for f in pm.FIELDS}
    assert pm.decision_problems(out, {"assemble": full}) == []
    assert pm.decision_problems(out, {"assemble": {**full, "title": 5}}) != []
    assert pm.decision_problems(out, {"assemble": {**full, "title": True}}) != []
    missing = {f: 0 for f in pm.FIELDS if f != "hook"}
    assert any("hook" in p or "开场钩子" in p for p in pm.decision_problems(out, {"assemble": missing}))


def test_apply_writes_the_assembled_premise_and_keeps_the_title(store):
    d = _drama(store)
    ctx = pm.context(store, d, idea="替嫁冲喜的庶女其实是神医", n=3, constraint="")
    picks = {f: 0 for f in pm.FIELDS} | {"title": 2, "genre": 1}
    pm.apply(store, d, _three(), ctx, {"assemble": picks})
    saved = json.loads(repo.get(store, "drama", d)["premise_json"])
    assert saved["title"] == "丙" and saved["genre"] == "都市逆袭" and saved["payoff"] == "打脸"
    assert repo.get(store, "drama", d)["title"] == "替嫁"                           # 剧名不动(spec 差异见计划)
    assert "丙" in pm.render(saved) and "都市逆袭" in pm.render(saved)


def test_rerun_after_approval_sees_the_current_premise(store):
    from tools.llm import service
    d = _drama(store)
    params = {"idea": "替嫁冲喜的庶女其实是神医", "n": 3, "constraint": ""}
    before = service.prepare(store, "premise", d, params)
    assert "当前立项:(还没有)" in before.user
    pm.apply(store, d, _three(), before.ctx, {"assemble": {f: 0 for f in pm.FIELDS}})
    after = service.prepare(store, "premise", d, params)
    assert "替嫁王妃" in after.user and before.sha != after.sha
