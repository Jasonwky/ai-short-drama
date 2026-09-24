# -*- coding: utf-8 -*-
"""假 LLM —— **只给确定性代码路径的测试与控制台自测用**(CLAUDE.md:不许拿 Fake 冒充 LLM 契约)。

- 按工具名返回预设参数;没有预设的工具 → `NoToolCall`,闸门照常当成不合格;
- 它产出的 llm_run 标 `llm_mode = "fake"`,数据库触发器保证**永远过不了审批**;
- 控制台正式入口不会用到它:没有 key 就明确报「LLM 不可用」,不静默切过来。
"""
from __future__ import annotations

import json

from tools.llm.client import NoToolCall, ToolResult

CANNED = {
    "submit_voice": {"gender": "female", "age": "young_adult", "timbre": "清亮柔和", "pace": "normal"},
}


class FakeLLM:
    def __init__(self, canned: dict | None = None):
        self.canned = dict(CANNED if canned is None else canned)
        self.calls = 0

    def call_tool(self, *, model, messages, tool_name, description, schema, max_tokens) -> ToolResult:
        self.calls += 1
        if tool_name not in self.canned:
            raise NoToolCall(f"假 LLM 没有工具 {tool_name} 的样例")
        args = self.canned[tool_name]
        return ToolResult(args=args, raw=json.dumps(args, ensure_ascii=False), model=f"fake:{model}",
                          finish="tool_calls", usage={"total_tokens": 0}, seconds=0.0)
