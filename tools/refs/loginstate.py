# -*- coding: utf-8 -*-
"""红果登录结论:存 refs.db 的 meta 表(key `login_state`,JSON:state / at / text),录屏前检查与扫码登录都写它,
同时照旧写 journal/refs 的 login 事件(留痕)。面板只读 meta —— 不为了一个角标每个页面去扫 journal。"""
from __future__ import annotations

import json
from pathlib import Path

from tools.refs import clock, rjournal

KEY = "login_state"


def record(refs, root: Path, ok: bool, text: str, **journal_fields) -> dict:
    st = {"state": "ok" if ok else "failed", "at": clock.now_iso(), "text": text}
    with refs.write() as c:
        c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (KEY, json.dumps(st, ensure_ascii=False)))
    rjournal.append(root, "login", source="hongguo", ok=ok, via=text, **journal_fields)
    return st


def latest(refs) -> dict | None:
    """{"ok", "at", "text"};从没查过 → None。"""
    row = refs.conn.execute("SELECT value FROM meta WHERE key = ?", (KEY,)).fetchone()
    if row is None:
        return None
    st = json.loads(row[0])
    return {"ok": st["state"] == "ok", "at": st["at"], "text": st["text"]}
