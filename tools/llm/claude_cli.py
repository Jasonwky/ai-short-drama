# -*- coding: utf-8 -*-
"""LLM 后端:本机 Claude Code 命令行(`claude -p`)。用户 2026-10-23 定:SynScale 连续两天拒绝连接,换成本机 Claude。

接口与 `client.ChatClient.call_tool` 一样(闸门 `gate.run_gated` 不用改):
- 结构化输出走 `--json-schema`,结果在输出的 `structured_output` 里;**照样由闸门在客户端再校验 + 代码判卷**,
  命令行的校验不是我们的保证;
- 输入走 `--input-format stream-json`(一条用户消息,可带 base64 图片 —— 看图任务要用);
  闸门回炉时的「上一版参数 + 判卷意见」接在同一条用户消息后面(命令行一次只收一条);
- **隔离**:工作目录是每次新建的空目录、`--setting-sources ""`、`--strict-mcp-config`、`--tools ""`、
  `--system-prompt-file` 换掉默认系统提示 —— 不读本仓 CLAUDE.md、不带任何工具。喂进去的剧本是数据不是指令。
  2026-10-23 实测:不隔离时一次小调用带进 1.7 万 token 上下文,隔离后 3 千;
  `--bare` 不认账号登录(报「Not logged in」),所以不用它;
- 直接调 `claude.exe`,不经 npm 的 `.CMD` 包装:cmd.exe 会改写参数里的 `|` `&` 之类,命令行也有 8191 字符上限。

失败分流与 `ChatClient` 同一口径(CLAUDE.md LLM 纪律):截断 → `Truncated` 不重试;拒答 → `Refused`;
没有结构化输出 → `NoToolCall`(闸门当一次不合格回炉);没登录 / 进程失败 / 超时 → `LLMUnavailable`。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from tools.llm.client import LLMUnavailable, NoToolCall, Refused, ToolResult, Truncated

PACKAGE_EXE = Path("node_modules/@anthropic-ai/claude-code/bin/claude.exe")


def find_exe() -> str:
    """claude.exe 的位置:npm 全局包里那份;找不到就报错,不猜。"""
    wrapper = shutil.which("claude")
    if wrapper:
        exe = Path(wrapper).parent / PACKAGE_EXE
        if exe.is_file():
            return str(exe)
    exe = shutil.which("claude.exe")
    if exe:
        return exe
    raise LLMUnavailable("找不到本机 Claude Code 命令行(claude.exe)—— 先安装并登录 Claude Code")


def _blocks(content) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    out = []
    for part in content:
        if part["type"] == "text":
            out.append({"type": "text", "text": part["text"]})
        elif part["type"] == "image_url":            # gate.image_part 出的 data URL
            head, data = part["image_url"]["url"].split(",", 1)
            out.append({"type": "image", "source": {"type": "base64", "media_type": head[5:].split(";")[0], "data": data}})
        else:
            raise ValueError(f"不认识的消息段 {part['type']}")
    return out


def to_input(messages: list[dict]) -> tuple[str, dict]:
    """闸门的 Chat 消息 → (系统提示, 一条 stream-json 用户消息)。纯函数。"""
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    blocks = []
    for m in messages:
        if m["role"] in ("user", "assistant"):
            blocks += _blocks(m["content"])
    return system, {"type": "user", "message": {"role": "user", "content": blocks}}


def build_args(exe: str, *, model: str, schema: dict, system_file: str) -> list[str]:
    return [exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--json-schema", json.dumps(schema, ensure_ascii=False), "--system-prompt-file", system_file,
            "--tools", "", "--setting-sources", "", "--strict-mcp-config", "--no-session-persistence",
            "--model", model]


def parse(stdout: str, model: str, seconds: float) -> ToolResult:
    """命令行 stream-json 输出 → ToolResult,或带原因的异常。纯函数。"""
    results = [json.loads(ln) for ln in stdout.splitlines() if ln.strip().startswith("{") and '"type":"result"' in ln.replace(" ", "")]
    if not results:
        raise LLMUnavailable(f"Claude 命令行没有 result 行:{stdout[-300:]}")
    r = results[-1]
    if r.get("is_error"):
        raise LLMUnavailable(f"Claude 命令行报错:{str(r.get('result'))[:300]}")
    stop = r.get("stop_reason") or ""
    out = r.get("structured_output")
    if out is None:
        if stop == "max_tokens":
            raise Truncated(f"输出被截断 —— 不重试,缩小输入。usage={r.get('usage')}")
        if stop == "refusal":
            raise Refused(f"模型拒答:{str(r.get('result'))[:200]}")
        raise NoToolCall(f"没有结构化输出(stop={stop}),正文:{str(r.get('result'))[:200]}")
    real = next(iter(r.get("modelUsage") or {}), model)
    usage = {**(r.get("usage") or {}), "total_cost_usd": r.get("total_cost_usd")}
    return ToolResult(args=out, raw=json.dumps(out, ensure_ascii=False), model=real, finish=stop, usage=usage,
                      seconds=round(seconds, 1))


class ClaudeCLIClient:
    def __init__(self, exe: str | None = None, timeout: int = 900):
        self.exe = exe or find_exe()
        self.timeout = timeout

    def call_tool(self, *, model: str, messages: list[dict], tool_name: str, description: str, schema: dict,
                  max_tokens: int) -> ToolResult:
        # tool_name / description:命令行的 --json-schema 不需要工具名;max_tokens 命令行不收,截断照样由 stop_reason 判
        system, msg = to_input(messages)
        t0 = time.time()
        with tempfile.TemporaryDirectory(prefix="asd_llm_") as work:     # 空目录:不读任何项目的 CLAUDE.md
            sys_file = Path(work) / "system.txt"
            sys_file.write_text(system, encoding="utf-8")
            try:
                p = subprocess.run(build_args(self.exe, model=model, schema=schema, system_file=str(sys_file)),
                                   input=json.dumps(msg, ensure_ascii=False) + "\n", capture_output=True, text=True,
                                   encoding="utf-8", timeout=self.timeout, cwd=work)
            except subprocess.TimeoutExpired as e:
                raise LLMUnavailable(f"Claude 命令行 {self.timeout} 秒没返回") from e
            except OSError as e:
                raise LLMUnavailable(f"启动 Claude 命令行失败:{e}") from e
        if p.returncode != 0 and '"type":"result"' not in p.stdout.replace(" ", ""):
            raise LLMUnavailable(f"Claude 命令行退出码 {p.returncode}:{(p.stderr or p.stdout)[-300:]}")
        return parse(p.stdout, model, time.time() - t0)
