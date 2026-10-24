# -*- coding: utf-8 -*-
"""LLM 代码闸门:调用 → JSON Schema 校验 → 代码判卷 → 不合格带错误回炉 → 轮次上限 fail-loud。

CLAUDE.md「LLM 调用纪律」在这里落成代码:
- **收窄输出面**:LLM 只能填一个 strict 工具的参数;schema 发出前先体检(每个 object 必须
  `additionalProperties: false` 且全部字段必填),不合格的 schema 不许发;
- **校验失败不「尽力解析」**:schema 不过 → 整轮不合格,带错误回炉;
- **用数据审 LLM**:`judge` 是调用方给的纯代码判卷(台词逐字核对原文、规则断言…),不是再问一次 LLM;
- **fail-loud**:轮次用完 → `GateFailed`,带上每一轮的问题;截断 / 拒答 / 连不上 → 原样抛出,不重试;
- **落盘**:每次运行(含失败)写一份 journal:输入、schema、每轮参数原文与问题、用量。**不含 key**。
"""
from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator

from tools.llm.client import LLMError, NoToolCall

Judge = Callable[[dict], list[str]]


class GateFailed(LLMError):
    def __init__(self, message: str, rounds: list, journal_path: Path):
        super().__init__(message)
        self.rounds = rounds
        self.journal_path = journal_path


class Fatal(str):
    """判卷问题里**回炉也改不好**的那种(如人在意见里要加的新角色还没建):出现就立即失败,不再白烧轮次
    —— 同 `max_tokens` 截断不进重试的道理(spec 2026-10-24-剧本分镜修改回路-design.md 0.1)。"""


@dataclass
class Round:
    n: int
    raw: str = ""
    problems: list[str] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    seconds: float = 0.0


@dataclass(frozen=True)
class GateResult:
    args: dict
    rounds: list
    journal_path: Path


def strict_schema_problems(schema: dict, path: str = "$") -> list[str]:
    """strict 工具模式的硬要求:每个 object 都 additionalProperties: false,且 required == 全部字段。"""
    out = []
    if schema.get("type") == "object" or "properties" in schema:
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is not False:
            out.append(f"{path}: object 必须 additionalProperties: false")
        if set(schema.get("required", [])) != set(props):
            out.append(f"{path}: required 必须列出全部字段 {sorted(props)}")
        for k, v in props.items():
            out += strict_schema_problems(v, f"{path}.{k}")
    if "items" in schema:
        out += strict_schema_problems(schema["items"], f"{path}[]")
    for key in ("anyOf", "oneOf", "allOf"):
        for i, sub in enumerate(schema.get(key, [])):
            out += strict_schema_problems(sub, f"{path}.{key}[{i}]")
    return out


def schema_errors(obj: dict, schema: dict) -> list[str]:
    errs = sorted(Draft202012Validator(schema).iter_errors(obj), key=lambda e: list(e.absolute_path))
    return [f"字段 {'/'.join(str(p) for p in e.absolute_path) or '(顶层)'}:{e.message}" for e in errs][:20]


def input_sha(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def image_part(png_or_jpeg: bytes) -> dict:
    """一张图片 → Chat 消息里的 image_url 段(data URL)。"""
    mime = "image/png" if png_or_jpeg[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(png_or_jpeg).decode()}"}}


def run_gated(*, client, model: str, task: str, system: str, user: str, tool_name: str, description: str,
              schema: dict, judge: Judge, journal_dir: Path, max_rounds: int = 3, max_tokens: int = 8000,
              images: list[bytes] = ()) -> GateResult:
    lint = strict_schema_problems(schema)
    if lint:
        raise ValueError("schema 不符合 strict 要求,不许发给 LLM:" + ";".join(lint))
    content = [{"type": "text", "text": user}, *map(image_part, images)] if images else user
    messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
    rounds: list[Round] = []
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = Path(journal_dir) / "llm" / f"{stamp}-{task}-{input_sha(system, user, schema)[:8]}.json"
    shown = [{"sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)} for b in images]   # journal 不存图本身

    def dump(status: str, args: dict | None, error: str = "") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "task": task, "model": model, "status": status, "error": error, "system": system, "user": user,
            "images": shown, "schema": schema, "rounds": [r.__dict__ for r in rounds], "result": args,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    for n in range(1, max_rounds + 1):
        rnd = Round(n)
        rounds.append(rnd)
        try:
            res = client.call_tool(model=model, messages=messages, tool_name=tool_name, description=description,
                                   schema=schema, max_tokens=max_tokens)
        except NoToolCall as e:
            rnd.problems = [str(e)]
        except LLMError as e:                      # 截断 / 拒答 / 连不上:不重试,落盘后原样抛
            rnd.problems = [f"{type(e).__name__}: {e}"]
            dump("error", None, str(e))
            raise
        else:
            rnd.raw, rnd.usage, rnd.seconds = res.raw, res.usage, res.seconds
            rnd.problems = schema_errors(res.args, schema) or judge(res.args)
            if not rnd.problems:
                dump("ok", res.args)
                return GateResult(res.args, rounds, path)
            fatal = [p for p in rnd.problems if isinstance(p, Fatal)]
            if fatal:
                dump("failed", None, fatal[0])
                raise GateFailed(f"{task}:{fatal[0]}(回炉也改不好,不再重试)", rounds, path)
        feedback = "上一版不合格,请逐条修正后重新调用工具 " + tool_name + ":\n" + "\n".join(f"- {p}" for p in rnd.problems)
        if rnd.raw:
            messages.append({"role": "assistant", "content": f"(上一版工具参数){rnd.raw}"})
        messages.append({"role": "user", "content": feedback})
    dump("failed", None, f"{max_rounds} 轮都没过判卷")
    raise GateFailed(f"{task}:{max_rounds} 轮都没过判卷。最后一轮的问题:" + ";".join(rounds[-1].problems[:5]),
                     rounds, path)
