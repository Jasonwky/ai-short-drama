# -*- coding: utf-8 -*-
"""parse_response 的分支(确定性)。真服务端的行为由 test_contract_real.py 用真 LLM 验证。"""
import json

import pytest

from tools.llm.client import LLMUnavailable, NoToolCall, Refused, Truncated, parse_response, read_key


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
