# -*- coding: utf-8 -*-
"""闸门的确定性路径:schema 体检、客户端校验、判卷回炉、轮次上限、落盘。用脚本化的假客户端,不声称覆盖 LLM 契约。"""
import json

import pytest

from tools.llm.client import NoToolCall, ToolResult, Truncated
from tools.llm.gate import GateFailed, run_gated, schema_errors, strict_schema_problems

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["mood", "n"],
          "properties": {"mood": {"type": "string", "enum": ["calm", "sad"]}, "n": {"type": "integer"}}}


class Scripted:
    """按顺序返回预设的结果;元素是 dict(工具参数)或异常。记下每次收到的 messages。"""
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.seen = []

    def call_tool(self, *, model, messages, tool_name, description, schema, max_tokens):
        self.seen.append([dict(m) for m in messages])
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return ToolResult(args=out, raw=json.dumps(out), model=model, finish="tool_calls",
                          usage={"total_tokens": 10}, seconds=1.0)


def _run(client, tmp_path, judge=lambda a: [], rounds=3):
    return run_gated(client=client, model="m", task="t", system="sys", user="u", tool_name="submit",
                     description="d", schema=SCHEMA, judge=judge, journal_dir=tmp_path, max_rounds=rounds)


def test_strict_schema_lint():
    assert strict_schema_problems(SCHEMA) == []
    loose = {"type": "object", "properties": {"a": {"type": "string"}}, "required": []}
    assert len(strict_schema_problems(loose)) == 2
    nested = {"type": "object", "additionalProperties": False, "required": ["xs"], "properties": {
        "xs": {"type": "array", "items": {"type": "object", "properties": {"b": {"type": "string"}}}}}}
    assert any("$.xs[]" in p for p in strict_schema_problems(nested))


def test_loose_schema_is_never_sent(tmp_path):
    client = Scripted({"mood": "calm", "n": 1})
    with pytest.raises(ValueError, match="不许发给 LLM"):
        run_gated(client=client, model="m", task="t", system="s", user="u", tool_name="x", description="d",
                  schema={"type": "object", "properties": {"a": {"type": "string"}}}, judge=lambda a: [],
                  journal_dir=tmp_path)
    assert client.seen == []


def test_schema_errors_name_the_field():
    assert schema_errors({"mood": "happy", "n": 1}, SCHEMA)[0].startswith("字段 mood")


def test_first_round_passes_and_is_journaled(tmp_path):
    res = _run(Scripted({"mood": "calm", "n": 1}), tmp_path)
    assert res.args == {"mood": "calm", "n": 1} and len(res.rounds) == 1
    doc = json.loads(res.journal_path.read_text(encoding="utf-8"))
    assert doc["status"] == "ok" and doc["rounds"][0]["usage"] == {"total_tokens": 10}


def test_schema_violation_goes_back_with_the_errors(tmp_path):
    client = Scripted({"mood": "nostalgic", "n": 1}, {"mood": "sad", "n": 2})
    res = _run(client, tmp_path)
    assert res.args["mood"] == "sad" and len(res.rounds) == 2
    feedback = client.seen[1][-1]["content"]
    assert "上一版不合格" in feedback and "mood" in feedback
    assert client.seen[1][-2]["role"] == "assistant"          # 上一版参数原样给它看


def test_judge_problems_also_go_back(tmp_path):
    client = Scripted({"mood": "calm", "n": 1}, {"mood": "calm", "n": 3})
    res = _run(client, tmp_path, judge=lambda a: [] if a["n"] == 3 else ["n 必须是 3(数据反证)"])
    assert res.args["n"] == 3 and "数据反证" in client.seen[1][-1]["content"]


def test_no_tool_call_counts_as_a_bad_round(tmp_path):
    res = _run(Scripted(NoToolCall("模型没有调用工具"), {"mood": "calm", "n": 1}), tmp_path)
    assert len(res.rounds) == 2 and "没有调用工具" in res.rounds[0].problems[0]


def test_round_cap_fails_loud_and_keeps_the_record(tmp_path):
    with pytest.raises(GateFailed) as e:
        _run(Scripted({"mood": "x", "n": 1}, {"mood": "y", "n": 1}), tmp_path, rounds=2)
    assert len(e.value.rounds) == 2
    assert json.loads(e.value.journal_path.read_text(encoding="utf-8"))["status"] == "failed"


def test_truncation_is_not_retried(tmp_path):
    client = Scripted(Truncated("截断"), {"mood": "calm", "n": 1})
    with pytest.raises(Truncated):
        _run(client, tmp_path)
    assert len(client.seen) == 1                                 # 没有第二次调用
