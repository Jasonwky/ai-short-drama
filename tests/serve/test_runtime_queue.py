# -*- coding: utf-8 -*-
"""运行时出队:一次只跑一条、断路器、暂停 / 继续(spec 2026-10-24-逐集批量出剧本分镜-design.md 第 4、8 节)。"""
import dataclasses
import threading

from tools.drama import repo
from tools.llm import llm_queue as q
from tools.llm.client import LLMUnavailable
from tools.llm.service import LLMBackend
from tools.serve.runtime import Runtime


class Down:
    def __init__(self):
        self.calls = 0

    def call_tool(self, **kw):
        self.calls += 1
        raise LLMUnavailable("命令行没登录")


def _eps(store, n):
    d = repo.create_drama(store, "玉佩", "9:16")
    repo.add_character(store, d, "林小雨")
    return [repo.add_episode(store, d, i, "script", outline=f"第{i}集大纲:林小雨找玉佩。") for i in range(1, n + 1)]


def _down_runtime(store, runtime, down):
    return Runtime(store, runtime.data_root,
                   dataclasses.replace(runtime.backend, llm=lambda: LLMBackend(down, "m", "real")))


def test_queue_runs_one_at_a_time_until_empty(runtime, store):
    eps = _eps(store, 3)
    for e in eps:
        q.enqueue(store, "script_outline", "episode", e, {"minutes": 1})
    runtime.pump_queue()
    runtime.wait_llm()
    started = store.conn.execute("SELECT run_id FROM llm_queue WHERE state = 'started' ORDER BY id").fetchall()
    assert len(started) == 3 and all(r[0] for r in started) and q.next_waiting(store) is None


def test_a_start_time_failure_is_recorded_and_the_queue_moves_on(runtime, store):
    eps = _eps(store, 2)
    repo.update(store, "episode", eps[0], outline="")                       # 前提不满足:开不了
    for e in eps:
        q.enqueue(store, "script_outline", "episode", e, {"minutes": 1})
    runtime.pump_queue()
    runtime.wait_llm()
    first, second = store.conn.execute("SELECT run_id, reason FROM llm_queue ORDER BY id").fetchall()
    assert first[0] is None and "还没有大纲" in first[1] and second[0] is not None


def test_breaker_pauses_after_three_llm_failures_and_resume_continues(store, runtime):
    down = Down()
    rt = _down_runtime(store, runtime, down)
    eps = _eps(store, 5)
    for e in eps:
        q.enqueue(store, "script_outline", "episode", e, {"minutes": 1})
    rt.pump_queue()
    rt.wait_llm()
    assert down.calls == 3 and "连续 3 条" in q.paused(store) and q.waiting_count(store, eps) == 2
    rt.pump_queue()                                                          # 暂停时不出队
    assert down.calls == 3
    q.resume(store)
    rt.pump_queue()
    rt.wait_llm()
    assert q.waiting_count(store, eps) == 0


def test_manual_runs_do_not_count_toward_the_breaker(store, runtime):
    down = Down()
    rt = _down_runtime(store, runtime, down)
    for e in _eps(store, 3):
        rt.start_llm("script_outline", e, {"minutes": 1})                    # 手动发起,不在队列里
    rt.wait_llm()
    assert down.calls == 3 and q.paused(store) == ""


def test_two_threads_pumping_at_once_start_only_one_run_per_item(runtime, store):
    eps = _eps(store, 2)
    for e in eps:
        q.enqueue(store, "script_outline", "episode", e, {"minutes": 1})
    gate = threading.Barrier(2)

    def pump():
        gate.wait()
        runtime.pump_queue()
    ts = [threading.Thread(target=pump) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    runtime.wait_llm()
    n = store.conn.execute("SELECT COUNT(*) FROM llm_run WHERE task = 'script_outline'").fetchone()[0]
    assert n == 2                                                            # 两条各一次,没有重复开


def test_unavailable_llm_pauses_the_queue(store, runtime):
    rt = Runtime(store, runtime.data_root, dataclasses.replace(runtime.backend, llm=None))
    (e,) = _eps(store, 1)
    q.enqueue(store, "script_outline", "episode", e, {"minutes": 1})
    rt.pump_queue()
    assert "LLM 不可用" in q.paused(store) and q.next_waiting(store) is not None
