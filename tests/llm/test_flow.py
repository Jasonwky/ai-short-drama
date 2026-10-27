# -*- coding: utf-8 -*-
"""全剧流程推进(spec 2026-10-25-全剧审与资产表-design.md 第 3、4 节):各关状态、只排不批、幂等、防空转、定稿后出图 / 声音 / 拆分镜。"""
import pytest

from tools.drama import repo
from tools.drama.db import Store
from tools.llm import flow, llm_queue, runs, service

EP1 = "【校场 · 白天】\n陈老三说：「来了。」\n燕无咎说：「我是伙夫。」"
EP2 = "【营地 · 夜】\n陈老三说：「行。」\n燕无咎说：「好。」"
FP = "A lean soldier in worn leather armor."


@pytest.fixture
def drama():
    s = Store(":memory:")
    d = repo.create_drama(s, "将军", "9:16", "realistic")
    e1 = repo.add_episode(s, d, 1, "script", script=EP1)
    e2 = repo.add_episode(s, d, 2, "script", script=EP2)
    yield s, d, e1, e2
    s.close()


def _st(s, d):
    return {x.key: x for x in flow.status(s, d)}


def _queue(s):
    return [(r["task"], r["target_type"], r["target_id"], llm_queue.params(r))
            for r in s.conn.execute("SELECT * FROM llm_queue WHERE state = 'waiting' ORDER BY id")]


def _take(s, task, target, params, out, decision=None):
    """模拟队列出了这一条、LLM 交了 out、人批准(decision)。"""
    s.conn.execute("DELETE FROM llm_queue WHERE task = ?", (task,))
    rid = runs.start(s, task=task, target_type=service.spec(task).target, target_id=target, model="m",
                     input_sha=service.prepare(s, task, target, params).sha, llm_mode="real", params=params)
    runs.propose(s, rid, out, "j")
    if decision is not False:
        service.approve(s, rid, decision=decision)
    return rid


def _issue(where, quote):
    return {"where": where, "quote": quote, "category": "称呼不一致", "severity": "中", "suggestion": "改", "also": []}


def test_nothing_happens_until_the_drama_turns_auto_on(drama):
    s, d, *_ = drama
    assert flow.advance(s, d) == [] and _queue(s) == []
    assert _st(s, d)["r1"].state == "ready"


def test_review_loop_until_nothing_is_adopted(drama):
    s, d, e1, e2 = drama
    repo.update(s, "drama", d, flow_auto=1)
    assert flow.advance(s, d) and _queue(s) == [("drama_review", "drama", d, {"stage": "script"})]
    assert flow.advance(s, d) == [] and len(_queue(s)) == 1                      # 幂等
    _take(s, "drama_review", d, {"stage": "script"}, {"issues": [_issue("第2集", "陈老三说：「行。」")]},
          {"adopt": [0], "reject": []})
    assert _queue(s)[0][:3] == ("script_revise", "episode", e2) and _st(s, d)["r1"].state == "fixing"
    s.conn.execute("DELETE FROM llm_queue")                                       # 修改被人驳回 / 没改成
    assert _st(s, d)["r1"].state == "stuck" and flow.advance(s, d) == []           # 剧本没变:不空转
    repo.update(s, "episode", e2, script=EP2.replace("行。", "行,走吧。"))
    assert _st(s, d)["r1"].state == "ready" and flow.advance(s, d)
    _take(s, "drama_review", d, {"stage": "script"}, {"issues": [_issue("第1集", "陈老三说")]},
          {"adopt": [], "reject": [0]})
    st = _st(s, d)
    assert st["r1"].state == "passed" and st["cast"].state == "ready"
    flow.advance(s, d)
    assert _queue(s) == [("cast", "drama", d, {"notes": []})]


def test_a_failed_run_is_not_retried_automatically(drama):
    s, d, *_ = drama
    repo.update(s, "drama", d, flow_auto=1)
    flow.advance(s, d)
    s.conn.execute("DELETE FROM llm_queue")
    rid = runs.start(s, task="drama_review", target_type="drama", target_id=d, model="m", llm_mode="real",
                     input_sha=service.prepare(s, "drama_review", d, {"stage": "script"}).sha, params={"stage": "script"})
    runs.fail(s, rid, "GateFailed: 判卷没过")
    assert _st(s, d)["r1"].state == "failed" and flow.advance(s, d) == [] and _queue(s) == []


CAST = {"characters": [{"name": n, "aliases": [], "appearance": "瘦", "styling": "皮甲", "final_prompt": FP}
                       for n in ("陈老三", "燕无咎")],
        "scenes": [{"name": p, "places": [p], "location": "军营", "time": "白天", "lighting": "日光",
                    "furnishings": "木桩", "final_prompt": "An empty camp."} for p in ("校场", "营地")],
        "props": []}


def _pass_r1(s, d):
    _take(s, "drama_review", d, {"stage": "script"}, {"issues": [_issue("第1集", "陈老三说")]}, {"adopt": [], "reject": [0]})


def test_assets_final_then_images_voices_and_storyboards_are_queued_once(drama):
    s, d, e1, e2 = drama
    repo.update(s, "drama", d, flow_auto=1)
    _pass_r1(s, d)
    _take(s, "cast", d, {"notes": []}, CAST, {"accept": [], "reject": []})
    st = _st(s, d)
    assert st["r3"].state == "ready" and st["media"].state == "blocked" and st["boards"].state == "blocked"
    flow.advance(s, d)
    assert _queue(s) == [("drama_review", "drama", d, {"stage": "assets"})]
    _take(s, "drama_review", d, {"stage": "assets"}, {"issues": [_issue("角色·陈老三", "瘦")]}, {"adopt": [], "reject": [0]})
    assert _st(s, d)["r3"].state == "passed"
    did = flow.advance(s, d)
    tasks = s.conn.execute("SELECT target_type, json_extract(params_json, '$.writes') FROM task ORDER BY id").fetchall()
    assert sorted(map(tuple, tasks)) == sorted([("character", "turnaround_path")] * 2 + [("scene", "image_path")] * 2)
    q = _queue(s)
    assert {(t, tid) for t, _, tid, _ in q if t == "storyboard"} == {("storyboard", e1), ("storyboard", e2)}
    assert sum(t == "voice" for t, *_ in q) == 2
    assert did and flow.advance(s, d) == []                                       # 第二次什么都不排
    s.conn.execute("UPDATE task SET state = 'failed', error = 'oom'")
    assert flow.advance(s, d) == [] and s.conn.execute("SELECT COUNT(*) FROM task").fetchone()[0] == 4   # 失败的交给人


def test_fourview_follows_the_front_image(drama):
    s, d, *_ = drama
    repo.update(s, "drama", d, flow_auto=1)
    _pass_r1(s, d)
    _take(s, "cast", d, {"notes": []}, CAST, {"accept": [], "reject": []})
    _take(s, "drama_review", d, {"stage": "assets"}, {"issues": [_issue("角色·陈老三", "瘦")]}, {"adopt": [], "reject": [0]})
    flow.advance(s, d)
    s.conn.execute("UPDATE task SET state = 'done'")
    lao = s.conn.execute("SELECT id FROM character WHERE name = '陈老三'").fetchone()[0]
    repo.update(s, "character", lao, turnaround_path="assets/lao.png")
    flow.advance(s, d)
    fv = s.conn.execute("SELECT target_id FROM task WHERE json_extract(params_json, '$.writes') = 'fourview_path'").fetchall()
    assert [r[0] for r in fv] == [lao]
    wall = {w["name"]: w for w in flow.wall(s, d)}
    assert wall["陈老三"]["cells"][0]["path"] == "assets/lao.png" and wall["陈老三"]["cells"][1]["task"] == "pending"


def test_asset_review_waits_for_pending_script_fixes(drama):
    s, d, e1, e2 = drama
    repo.update(s, "drama", d, flow_auto=1)
    _pass_r1(s, d)
    _take(s, "cast", d, {"notes": []}, CAST, {"accept": [], "reject": []})
    llm_queue.enqueue(s, "script_revise", "episode", e2, {"notes": [{"text": "统一称呼", "target": "", "must": True}]})
    st = _st(s, d)
    assert st["r3"].state == "blocked" and "剧本修改" in st["r3"].detail
    flow.advance(s, d)
    assert [t for t, *_ in _queue(s)] == ["script_revise"]
    s.conn.execute("DELETE FROM llm_queue")
    flow.advance(s, d)
    assert [t for t, *_ in _queue(s)] == ["drama_review"]
