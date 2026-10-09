# -*- coding: utf-8 -*-
"""操作留痕(CLAUDE.md「三支柱」之一):worker 与控制台的每一行动作都追加到 `<data_root>/journal/worker.log`。"""
from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

LOG = "journal/worker.log"


def append(data_root: Path, lines: list[str], *, prefix: str = "") -> list[str]:
    """带 UTC 时间戳追加,返回实际写入的行(调用方可以原样打印)。空列表不写。"""
    if not lines:
        return []
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out = [f"{stamp} {prefix}{line}" for line in lines]
    path = Path(data_root) / LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    return out


def tail(data_root: Path, n: int = 50) -> list[str]:
    path = Path(data_root) / LOG
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()[-n:]


@dataclass(frozen=True)
class Read:
    lines: list[str]     # 新的在前
    more: int            # 符合条件、但超出 limit 没显示的条数


def read(data_root: Path, *, since: str = "", until: str = "", prefixes: list[str] | None = None,
         mentions: tuple[str, int] | None = None, limit: int = 500) -> Read:
    """**逐行流式读整个文件**再过滤 —— 只读最后若干行的话,更早的时间段永远筛不到。
    since / until 是 UTC ISO 时间(与行首时间戳同格式,按字符串比较);prefixes 匹配时间戳后面的前缀
    (`host=` / `console` / `workflows` / `runtime`);mentions=("task", 12) 只要提到这条的行(整词,`task 1` 不匹配 `task 12`)。"""
    path = Path(data_root) / LOG
    if not path.exists():
        return Read([], 0)
    hit = re.compile(rf"(?<![\w=]){mentions[0]} {mentions[1]}(?!\d)") if mentions else None
    keep: deque[str] = deque(maxlen=limit)
    total = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            stamp, _, rest = line.partition(" ")
            if (since and stamp < since) or (until and stamp > until):
                continue
            if prefixes and not any(rest.startswith(p) for p in prefixes):
                continue
            if hit and not hit.search(rest):
                continue
            keep.append(line)
            total += 1
    return Read(list(reversed(keep)), total - len(keep))


_LINK = re.compile(r"(?<![\w=])(task|llm_run|export) (\d+)(?!\d)")
_HREF = {"task": "/task/{}", "llm_run": "/llm/run/{}", "export": "/trace/export/{}"}


def links(line: str) -> list[tuple[str, str | None]]:
    """把一行切成 (文字, 链接或 None) 的片段:`task 12` / `llm_run 3` / `export 2` 变成链接。模板逐段渲染,不拼 HTML。"""
    out, pos = [], 0
    for m in _LINK.finditer(line):
        if m.start() > pos:
            out.append((line[pos:m.start()], None))
        out.append((m.group(0), _HREF[m.group(1)].format(m.group(2))))
        pos = m.end()
    if pos < len(line):
        out.append((line[pos:], None))
    return out
