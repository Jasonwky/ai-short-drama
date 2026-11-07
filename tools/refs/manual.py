# -*- coding: utf-8 -*-
"""人工投递(spec 3.1 作品详情):1a 只收文本(视频在 1b 随录屏、转写一起做)。来源说明必填;照样判定、算指纹、按 manual 来源的保留天数清理。"""
from __future__ import annotations

import hashlib
from pathlib import Path

from tools.refs import clock, fingerprint, judge, settings, works
from tools.refs.sources.base import Unit


def add_text(store, root: Path, *, title: str, text: str, origin: str, work_id: int | None = None) -> int:
    if not origin.strip():
        raise ValueError("来源说明没填 —— 写清楚这段文字是怎么来的(例如「自己看完第 1–5 集整理」)")
    src = settings.get(store, "manual")
    p = settings.fetch_problems(src)
    if p:
        raise ValueError(";".join(p))
    if work_id is not None:
        works.get(store, work_id)          # 作品不存在 → KeyError(面板 404),先查再写文件,不留孤儿文件
    else:
        n = store.conn.execute("SELECT COUNT(*) FROM ref_work WHERE source = 'manual'").fetchone()[0] + 1
        work_id = works.upsert(store, "manual", f"manual-{n}", title=title)
    verdict, reason = judge.chapter(Unit(0, title, text, None))
    at = clock.now_iso()
    rel = f"content/manual/{work_id}/{hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]}.txt"
    (Path(root) / rel).parent.mkdir(parents=True, exist_ok=True)
    (Path(root) / rel).write_text(text, encoding="utf-8")
    days = src["retention_days"]
    with store.write() as c:
        cid = c.execute("INSERT INTO ref_content (work_id, kind, title, path, sha256, origin, verdict, reason, fetched_at, due_at, hard_due_at)"
                        " VALUES (?, 'manual', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (work_id, title, rel, hashlib.sha256(text.encode("utf-8")).hexdigest(), origin.strip(), verdict, reason,
                         at, clock.add_days(at, days), clock.add_days(at, 2 * days))).lastrowid
    if verdict == "ok":
        fingerprint.add(store, cid, text)
    return cid
