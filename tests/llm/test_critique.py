# -*- coding: utf-8 -*-
"""评审团(spec 2026-10-23-剧集脑爆系统-design.md 第 3a 节):一个视角一次,只出带原文引用的问题清单。"""
import pytest

from tools.drama import repo
from tools.llm import critique as cq
from tools.llm import runs

SCRIPT = ("【老宅庭院 · 黄昏】\n林小雨捧着玉佩低头凝视。\n林小雨说：「这块玉佩，是我母亲留给我的。」\n"
          "沈墨提灯走进院门。\n沈墨说：「天快黑了，你还不进屋？」\n")


def _ep(store, script=SCRIPT):
    d = repo.create_drama(store, "玉佩", "9:16")
    repo.add_character(store, d, "林小雨")
    repo.add_character(store, d, "沈墨")
    return d, repo.add_episode(store, d, 1, "script", script=script)


def _issue(quote="这块玉佩，是我母亲留给我的", category="动机不足", severity="中"):
    return {"quote": quote, "category": category, "severity": severity, "suggestion": "加一句她为什么珍惜"}


def test_each_role_has_its_own_closed_set_of_categories(store):
    _, ep = _ep(store)
    ctx = cq.context(store, ep, role="导演")
    cats = cq.schema(ctx)["properties"]["issues"]["items"]["properties"]["category"]["enum"]
    assert cats == list(cq.ROLES["导演"]) and "不可拍" in cats[0]
    with pytest.raises(ValueError, match="视角"):
        cq.context(store, ep, role="路人")
    with pytest.raises(ValueError, match="还没有剧本"):
        cq.context(store, _ep(store, script="")[1], role="观众")


def test_quotes_must_be_verbatim_from_the_script(store):
    _, ep = _ep(store)
    judge = cq.judge_factory(cq.context(store, ep, role="编剧"))
    assert judge({"issues": [_issue()]}) == []
    assert judge({"issues": [_issue(quote="这块玉佩,是我母亲留给我的")]}) == []     # 标点宽度不同照样认(verbatim)
    (p,) = judge({"issues": [_issue(quote="这玉佩是我娘给的")]})
    assert "原文里找不到" in p
    assert any("1–8" in p for p in judge({"issues": []}))


def test_every_issue_needs_an_adopt_or_reject(store):
    out = {"issues": [_issue(), _issue()]}
    assert cq.decision_problems(out, {"adopt": [0], "reject": [1]}) == []
    assert cq.decision_problems(out, {"adopt": [0], "reject": []}) == ["第 2 条还没表态(采纳或驳回)"]
    assert cq.decision_problems(out, {"adopt": [0], "reject": [0, 1]}) == ["第 1 条既采纳又驳回"]


def test_adoption_rate_per_role_comes_from_approved_decisions(store):
    d, ep = _ep(store)
    for role, decision in (("观众", {"adopt": [0], "reject": [1]}), ("观众", {"adopt": [], "reject": [0]}),
                           ("制片", {"adopt": [0], "reject": []})):
        rid = runs.start(store, task="critique", target_type="episode", target_id=ep, model="m", input_sha=role,
                         llm_mode="real", params={"role": role})
        runs.propose(store, rid, {"issues": [_issue()] * (len(decision["adopt"]) + len(decision["reject"]))}, "j")
        runs.approve(store, rid, decision)
    assert cq.adoption(store, d) == {"观众": (1, 3), "制片": (1, 1)}


def test_adopted_issues_are_listed_for_rewriting(store):
    _, ep = _ep(store)
    rid = runs.start(store, task="critique", target_type="episode", target_id=ep, model="m", input_sha="x",
                     llm_mode="real", params={"role": "编剧"})
    runs.propose(store, rid, {"issues": [_issue(), _issue(quote="天快黑了，你还不进屋？")]}, "j")
    runs.approve(store, rid, {"adopt": [1], "reject": [0]})
    (a,) = cq.adopted(store, ep)
    assert a["quote"] == "天快黑了，你还不进屋？" and a["role"] == "编剧" and a["run_id"] == rid


def test_decision_must_reference_existing_issues_once(store):
    out = {"issues": [_issue(), _issue()]}
    assert cq.decision_problems(out, {"adopt": [0, 5], "reject": [1]}) == ["第 6 条不存在(只有 2 条)"]
    assert cq.decision_problems(out, {"adopt": [0], "reject": [-1, 1]}) == ["第 0 条不存在(只有 2 条)"]
    assert cq.decision_problems(out, {"adopt": [0, 0], "reject": [1]}) == ["采纳里第 1 条重复了"]
    assert cq.decision_problems(out, {"adopt": [0], "reject": [1, "1"]}) == ["驳回里有不是序号的值:'1'"]
