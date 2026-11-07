# -*- coding: utf-8 -*-
"""参考库的操作留痕(三支柱):每次抓取的 URL / 状态码、当时的 robots 原文、设置改动、熔断 / 暂停、清理,
按北京日期一天一个 `journal/refs/<日>.jsonl`。**不存页面正文**(spec 1.1)。查看器在来源设置与作品详情里(只读)。"""
from __future__ import annotations

import json
from pathlib import Path

from tools.refs import clock

DIR = Path("journal") / "refs"


def append(root: Path, event: str, **fields) -> dict:
    rec = {"at": clock.now_iso(), "event": event, **fields}
    path = Path(root) / DIR / f"{clock.today(rec['at'])}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def read(root: Path, *, source: str | None = None, work_id: int | None = None, event: str | None = None,
         limit: int = 100) -> list[dict]:
    """新的在前。逐文件从新到旧读,够 limit 条就停。
    坏行(不是 UTF-8 / 不是 JSON 对象)跳过,换成一条 `{"event": "corrupt", "file", "line"}`(不受 source / work_id 筛选,
    面板上看得见);只有最新文件的最后一行坏了不报 —— 那是后台线程正在写一半。"""
    d = Path(root) / DIR
    if not d.is_dir():
        return []
    out: list[dict] = []
    for fi, path in enumerate(sorted(d.glob("*.jsonl"), reverse=True)):
        lines = path.read_bytes().splitlines()
        for li, raw in enumerate(reversed(lines)):
            try:
                rec = json.loads(raw.decode("utf-8"))
                if not isinstance(rec, dict):
                    raise ValueError("不是对象")
            except ValueError:                # JSONDecodeError / UnicodeDecodeError 都是 ValueError
                if fi == 0 and li == 0:
                    continue
                if event in (None, "corrupt"):
                    out.append({"event": "corrupt", "file": path.name, "line": len(lines) - li})
                    if len(out) >= limit:
                        return out
                continue
            if source and rec.get("source") != source:
                continue
            if work_id is not None and rec.get("work_id") != work_id:
                continue
            if event and rec.get("event") != event:
                continue
            out.append(rec)
            if len(out) >= limit:
                return out
    return out
