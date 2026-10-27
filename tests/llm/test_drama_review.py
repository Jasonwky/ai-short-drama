# -*- coding: utf-8 -*-
"""全剧审的确定性部分(spec 2026-10-25-全剧审与资产表-design.md 第 2 节):前提、引用判卷、分段定位、输入上限、
批准时把采纳的问题排进修改回路(以及排不进去时拒批准)。产出由测试手写;LLM 契约在 test_contract_drama_review.py。"""
import pytest

from tools.drama import repo
from tools.drama.db import Store
from tools.llm import drama_review as dr, llm_queue, runs, service

EP1 = "【校场 · 白天】\n陈老三说：「来了。」\n燕无咎说：「我是伙夫。」"
EP2 = "【营地 · 夜】\n老陈说：「行。」\n燕无咎说：「我是伙夫。」"


@pytest.fixture
def drama():
    s = Store(":memory:")
    d = repo.create_drama(s, "将军", "9:16")
    e1 = repo.add_episode(s, d, 1, "script", script=EP1, outline="燕无咎进军营。")
    e2 = repo.add_episode(s, d, 2, "script", script=EP2)
    yield s, d, e1, e2
    s.close()


def _issue(where, quote, suggestion="统一叫陈老三", also=()):
    return {"where": where, "quote": quote, "category": "称呼不一致", "severity": "中", "suggestion": suggestion,
            "also": list(also)}


def test_script_stage_needs_every_script_and_carries_outlines(drama):
    s, d, e1, e2 = drama
    ctx = dr.context(s, d, "script")
    assert list(ctx["material"]) == ["第1集", "第2集"] and "燕无咎进军营" in ctx["reference"]
    repo.update(s, "episode", e2, script="")
    with pytest.raises(ValueError, match="第 2 集还没剧本"):
        dr.context(s, d, "script")
    with pytest.raises(ValueError, match="阶段"):
        dr.context(s, d, "nope")


def test_storyboard_and_assets_stages_need_their_material(drama):
    s, d, *_ = drama
    with pytest.raises(ValueError, match="还没分镜"):
        dr.context(s, d, "storyboard")
    with pytest.raises(ValueError, match="还没有资产"):
        dr.context(s, d, "assets")


def test_input_over_the_limit_is_refused_not_truncated(drama, monkeypatch):
    s, d, *_ = drama
    monkeypatch.setattr(dr, "MAX_INPUT_CHARS", 20)
    with pytest.raises(ValueError, match="超过单次上限 20 字"):
        dr.context(s, d, "script")


def test_judge_checks_quotes_and_also(drama):
    s, d, *_ = drama
    ctx = dr.context(s, d, "script")
    judge = dr.judge_factory(ctx)
    assert judge({"issues": [_issue("第2集", "老陈说：「行。」", also=[{"where": "第1集", "quote": "陈老三说"}])]}) == []
    problems = judge({"issues": [_issue("第1集", "老陈说"), _issue("第2集", "老陈", also=[{"where": "第1集", "quote": "编的"}])]})
    assert any("在第1集里找不到" in p for p in problems) and any("also 的引用「编的」" in p for p in problems)
    assert judge({"issues": []}) == ["问题要 1–20 条,现在 0 条"]


def _run(s, d, stage, issues, adopt, reject=()):
    params = {"stage": stage}
    rid = runs.start(s, task="drama_review", target_type="drama", target_id=d, model="m",
                     input_sha=service.prepare(s, "drama_review", d, params).sha, llm_mode="real", params=params)
    runs.propose(s, rid, {"issues": issues}, "j")
    return rid, {"adopt": list(adopt), "reject": list(reject)}


def test_adopted_script_issues_become_script_revisions(drama):
    s, d, e1, e2 = drama
    rid, dec = _run(s, d, "script", [_issue("第2集", "老陈说：「行。」"), _issue("第1集", "陈老三说")], [0], [1])
    service.approve(s, rid, decision=dec)
    item = llm_queue.next_waiting(s)
    assert (item["task"], item["target_id"]) == ("script_revise", e2)
    note = llm_queue.params(item)["notes"][0]
    assert note["target"] == "老陈说：「行。」" and note["text"].startswith("[称呼不一致] 统一叫陈老三")
    assert runs.applied(runs.get(s, rid))["queued"][0]["target_id"] == e2


def test_nothing_adopted_queues_nothing(drama):
    s, d, *_ = drama
    rid, dec = _run(s, d, "script", [_issue("第2集", "老陈说")], [], [0])
    service.approve(s, rid, decision=dec)
    assert llm_queue.next_waiting(s) is None and runs.get(s, rid)["state"] == "approved"


def test_unlocatable_quote_refuses_the_approval(drama):
    s, d, *_ = drama
    rid, dec = _run(s, d, "script", [_issue("第1集", "燕无咎说：「我是伙夫。」")], [0])
    s.conn.execute("UPDATE episode SET script = ? WHERE number = 1", (EP1 + "\n燕无咎说：「我是伙夫。」",))
    with pytest.raises(ValueError):          # 剧本变了:输入指纹先拦;没变时定位不到唯一一行也拦(见下)
        service.approve(s, rid, decision=dec)
    ctx = dr.context(s, d, "script")
    with pytest.raises(ValueError, match="定位不到唯一一行"):
        dr.plan_queue(s, {**ctx, "drama_id": d}, {"issues": [_issue("第1集", "燕无咎说：「我是伙夫。」")]}, {"adopt": [0]})


def test_busy_episode_or_too_many_notes_refuse_the_approval(drama):
    s, d, e1, e2 = drama
    llm_queue.enqueue(s, "script_revise", "episode", e2, {"notes": []})
    ctx = {**dr.context(s, d, "script"), "drama_id": d}
    with pytest.raises(ValueError, match="第2集的剧本修改已在队列里"):
        dr.plan_queue(s, ctx, {"issues": [_issue("第2集", "老陈说")]}, {"adopt": [0]})
    many = {"issues": [_issue("第1集", "陈老三说") for _ in range(11)]}
    with pytest.raises(ValueError, match="一集最多 10 条"):
        dr.plan_queue(s, ctx, many, {"adopt": list(range(11))})


def _boards(s, d, e1, e2):
    yard = repo.add_scene(s, d, "校场")
    for e in (e1, e2):
        repo.add_segment(s, e, 1, scene_id=yard, description="【镜头1】燕无咎扛着锅走进校场。")
        repo.add_segment(s, e, 2, scene_id=yard, description="【镜头1】霍猛冷笑。")


def test_storyboard_issues_are_located_to_one_segment_and_structure_changes_refused(drama):
    s, d, e1, e2 = drama
    _boards(s, d, e1, e2)
    ctx = {**dr.context(s, d, "storyboard"), "drama_id": d}
    judge = dr.judge_factory(ctx)
    assert judge({"issues": [_issue("第1集", "霍猛冷笑")]}) == []
    assert any("恰好一段" in p for p in judge({"issues": [_issue("第1集", "【镜头1】")]}))
    plan = dr.plan_queue(s, ctx, {"issues": [_issue("第2集", "霍猛冷笑", "改成近景")]}, {"adopt": [0]})
    assert plan == [("storyboard_revise", "episode", e2, [{"text": "[称呼不一致] 改成近景", "target": "2", "must": False}])]
    with pytest.raises(ValueError, match="要「拆成」段落"):
        dr.plan_queue(s, ctx, {"issues": [_issue("第2集", "霍猛冷笑", "拆成两段")]}, {"adopt": [0]})


def test_asset_issues_go_back_to_the_cast_task(drama):
    s, d, *_ = drama
    repo.add_character(s, d, "陈老三", appearance="中年")
    ctx = {**dr.context(s, d, "assets"), "drama_id": d}
    assert list(ctx["material"]) == ["角色·陈老三"]
    it = {**_issue("角色·陈老三", "中年"), "category": "造型与剧本冲突"}
    plan = dr.plan_queue(s, ctx, {"issues": [it]}, {"adopt": [0]})
    assert plan[0][:3] == ("cast", "drama", d) and plan[0][3][0]["text"].startswith("[造型与剧本冲突] 角色·陈老三:")


def test_reviewer_can_move_the_fix_to_an_also_episode(drama):
    """模型把「要改的那处」挂在先出现的那集(实战两轮都有):人选改哪一集,引用换成那一集的原文。"""
    s, d, e1, e2 = drama
    it = _issue("第1集", "燕无咎说：「我是伙夫。」", "后一集换句台词", also=[{"where": "第2集", "quote": "燕无咎说：「我是伙夫。」"}])
    args = {"issues": [it]}
    assert dr.decision_problems(args, {"adopt": [0], "reject": [], "fix": {"0": "第2集"}}) == []
    assert dr.decision_problems(args, {"adopt": [0], "reject": [], "fix": {"0": "第9集"}})
    ctx = {**dr.context(s, d, "script"), "drama_id": d}
    plan = dr.plan_queue(s, ctx, args, {"adopt": [0], "fix": {"0": "第2集"}})
    assert plan[0][2] == e2 and plan[0][3][0]["target"] == "燕无咎说：「我是伙夫。」"
    assert "另见第1集" in plan[0][3][0]["text"]


def test_wrong_scene_issues_cannot_go_into_storyboard_revisions(drama):
    """分镜修改锁场景:「场景用错」采纳了也改不动,批准时当场拒,让人去段落编辑页换(2026-10-27 实战)。"""
    s, d, e1, e2 = drama
    _boards(s, d, e1, e2)
    ctx = {**dr.context(s, d, "storyboard"), "drama_id": d}
    it = {**_issue("第2集", "霍猛冷笑", "改成伙房"), "category": dr.SCENE_WRONG}
    with pytest.raises(ValueError, match="段落编辑页换场景"):
        dr.plan_queue(s, ctx, {"issues": [it]}, {"adopt": [0]})
