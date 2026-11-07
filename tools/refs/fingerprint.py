# -*- coding: utf-8 -*-
"""原文指纹(spec 1.2、2.4):小说章节、视频转写、剧评,去掉标点空白后每个 WINDOW 字滑动窗口一个 64 位哈希,永久保留。
WINDOW = 12 已标定(spike-results/2026-11-07-指纹重叠阈值标定.md:16 部作品 12.1 万字里 10 字及以上跨作品重叠为 0、8 字开始误报常用语);1c 库变大后再量一次。"""
from __future__ import annotations

import hashlib
import re

WINDOW = 12


def normalize(text: str) -> str:
    return "".join(re.findall(r"[一-鿿A-Za-z0-9]", text))


def _h(s: str) -> int:
    return int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest(), "big", signed=True)


def windows(text: str) -> list[int]:
    t = normalize(text)
    return [_h(t[i:i + WINDOW]) for i in range(len(t) - WINDOW + 1)]


def add(store, content_id: int, text: str) -> int:
    hs = windows(text)
    with store.write() as c:
        c.executemany("INSERT OR IGNORE INTO ref_fingerprint (content_id, pos, h) VALUES (?, ?, ?)",
                      [(content_id, i, h) for i, h in enumerate(hs)])
    return len(hs)


def overlaps(store, text: str) -> list[dict]:
    """text 里与库中原文连续 ≥ WINDOW 字相同的片段:[{content_id, start, length}],按出现先后。"""
    t = normalize(text)
    hs = [_h(t[i:i + WINDOW]) for i in range(len(t) - WINDOW + 1)]
    hits: dict[int, int] = {}
    for i, h in enumerate(hs):
        row = store.conn.execute("SELECT content_id FROM ref_fingerprint WHERE h = ? LIMIT 1", (h,)).fetchone()
        if row:
            hits[i] = row[0]
    out: list[dict] = []
    for i in sorted(hits):
        if out and out[-1]["content_id"] == hits[i] and out[-1]["start"] + out[-1]["length"] - WINDOW + 1 == i:
            out[-1]["length"] += 1
        else:
            out.append({"content_id": hits[i], "start": i, "length": WINDOW})
    return out
