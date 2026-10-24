# -*- coding: utf-8 -*-
"""service.finish 的失败类别:逐集队列的断路器只数「LLM 本身」的失败(spec 2026-10-24-逐集批量出剧本分镜 第 4 节)。"""
from tools.drama import repo
from tools.llm import runs, service
from tools.llm.client import LLMUnavailable
from tools.llm.fake import FakeLLM
from tools.llm.service import LLMBackend


class Down:
    def call_tool(self, **kw):
        raise LLMUnavailable("命令行没登录")


def _run(store, llm):
    d = repo.create_drama(store, "玉佩", "9:16")
    repo.add_character(store, d, "林小雨")
    rid, _ = service.begin(store, "premise", d, {"idea": "替嫁冲喜的庶女其实是神医", "n": 3, "constraint": ""}, llm)
    return rid


def test_finish_reports_llm_judge_and_ok(store, tmp_path):
    down = LLMBackend(Down(), "m", "real")
    rid = _run(store, down)
    assert service.finish(store, rid, down, tmp_path) == "llm" and runs.get(store, rid)["state"] == "failed"
    nothing = LLMBackend(FakeLLM({}), "m", "real")                      # 没有样例 → 不出工具调用 → 判卷轮次用完
    rid = _run(store, nothing)
    assert service.finish(store, rid, nothing, tmp_path, max_rounds=1) == "judge"
    ok = LLMBackend(FakeLLM(), "m", "real")      # 立项有现成样例,判卷能过(写计划时实测:落成 proposed)
    rid = _run(store, ok)
    assert service.finish(store, rid, ok, tmp_path) is None and runs.get(store, rid)["state"] == "proposed"
