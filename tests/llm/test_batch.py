# -*- coding: utf-8 -*-
"""逐集批量(spec 2026-10-24-逐集批量出剧本分镜-design.md 第 2、5、8 节)。"""
import pytest

from tools.drama import repo
from tools.llm import batch, llm_queue as q, runs, service

SCRIPT = "【老宅庭院 · 黄昏】\n林小雨捧着玉佩。\n林小雨说：「这块玉佩，是我母亲留给我的。」\n"


def _drama(store):
    d = repo.create_drama(store, "玉佩", "9:16")
    repo.add_character(store, d, "林小雨")
    repo.add_scene(store, d, "老宅庭院")
    e = {"has": repo.add_episode(store, d, 1, "script", script=SCRIPT),
         "outline": repo.add_episode(store, d, 2, "script", outline="林小雨回老宅找玉佩。"),
         "novel": repo.add_episode(store, d, 3, "novel", outline="大纲", source_text="原文"),
         "blank": repo.add_episode(store, d, 4, "script")}
    return d, e


def _status(items):
    return {i.number: (i.status, i.task) for i in items}


def test_plan_sorts_episodes_into_four_kinds(store):
    d, e = _drama(store)
    assert _status(batch.plan(store, d)) == {1: ("has", None), 2: ("todo", "script_outline"),
                                              3: ("todo", "script_adapt"), 4: ("blocked", None)}


def test_queued_running_or_proposed_episodes_are_in_flight(store):
    d, e = _drama(store)
    q.enqueue(store, "script_outline", "episode", e["outline"], {"minutes": 1})
    rid = runs.start(store, task="script_adapt", target_type="episode", target_id=e["novel"], model="m", input_sha="x",
                     llm_mode="real", params={"minutes": 1})
    runs.propose(store, rid, {"scenes": []}, "j")
    got = _status(batch.plan(store, d))
    assert got[2] == ("in_flight", None) and got[3] == ("in_flight", None)


def test_confirm_only_enqueues_listed_episodes_that_are_still_todo(store):
    d, e = _drama(store)
    ids = batch.confirm(store, d, [e["outline"], e["has"], e["blank"]], 1)       # 第 3 集没列出:不排
    assert len(ids) == 1 and q.next_waiting(store)["target_id"] == e["outline"]
    assert q.params(q.next_waiting(store)) == {"minutes": 1}
    assert batch.confirm(store, d, [e["outline"]], 1) == []                       # 已在队:确认时再判一次,跳过
    with pytest.raises(ValueError, match="目标时长"):
        batch.confirm(store, d, [e["novel"]], 7)


def test_storyboard_gaps_block_unknown_speakers_but_only_hint_place_names(store):
    d, e = _drama(store)
    assert batch.storyboard_gaps(store, e["has"]) == []
    repo.update(store, "episode", e["has"], script=SCRIPT + "【王府正堂 · 夜】\n小翠说：「小姐请用茶。」\n")
    assert batch.storyboard_gaps(store, e["has"]) == ["角色「小翠」"]
    assert batch.place_hints(store, e["has"]) == ["王府正堂"]


def test_after_script_approved_enqueues_storyboard_once(store):
    d, e = _drama(store)
    assert batch.after_script_approved(store, e["has"]) == "已排分镜"
    assert batch.after_script_approved(store, e["has"]) == ""                     # 已在队:不重复排
    assert q.next_waiting(store)["task"] == "storyboard"


def test_after_script_approved_skips_episodes_with_segments_or_missing_assets(store):
    d, e = _drama(store)
    repo.add_segment(store, e["has"], 1)
    assert batch.after_script_approved(store, e["has"]) == ""
    repo.update(store, "episode", e["outline"], script="小翠说：「小姐。」\n")
    assert batch.after_script_approved(store, e["outline"]).startswith("没排分镜 —— 缺资产:角色「小翠」")
    assert q.next_waiting(store) is None


def test_episode_rows_show_queue_review_stale_and_failed(store):
    d, e = _drama(store)
    q.enqueue(store, "script_outline", "episode", e["outline"], {"minutes": 1})
    params = {"minutes": 1}
    sha = service.prepare(store, "script_adapt", e["novel"], params).sha
    rid = runs.start(store, task="script_adapt", target_type="episode", target_id=e["novel"], model="m", input_sha=sha,
                     llm_mode="real", params=params)
    runs.propose(store, rid, {"scenes": []}, "j")
    rows = {r["number"]: r for r in batch.episode_rows(store, d)}
    assert rows[1]["script"]["state"] == "has" and rows[1]["storyboard"]["state"] == "none"
    assert rows[2]["script"] == {"state": "queued", "label": "排队中(第 1 位)"}
    assert rows[3]["script"]["state"] == "proposed" and rows[3]["script"]["run"] == rid
    repo.update(store, "episode", e["novel"], outline="大纲改了")
    assert {r["number"]: r for r in batch.episode_rows(store, d)}[3]["script"]["state"] == "stale"
    repo.update(store, "episode", e["novel"], outline="")                         # 前提没了:重建上下文会抛错
    cell = {r["number"]: r for r in batch.episode_rows(store, d)}[3]["script"]
    assert cell["state"] == "stale" and cell["label"].startswith("前提变了")


def test_rejected_script_reads_as_none_and_is_todo_again(store):
    d, e = _drama(store)
    rid = runs.start(store, task="script_outline", target_type="episode", target_id=e["outline"], model="m",
                     input_sha="x", llm_mode="real", params={"minutes": 1})
    runs.propose(store, rid, {"scenes": []}, "j")
    runs.reject(store, rid)
    assert {r["number"]: r for r in batch.episode_rows(store, d)}[2]["script"]["state"] == "none"
    assert _status(batch.plan(store, d))[2] == ("todo", "script_outline")


def test_storyboard_cell_shows_missing_assets(store):
    d, e = _drama(store)
    repo.update(store, "episode", e["has"], script="小翠说：「小姐。」\n")
    cell = {r["number"]: r for r in batch.episode_rows(store, d)}[1]["storyboard"]
    assert cell == {"state": "blocked", "label": "缺资产:角色「小翠」—— 先去资产里建"}
