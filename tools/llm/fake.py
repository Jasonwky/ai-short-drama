# -*- coding: utf-8 -*-
"""假 LLM —— **只给确定性代码路径的测试与控制台自测用**(CLAUDE.md:不许拿 Fake 冒充 LLM 契约)。

- 按工具名返回预设参数(预设可以是函数:按本次 schema 现拼);没有预设的工具 → `NoToolCall`,闸门照常当成不合格;
- 它产出的 llm_run 标 `llm_mode = "fake"`,数据库触发器保证**永远过不了审批**;
- 控制台正式入口不会用到它:没有 key 就明确报「LLM 不可用」,不静默切过来。
"""
from __future__ import annotations

import json

from tools.llm.client import NoToolCall, ToolResult

CANNED = {
    "submit_voice": {"gender": "female", "age": "young_adult", "timbre": "清亮柔和", "pace": "normal"},
    # 从图反推描述:三类资产字段不同,按 schema 取需要的键。假 LLM **看不见图**:校验数字只印在图上,
    # 它答的 000 必错 —— 闸门照常拦下,自测里演示的就是「看不见图的模型被拦住」
    "submit_description": lambda schema: {k: DESCRIBE_SAMPLE[k] for k in schema["properties"]},
}
DESCRIBE_SAMPLE = {
    "appearance": "年轻女性,黑色中分长直发", "styling": "米白色长袖上衣,深蓝色长裙,黑色平底鞋",
    "location": "中式老宅庭院", "time": "黄昏", "lighting": "低角度暖光,红灯笼点亮",
    "description": "椭圆形绿色玉佩,刻云纹,系红绳", "final_prompt": "A sample description written by the fake LLM.",
    "check_number": "000",
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
        if callable(args):
            args = args(schema)
        return ToolResult(args=args, raw=json.dumps(args, ensure_ascii=False), model=f"fake:{model}",
                          finish="tool_calls", usage={"total_tokens": 0}, seconds=0.0)
