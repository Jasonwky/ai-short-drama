# -*- coding: utf-8 -*-
"""作品:平台 + 外部编号唯一;关注 / 拉黑是人的标记,upsert 不动它们。检索只在已收进来的作品里找(spec 3.1)。"""
from __future__ import annotations

import json

from tools.refs import clock
from tools.refs.sources import ADAPTERS
from tools.refs.sources.base import WorkMeta


def upsert(store, source: str, external_id: str, *, title: str = "") -> int:
    with store.write() as c:
        c.execute("INSERT OR IGNORE INTO ref_work (source, external_id, first_seen) VALUES (?, ?, ?)",
                  (source, external_id, clock.now_iso()))
        if title:
            c.execute("UPDATE ref_work SET title = ? WHERE source = ? AND external_id = ?", (title, source, external_id))
        return c.execute("SELECT id FROM ref_work WHERE source = ? AND external_id = ?", (source, external_id)).fetchone()[0]


def get(store, wid: int) -> dict:
    row = store.conn.execute("SELECT * FROM ref_work WHERE id = ?", (wid,)).fetchone()
    if row is None:
        raise KeyError(f"没有这部作品:{wid}")
    return dict(row)


def tags(w: dict) -> list[str]:
    return json.loads(w["tags_json"])


def roles(w: dict) -> list[str]:
    return json.loads(w["roles_json"])


def apply_meta(store, wid: int, m: WorkMeta) -> None:
    with store.write() as c:
        c.execute("UPDATE ref_work SET title = ?, author = ?, url = ?, tags_json = ?, intro = ?, roles_json = ? WHERE id = ?",
                  (m.title, m.author, m.url, json.dumps(m.tags, ensure_ascii=False), m.intro,
                   json.dumps(m.roles, ensure_ascii=False), wid))


def set_flag(store, wid: int, *, followed: bool | None = None, blocked: bool | None = None) -> None:
    with store.write() as c:
        if followed is not None:
            c.execute("UPDATE ref_work SET followed = ? WHERE id = ?", (int(followed), wid))
        if blocked is not None:
            c.execute("UPDATE ref_work SET blocked = ?, followed = 0 WHERE id = ?", (int(blocked), wid))


def set_candidate(store, wid: int, reason: str, day: str) -> None:
    with store.write() as c:
        c.execute("UPDATE ref_work SET candidate_reason = ?, candidate_day = ? WHERE id = ?", (reason, day, wid))


def search(store, q: str, limit: int = 50) -> list[dict]:
    like = f"%{q.strip()}%"
    return [dict(r) for r in store.conn.execute(
        "SELECT * FROM ref_work WHERE blocked = 0 AND (title LIKE ? OR author LIKE ? OR intro LIKE ? OR tags_json LIKE ?)"
        " ORDER BY id DESC LIMIT ?", (like, like, like, like, limit))]


def add_by_link(store, url: str) -> int:
    for key, a in ADAPTERS.items():
        ext = a.match(url)
        if ext:
            wid = upsert(store, key, ext)
            set_flag(store, wid, followed=True)
            return wid
    raise ValueError(f"认不出这个链接属于哪个来源:{url}(支持红果、晋江、17K、纵横、七猫、番茄的作品页)")
