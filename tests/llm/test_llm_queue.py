# -*- coding: utf-8 -*-
"""持久的 LLM 队列(spec 2026-10-24-逐集批量出剧本分镜-design.md 第 4 节)。"""
from tools.drama import repo
from tools.llm import llm_queue as q, runs


def _ep(store, n=1):
    d = repo.create_drama(store, "玉佩", "9:16")
    return [repo.add_episode(store, d, i, "script") for i in range(1, n + 1)]


def test_items_come_out_oldest_first_and_report_their_position(store):
    a, b = _ep(store, 2)
    qa = q.enqueue(store, "script_outline", "episode", a, {"minutes": 1})
    qb = q.enqueue(store, "script_outline", "episode", b, {"minutes": 1})
    assert q.next_waiting(store)["id"] == qa and q.params(q.next_waiting(store)) == {"minutes": 1}
    assert q.position(store, qb) == 2 and q.waiting_count(store, [a, b]) == 2
    q.mark_started(store, qa, None, "前提不满足")
    assert q.next_waiting(store)["id"] == qb and q.position(store, qb) == 1


def test_running_item_is_the_started_one_whose_run_is_still_running(store):
    (a,) = _ep(store)
    qa = q.enqueue(store, "script_outline", "episode", a, {"minutes": 1})
    rid = runs.start(store, task="script_outline", target_type="episode", target_id=a, model="m", input_sha="x",
                     llm_mode="real", params={"minutes": 1})
    q.mark_started(store, qa, rid)
    assert q.running_item(store)["id"] == qa and q.item_for_run(store, rid)["id"] == qa
    runs.fail(store, rid, "x")
    assert q.running_item(store) is None


def test_cancel_only_touches_waiting_items_of_the_given_episodes(store):
    a, b = _ep(store, 2)
    qa = q.enqueue(store, "storyboard", "episode", a, {})
    q.enqueue(store, "storyboard", "episode", b, {})
    q.mark_started(store, qa, None)
    assert q.cancel_waiting(store, [a, b]) == 1 and q.next_waiting(store) is None


def test_pause_and_resume(store):
    assert q.paused(store) == ""
    q.pause(store, "LLM 不可用")
    assert q.paused(store) == "LLM 不可用"
    q.resume(store)
    assert q.paused(store) == ""
