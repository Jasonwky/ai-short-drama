# -*- coding: utf-8 -*-
"""操作留痕(CLAUDE.md「三支柱」之一):worker 与控制台的每一行动作都追加到 `<data_root>/journal/worker.log`。"""
from __future__ import annotations

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
