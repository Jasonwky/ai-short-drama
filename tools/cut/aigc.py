# -*- coding: utf-8 -*-
"""GB 45438-2025 附录 E(规范性)的文件元数据隐式标识:拼、验。

原文核对见 `docs/superpowers/spike-results/2026-09-24-GB45438原文核对与导出实测.md`。要点:
- 字段值为整串 `{"AIGC":{…7 个键…}}`,键名以**规范性附录 E** 为准(资料性附录 F 的示例拼错了三个键名);
- 各要素值只能由码位 0x21、0x23–0x5B、0x5D–0x7E 的字符构成(E.j)—— 即除空格、`"`、`\\` 外的单字节可见字符;
- 首次写入时,传播者 = 生产者、传播编号 = 制作编号(E 注 1);
- 一个文件只保留一份(6.1 c)。
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

TAG = "AIGC"
KEYS = ("Label", "ContentProducer", "ProduceID", "ReservedCode1", "ContentPropagator", "PropagateID",
        "ReservedCode2")
_VALUE = re.compile(r"^[\x21\x23-\x5b\x5d-\x7e]*$")
MAX_PRODUCER = 64


def value_problems(name: str, value: str) -> list[str]:
    if not _VALUE.match(value):
        bad = sorted({c for c in value if not _VALUE.match(c)})
        return [f"{name} 含不允许的字符 {bad} —— 只能用单字节可见字符(不含空格、\" 和 \\)"]
    return []


def producer_problems(producer: str) -> list[str]:
    """服务提供者编码(★必答):由平台运营方提供,如统一社会信用代码。缺了就不导出,不用默认值。"""
    if not producer:
        return ["缺少「生成合成服务提供者编码」(★必答,如统一社会信用代码)—— 没有它不能导出"]
    if len(producer) > MAX_PRODUCER:
        return [f"服务提供者编码太长({len(producer)} 字符,上限 {MAX_PRODUCER})"]
    return value_problems("服务提供者编码", producer)


def new_produce_id(now: datetime | None = None) -> str:
    """内容制作编号:时间 + 随机,ASCII。与溯源报告的作品编号是同一个。"""
    ts = (now or datetime.now(timezone.utc)).strftime("%Y%m%d%H%M%S")
    return f"ASD-{ts}-{uuid.uuid4().hex[:12]}"


def build(producer: str, produce_id: str, reserved1: str) -> str:
    problems = producer_problems(producer) + value_problems("ProduceID", produce_id) + value_problems(
        "ReservedCode1", reserved1)
    if problems:
        raise ValueError(";".join(problems))
    body = {"Label": "1", "ContentProducer": producer, "ProduceID": produce_id, "ReservedCode1": reserved1,
            "ContentPropagator": producer, "PropagateID": produce_id, "ReservedCode2": ""}
    return json.dumps({TAG: body}, ensure_ascii=True, separators=(",", ":"))


def check(tags: dict[str, str], *, producer: str, produce_id: str, reserved1: str) -> list[str]:
    """读回的元数据逐条对附录 E。返回问题清单,空 = 合规。"""
    keys = [k for k in tags if TAG.lower() in k.lower()]
    if len(keys) != 1:
        return [f"含「AIGC」的元数据字段应恰好 1 个(6.1 c),实际 {len(keys)} 个:{keys}"]
    try:
        doc = json.loads(tags[keys[0]])
    except json.JSONDecodeError as e:
        return [f"AIGC 字段不是合法 JSON:{e}"]
    if not isinstance(doc, dict) or list(doc) != [TAG] or not isinstance(doc[TAG], dict):
        return ['AIGC 字段应为 {"AIGC":{…}} 的整串(附录 E b)']
    body = doc[TAG]
    problems = []
    if list(body) != list(KEYS):
        problems.append(f"键应依次为 {list(KEYS)},实际 {list(body)}")
    for k, v in body.items():
        if not isinstance(v, str):
            problems.append(f"{k} 应为字符串,实际 {type(v).__name__}")
        else:
            problems += value_problems(k, v)
    expect = {"Label": "1", "ContentProducer": producer, "ProduceID": produce_id, "ReservedCode1": reserved1,
              "ContentPropagator": producer, "PropagateID": produce_id}
    problems += [f"{k} 应为 {v!r},实际 {body.get(k)!r}" for k, v in expect.items() if body.get(k) != v]
    return problems
