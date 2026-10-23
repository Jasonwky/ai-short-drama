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
    # 评审团 / 局部改写(P17a):引用和台词都取自自测演示剧第 1 集剧本(selftest.seed_demo)
    "submit_issues": lambda schema: {"issues": [{
        "quote": "这块玉佩，是我母亲留给我的", "severity": "中", "suggestion": "加一句她为什么珍惜这块玉佩",
        "category": schema["properties"]["issues"]["items"]["properties"]["category"]["enum"][0]}]},
    "submit_candidates": {"candidates": [
        {"text": "林小雨说：「这块玉佩，是娘留给我唯一的念想。」", "why": "更有情感"},
        {"text": "林小雨攥紧玉佩。\n林小雨说：「这是我母亲的东西，谁也拿不走。」", "why": "加强冲突"}]},
    "submit_premises": lambda schema: premise_sample(schema),
}


def premise_sample(schema: dict) -> dict:
    """立项脑爆样例:三个方案,每个维度分别取 enum 的第 0 / 1 / 2 个(两两 4 维全不同),典型度 0.2 / 0.9 / 0.5;
    主角名避开自测演示剧的角色(林小雨、沈墨、周伯)。方案数固定 3 —— 自测用默认 n=3。"""
    props = schema["properties"]["candidates"]["items"]["properties"]
    heroes = [("顾清岚", "落魄医女", "看似柔弱却懂得救人"), ("陆长风", "边关少将", "战场上冷峻,家里怕猫"),
              ("苏晚棠", "替嫁新娘", "表面温顺,心里算得极清")]
    titles = [("寒门医女", "替嫁冲喜的医女,一针救活夫君,也救了自己"), ("少将归来", "少将军奉旨回京,一进门就撞破阴谋"),
              ("嫁衣之下", "新娘盖头一掀,才发现夫君早已知道她的底细")]
    typicality = [0.2, 0.9, 0.5]
    out = []
    for i in range(3):
        c = {"title": titles[i][0], "logline": titles[i][1], "antagonist": f"府中管事{'甲乙丙'[i]}",
             "hook": f"第 {i + 1} 号开场:一声瓷碗摔碎,所有人同时回头",
             "protagonist": dict(zip(("name", "tag", "contrast"), heroes[i])), "typicality": typicality[i]}
        for dim in ("genre", "payoff", "conflict", "hook_type"):
            c[dim] = props[dim]["enum"][i]
        out.append(c)
    return {"candidates": out}


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
