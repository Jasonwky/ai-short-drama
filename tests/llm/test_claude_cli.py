# -*- coding: utf-8 -*-
"""本机 Claude Code 命令行当 LLM 后端(用户 2026-10-23 定:SynScale 连不上,换成本机 Claude)。

只测确定性部分:闸门消息 → 命令行输入的转换、命令行参数、输出解析与各种失败的分流。真模型行为由真 LLM 契约测试管。
"""
import json

import pytest

from tools.llm import claude_cli as cc
from tools.llm.client import LLMUnavailable, NoToolCall, Refused, Truncated
from tools.llm.gate import image_part

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["a"], "properties": {"a": {"type": "string"}}}


def test_first_round_becomes_system_prompt_plus_one_user_message():
    system, msg = cc.to_input([{"role": "system", "content": "你是分镜师。"}, {"role": "user", "content": "<script>剧本</script>"}])
    assert system == "你是分镜师。"
    assert msg == {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "<script>剧本</script>"}]}}


def test_images_become_base64_image_blocks():
    png = b"\x89PNG\r\n\x1a\nfake"
    _, msg = cc.to_input([{"role": "system", "content": "s"},
                          {"role": "user", "content": [{"type": "text", "text": "看图"}, image_part(png)]}])
    text, img = msg["message"]["content"]
    assert text == {"type": "text", "text": "看图"}
    assert img["type"] == "image" and img["source"]["media_type"] == "image/png" and img["source"]["type"] == "base64"


def test_retry_rounds_are_flattened_into_the_same_user_message():
    """闸门回炉:上一版参数(assistant)+ 判卷意见(user)接在原输入后面,命令行一次只收一条用户消息。"""
    _, msg = cc.to_input([{"role": "system", "content": "s"}, {"role": "user", "content": "原输入"},
                          {"role": "assistant", "content": "(上一版工具参数){\"a\": 1}"},
                          {"role": "user", "content": "字段 a:1 is not of type 'string'"}])
    texts = [b["text"] for b in msg["message"]["content"]]
    assert texts[0] == "原输入" and "上一版" in texts[1] and "is not of type" in texts[2]


def test_args_turn_off_tools_settings_and_session_files():
    args = cc.build_args("claude.exe", model="sonnet", schema=SCHEMA, system_file="sys.txt")
    assert args[:2] == ["claude.exe", "-p"]
    for flag in ("--tools", "--setting-sources", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in args
    assert args[args.index("--tools") + 1] == "" and args[args.index("--setting-sources") + 1] == ""
    assert json.loads(args[args.index("--json-schema") + 1]) == SCHEMA
    assert args[args.index("--model") + 1] == "sonnet" and args[args.index("--system-prompt-file") + 1] == "sys.txt"


def _result(**kw):
    base = {"type": "result", "is_error": False, "subtype": "success", "stop_reason": "tool_use",
            "structured_output": {"a": "x"}, "result": "{\"a\":\"x\"}", "total_cost_usd": 0.01,
            "usage": {"input_tokens": 2, "output_tokens": 9}, "modelUsage": {"claude-sonnet-5-5": {}}}
    return json.dumps({**base, **kw})


def test_parse_takes_the_structured_output_and_the_real_model_name():
    out = "\n".join([json.dumps({"type": "system"}), _result()])
    r = cc.parse(out, "sonnet", 6.0)
    assert r.args == {"a": "x"} and r.model == "claude-sonnet-5-5" and r.seconds == 6.0
    assert json.loads(r.raw) == {"a": "x"} and r.usage["output_tokens"] == 9


@pytest.mark.parametrize("kw, exc", [
    ({"is_error": True, "result": "Not logged in · Please run /login"}, LLMUnavailable),
    ({"stop_reason": "max_tokens", "structured_output": None}, Truncated),
    ({"stop_reason": "refusal", "structured_output": None}, Refused),
    ({"structured_output": None, "result": "我不确定"}, NoToolCall),
])
def test_failures_are_split_the_same_way_as_the_chat_client(kw, exc):
    with pytest.raises(exc):
        cc.parse(_result(**kw), "sonnet", 1.0)


def test_no_result_line_at_all_is_unavailable():
    with pytest.raises(LLMUnavailable, match="没有 result"):
        cc.parse(json.dumps({"type": "system"}), "sonnet", 1.0)
