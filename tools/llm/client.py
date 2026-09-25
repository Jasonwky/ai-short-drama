# -*- coding: utf-8 -*-
"""LLM 客户端:SynScale(OpenAI 兼容)的 Chat Completions,**只用「强制调用一个 strict 工具」这一种传法**。

2026-09-24 实测(`spike-results/2026-09-24-SynScale结构化输出实测.md`):
- `response_format: json_schema` 与 Responses API 的 `text.format` 被**静默忽略** —— HTTP 200、输出是
  ```json 围栏包着的自由发挥,schema 连提示词都没进。这是本模块最要防的静默降级;
- 强制调用 strict 工具时,结构被守住(诱导加字段、填 enum 外的值都没成功)。

但「服务端守住了」不是我们的保证 —— 返回的参数**一律在客户端用 JSON Schema 再校验**(`gate.py`),
不合格带错误回炉。

几条 fail-loud 规矩(CLAUDE.md LLM 纪律):
- key 从 gitignored 的 `llm.txt` 运行时读,**不进库、不进日志、不进 journal**;没有 key 直接报错,不静默切 Fake;
- `finish_reason == "length"`(截断)→ `Truncated`,**不重试**(重试不会变短,纯烧钱);
- 拒答(`refusal` / `content_filter`)→ `Refused`;
- 没有调用工具 → `NoToolCall`,交给闸门当作一次不合格回炉;
- 网络 / HTTP 错误 → `LLMUnavailable`。

⚠️ 服务地址只有 http(https 被拒)—— key 与发送的剧本内容是**明文传输**。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

BASE_URL = "http://synscale.onesyn.ai/v1"
KEY_FILE = Path(__file__).resolve().parents[2] / "llm.txt"


class LLMError(RuntimeError):
    pass


class LLMUnavailable(LLMError):
    pass


class Truncated(LLMError):
    pass


class Refused(LLMError):
    pass


class NoToolCall(LLMError):
    pass


@dataclass(frozen=True)
class ToolResult:
    args: dict
    raw: str               # 工具参数的原文(未解析),落 journal 用
    model: str
    finish: str
    usage: dict
    seconds: float


def read_key(path: Path | None = None) -> str:
    path = path or KEY_FILE            # 运行时取模块变量(测试可替换),不在定义时绑死
    if not path.exists():
        raise LLMUnavailable(f"找不到 LLM key 文件 {path} —— 把 key 放进去(该文件已被 .gitignore 忽略)")
    key = path.read_text(encoding="utf-8").strip()
    if not key:
        raise LLMUnavailable(f"LLM key 文件 {path} 是空的")
    return key


class ChatClient:
    def __init__(self, key: str, base_url: str = BASE_URL, timeout: int = 300):
        self._key = key
        self.base_url = base_url
        self.timeout = timeout

    def call_tool(self, *, model: str, messages: list[dict], tool_name: str, description: str, schema: dict,
                  max_tokens: int) -> ToolResult:
        t0 = time.time()
        for n, spelling in enumerate(SPELLINGS, 1):
            body = request_body(model=model, messages=messages, tool_name=tool_name, description=description,
                                schema=schema, max_tokens=max_tokens, spelling=spelling)
            req = urllib.request.Request(self.base_url + "/chat/completions", data=json.dumps(body).encode("utf-8"),
                                         headers={"Authorization": f"Bearer {self._key}",
                                                  "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.loads(r.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as e:
                msg = e.read().decode("utf-8", "replace")
                if e.code == 400 and "tool_choice" in msg and n < len(SPELLINGS):
                    continue            # 只有「tool_choice 拼法不对」才换拼法重发;同一个请求,换个写法
                raise LLMUnavailable(f"LLM 返回 HTTP {e.code}:{msg[:300]}") from e
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                raise LLMUnavailable(f"连不上 LLM 服务 {self.base_url}:{e}") from e
        return parse_response(data, model, tool_name, time.time() - t0)


# `tool_choice` 的三种拼法,按顺序试。2026-09-24 实测 SynScale 同一个模型会路由到不同上游,各自只认一种:
# 「Missing required parameter: 'tool_choice.name'」(要顶层 name)、「missing `tool_choice.function`」(要 function.name)、
# 「Unknown parameter: 'tool_choice.function'」(不认 function)。P4 时 Chat 拼法对所有模型都能用,之后服务端变了
# (spike-results/2026-09-24-视觉LLM反推描述实测.md)。三种表达的是同一个请求,只换拼法,不是降级。
SPELLINGS = ("both", "flat", "chat")


def request_body(*, model: str, messages: list[dict], tool_name: str, description: str, schema: dict,
                 max_tokens: int, spelling: str = "both") -> dict:
    """强制调用一个 strict 工具的请求体。纯函数,可单测。"""
    choice = {"both": {"type": "function", "name": tool_name, "function": {"name": tool_name}},
              "flat": {"type": "function", "name": tool_name},
              "chat": {"type": "function", "function": {"name": tool_name}}}[spelling]
    return {
        "model": model, "messages": messages, "max_tokens": max_tokens,
        "tools": [{"type": "function", "function": {"name": tool_name, "description": description,
                                                    "strict": True, "parameters": schema}}],
        "tool_choice": choice,
    }


def parse_response(data: dict, model: str, tool_name: str, seconds: float) -> ToolResult:
    """把一次 Chat Completions 响应变成 ToolResult,或抛出带原因的异常。纯函数,可单测。"""
    choice = (data.get("choices") or [{}])[0]
    finish = choice.get("finish_reason") or ""
    msg = choice.get("message") or {}
    if finish == "length":
        raise Truncated(f"输出被截断(max_tokens 不够)—— 不重试,调大上限或缩小输入。usage={data.get('usage')}")
    if msg.get("refusal") or finish == "content_filter":
        raise Refused(f"模型拒答:{msg.get('refusal') or finish}")
    calls = [c for c in msg.get("tool_calls") or [] if c.get("function", {}).get("name") == tool_name]
    if not calls:
        raise NoToolCall(f"模型没有调用工具 {tool_name}(finish={finish}),正文:{(msg.get('content') or '')[:200]}")
    raw = calls[0]["function"].get("arguments") or ""
    try:
        args = json.loads(raw)
    except json.JSONDecodeError as e:
        raise NoToolCall(f"工具参数不是合法 JSON:{e};原文:{raw[:200]}") from e
    return ToolResult(args=args, raw=raw, model=data.get("model") or model, finish=finish,
                      usage=data.get("usage") or {}, seconds=round(seconds, 1))
