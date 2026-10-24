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
    # P17c:节拍 / 单集变体 / 分镜方案。样例取自自测演示剧(selftest.seed_demo)
    "submit_beats": lambda schema: beats_sample(schema),
    "submit_outline_variants": lambda schema: variants_sample(schema),
    "submit_shot_options": lambda schema: shot_options_sample(schema),
    # P19-C 修改回路:样例取自自测演示剧第 1 集(selftest.seed_demo);第 1 条意见改,其余「不改」
    "submit_script_edits": lambda schema: script_edits_sample(schema),
    "submit_storyboard_patches": lambda schema: storyboard_patches_sample(schema),
    # P19-B 逐集批量:自测里批量出剧本要走到「待你审」
    "submit_script": lambda schema: script_sample(schema),
}


def script_sample(schema: dict) -> dict:
    """写剧本样例:一个场景、479 字(1 分钟篇幅 400–600 之内,自测选 1 分钟);人物取自自测演示剧(selftest.seed_demo)。"""
    act = "林小雨推开老宅斑驳的木门，院子里落满枯叶，她在石桌旁慢慢坐下，望着那口古井出神良久"
    lines = [{"kind": "action", "speaker": None, "text": act} for _ in range(12)]
    lines.append({"kind": "dialogue", "speaker": "林小雨", "text": "这块玉佩，是我母亲留给我的。"})
    return {"scenes": [{"place": "老宅庭院", "time": "黄昏", "lines": lines}]}


def _notes_enum(schema: dict) -> list[int]:
    return schema["properties"]["responses"]["items"]["properties"]["note"]["enum"]


def script_edits_sample(schema: dict) -> dict:
    first, *rest = _notes_enum(schema)
    edit = {"old": "林小雨说：「这块玉佩，是我母亲留给我的。」", "new": "林小雨说：「这块玉佩，是娘留给我唯一的念想。」",
            "why": "更有情感"}
    return {"responses": [{"note": first, "verdict": "改", "reason": "更有情感", "edits": [edit]},
                          *({"note": k, "verdict": "不改", "reason": "(假 LLM 只改第 1 条)", "edits": []} for k in rest)]}


def storyboard_patches_sample(schema: dict) -> dict:
    first, *rest = _notes_enum(schema)
    item = schema["properties"]["responses"]["items"]["properties"]["patches"]["items"]["properties"]
    blank = {k: None for k in item if k not in ("segment", "field")}
    patch = {**blank, "segment": item["segment"]["enum"][0], "field": "video_prompt",
             "text": "@林小雨 在 @老宅庭院 低头凝视 @玉佩,镜头缓缓推近,她抬眼说话"}
    return {"responses": [{"note": first, "verdict": "改", "reason": "运镜更有情绪", "patches": [patch]},
                          *({"note": k, "verdict": "不改", "reason": "(假 LLM 只改第 1 条)", "patches": []} for k in rest)]}


def beats_sample(schema: dict) -> dict:
    """节拍样例:按 schema 里 number 的 enum 逐集一项。假 LLM 读不到大纲,依据必然对不上原文 ——
    逐字依据的判卷会拦下它,自测演示的就是「编造的依据被拦住」(预期是失败页)。"""
    item = schema["properties"]["episodes"]["items"]["properties"]
    return {"episodes": [{"number": n, "hook": "有", "reversal": "小", "emotion": "压", "paywall": "否",
                          "quote": "(假 LLM 读不到大纲)"} for n in item["number"]["enum"]]}


def variants_sample(schema: dict) -> dict:
    """单集变体样例:3 个版本,三个维度各取 enum 第 0 / 1 / 2 个(两两 3 维全不同),大纲互不相同也不同于原大纲。
    版本数固定 3 —— 自测用默认 n=3。"""
    props = schema["properties"]["variants"]["items"]["properties"]
    texts = ["开场就是祖宅起火,林小雨冲进去抢出玉佩,中途才发现纵火的是她最信任的人。",
             "林小雨按母亲遗嘱回到老宅,管家当众否认她的身份,她拿出玉佩反将一军,结尾又收到一封匿名信。",
             "玉佩在拍卖会上被人高价拍走,林小雨追查买家,最后在门口撞见一个不该出现的人。"]
    return {"variants": [{"outline": texts[i], **{d: props[d]["enum"][i] for d in ("hook_type", "reversal_type", "cliff_type")},
                          "typicality": (0.8, 0.3, 0.5)[i]} for i in range(3)]}


def shot_options_sample(schema: dict) -> dict:
    """分镜方案样例:2 个方案,景别 / 机位不同。角色名与道具名取自 schema 的 enum;
    场景名不在 schema 里,这里写死演示剧的「老宅庭院」。图提示词不写任何姿态 / 站位 / 朝向 / 手持词。
    方案数固定 2 —— 自测用默认 n=2;只适用单角色段落。"""
    item = schema["properties"]["options"]["items"]["properties"]
    who = item["blocking"]["items"]["properties"]["character"]["enum"][0]
    prop = next(p for p in item["blocking"]["items"]["properties"]["holding"]["enum"] if p != "无")
    entry = {"character": who, "pose": "站", "facing": "镜头", "side": "中", "holding": prop}
    prompt = f"@{who} 与 @{prop} 在 @老宅庭院 ,黄昏暖光"
    return {"options": [
        {"shot_type": "近景", "angle": "平视", "movement": "推", "blocking": [entry], "image_prompt": prompt,
         "why": "近景推进,看清神情"},
        {"shot_type": "全景", "angle": "俯拍", "movement": "拉", "blocking": [entry], "image_prompt": prompt,
         "why": "全景俯拍,交代环境"}]}


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
