# -*- coding: utf-8 -*-
"""parse_response 的分支(确定性)。真服务端的行为由 test_contract_real.py 用真 LLM 验证。"""
import io
import json
import urllib.error
import urllib.request

import pytest

from tools.llm.client import (ChatClient, LLMUnavailable, NoToolCall, Refused, Truncated, parse_response, read_key,
                              request_body)


def _resp(finish="tool_calls", args='{"a": 1}', name="t", refusal=None, content=None):
    msg = {"role": "assistant", "content": content, "refusal": refusal,
           "tool_calls": [{"type": "function", "function": {"name": name, "arguments": args}}] if args is not None else None}
    return {"model": "m", "choices": [{"finish_reason": finish, "message": msg}], "usage": {"total_tokens": 9}}


def test_tool_call_is_parsed():
    r = parse_response(_resp(), "m", "t", 1.23)
    assert r.args == {"a": 1} and r.raw == '{"a": 1}' and r.usage == {"total_tokens": 9} and r.seconds == 1.2


def test_truncation_is_its_own_error_and_not_a_bad_round():
    with pytest.raises(Truncated, match="不重试"):
        parse_response(_resp(finish="length"), "m", "t", 1)


def test_refusal_and_content_filter():
    with pytest.raises(Refused):
        parse_response(_resp(refusal="不能帮你"), "m", "t", 1)
    with pytest.raises(Refused):
        parse_response(_resp(finish="content_filter"), "m", "t", 1)


def test_missing_or_wrong_tool_call():
    # 实测:response_format 被忽略时,模型把 ```json 放进正文、不调用工具 —— 必须当成不合格,而不是去正文里「尽力解析」
    with pytest.raises(NoToolCall, match="没有调用工具"):
        parse_response(_resp(finish="stop", args=None, content='```json\n{"a": 1}\n```'), "m", "t", 1)
    with pytest.raises(NoToolCall):
        parse_response(_resp(name="other"), "m", "t", 1)
    with pytest.raises(NoToolCall, match="不是合法 JSON"):
        parse_response(_resp(args="{broken"), "m", "t", 1)


def test_missing_key_file_fails_loudly(tmp_path):
    with pytest.raises(LLMUnavailable, match="找不到 LLM key"):
        read_key(tmp_path / "llm.txt")
    (tmp_path / "llm.txt").write_text("  \n")
    with pytest.raises(LLMUnavailable, match="是空的"):
        read_key(tmp_path / "llm.txt")


def test_response_is_json_serializable_for_the_journal():
    r = parse_response(_resp(), "m", "t", 1)
    json.dumps(r.__dict__)



def test_tool_choice_spellings():
    kw = dict(model="m", messages=[], tool_name="submit", description="d", schema={"type": "object"}, max_tokens=10)
    assert request_body(**kw)["tool_choice"] == {"type": "function", "name": "submit", "function": {"name": "submit"}}
    assert request_body(**kw, spelling="flat")["tool_choice"] == {"type": "function", "name": "submit"}
    assert request_body(**kw, spelling="chat")["tool_choice"] == {"type": "function", "function": {"name": "submit"}}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _server(monkeypatch, replies):
    """按顺序回放:int = 该状态码 + 报错正文;dict = 200 + JSON。记下每次请求的 tool_choice。"""
    seen = []

    def urlopen(req, timeout):
        seen.append(json.loads(req.data)["tool_choice"])
        code, body = replies[len(seen) - 1]
        if code != 200:
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(body.encode()))
        return _Resp(json.dumps(body).encode())
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return seen


OK = {"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [
    {"function": {"name": "submit", "arguments": "{}"}}]}}]}


def test_switches_spelling_only_on_tool_choice_400(monkeypatch):
    seen = _server(monkeypatch, [(400, "Unknown parameter: 'tool_choice.function'."), (200, OK)])
    ChatClient("k").call_tool(model="m", messages=[], tool_name="submit", description="d", schema={}, max_tokens=5)
    assert seen == [{"type": "function", "name": "submit", "function": {"name": "submit"}},
                    {"type": "function", "name": "submit"}]


def test_other_400_and_exhausted_spellings_fail_loudly(monkeypatch):
    _server(monkeypatch, [(400, "context too long")])
    with pytest.raises(LLMUnavailable, match="context too long"):
        ChatClient("k").call_tool(model="m", messages=[], tool_name="submit", description="d", schema={}, max_tokens=5)
    seen = _server(monkeypatch, [(400, "tool_choice bad")] * 3)
    with pytest.raises(LLMUnavailable, match="tool_choice bad"):
        ChatClient("k").call_tool(model="m", messages=[], tool_name="submit", description="d", schema={}, max_tokens=5)
    assert len(seen) == 3
