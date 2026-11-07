# -*- coding: utf-8 -*-
"""来源设置(spec 1.2 ref_source、1.3、3.2)。

★ 必填项没有默认值:保留天数不填不让抓;小说「拆前 N 章」不填不抓正文;切 A 全自动还要两个每日上限与前 K 名。
改了马上生效(用量是现算的);每个改动写 ref_setting_log(不可改)+ journal/refs。
熔断(当天挡住,第二天自动放开)与暂停(人看过才恢复)也在这里。
"""
from __future__ import annotations

from pathlib import Path

from tools.refs import clock, rjournal

SOURCES = {"hongguo": "红果短剧", "jjwxc": "晋江文学城", "k17": "17K 小说网", "zongheng": "纵横中文网",
           "qimao": "七猫中文网", "fanqie": "番茄小说(只有元数据)", "manual": "人工投递"}
NOVELS = ("jjwxc", "k17", "zongheng", "qimao")
INT_FIELDS = {"cap_fetch": ("每天自动抓几部", 0), "cap_breakdown": ("每天自动拆解几次", 0),
              "retention_days": ("原文保留天数", 1), "chapters_n": ("小说拆前 N 章", 1),
              "top_k": ("A 模式自动选前 K 名", 1)}
MODES = ("A", "B", "C")


def ensure(store) -> None:
    with store.write() as c:
        for k in SOURCES:
            c.execute("INSERT OR IGNORE INTO ref_source (key) VALUES (?)", (k,))


def get(store, key: str) -> dict:
    row = store.conn.execute("SELECT * FROM ref_source WHERE key = ?", (key,)).fetchone()
    if row is None:
        raise KeyError(f"没有这个来源:{key}")
    return dict(row)


def all_sources(store) -> list[dict]:
    order = list(SOURCES)
    rows = [dict(r) for r in store.conn.execute("SELECT * FROM ref_source")]
    return sorted(rows, key=lambda r: order.index(r["key"]))


def fetch_problems(src: dict) -> list[str]:
    p = []
    if src["retention_days"] is None:
        p.append("保留天数没填(★ 必填)—— 不填不让抓")
    if src["key"] in NOVELS and src["chapters_n"] is None:
        p.append("小说拆前 N 章没填(★ 必填)—— 不填不抓正文")
    return p


def auto_problems(src: dict) -> list[str]:
    p = fetch_problems(src)
    for f in ("cap_fetch", "cap_breakdown", "top_k"):
        if src[f] is None:
            p.append(f"{INT_FIELDS[f][0]}没填(★ 必填)—— 切不到 A 全自动")
    return p


def _parse(field: str, raw: str):
    if field == "mode":
        if raw not in MODES:
            raise ValueError(f"模式只能是 A / B / C(收到「{raw}」)")
        return raw
    label, low = INT_FIELDS[field]
    raw = raw.strip()
    if raw == "":
        return None
    try:
        v = int(raw)
    except ValueError:
        raise ValueError(f"{label}应为整数(收到「{raw}」)") from None
    if v < low:
        raise ValueError(f"{label}不能小于 {low}(收到 {v})")
    return v


def update(store, root: Path, key: str, form: dict[str, str]) -> list[str]:
    """改设置;返回真正改了的字段。不合法整组拒绝(ValueError),一个字段也不写。"""
    cur = get(store, key)
    parsed = {f: _parse(f, v) for f, v in form.items() if f in INT_FIELDS or f == "mode"}
    new = {**cur, **parsed}
    if key == "manual" and new["mode"] != "C":
        raise ValueError("人工投递只能是 C(全手动)")
    if new["mode"] == "A":
        p = auto_problems(new)
        if p:
            raise ValueError(";".join(p))
    changed = [f for f in parsed if parsed[f] != cur[f]]
    if not changed:
        return []
    at = clock.now_iso()
    with store.write() as c:
        for f in changed:
            c.execute(f"UPDATE ref_source SET {f} = ? WHERE key = ?", (parsed[f], key))
            old, nv = cur[f], parsed[f]
            c.execute("INSERT INTO ref_setting_log (source, field, old, new, at) VALUES (?, ?, ?, ?, ?)",
                      (key, f, None if old is None else str(old), None if nv is None else str(nv), at))
    for f in changed:
        rjournal.append(root, "setting", source=key, field=f,
                        old=None if cur[f] is None else str(cur[f]), new=None if parsed[f] is None else str(parsed[f]))
    return changed


def _set_state(store, root: Path, key: str, state: str, reason: str, until: str = "") -> None:
    with store.write() as c:
        c.execute("UPDATE ref_source SET state = ?, state_reason = ?, state_until = ? WHERE key = ?",
                  (state, reason, until, key))
    rjournal.append(root, "state", source=key, state=state, reason=reason, until=until)


def trip(store, root: Path, key: str, reason: str, day: str) -> None:
    _set_state(store, root, key, "tripped", reason, day)


def pause(store, root: Path, key: str, reason: str) -> None:
    _set_state(store, root, key, "paused", reason)


def reset_state(store, root: Path, key: str) -> None:
    with store.write() as c:
        c.execute("UPDATE ref_source SET fail_streak = 0 WHERE key = ?", (key,))
    _set_state(store, root, key, "ok", "")


def blocked_reason(src: dict, today: str) -> str | None:
    if src["state"] == "paused":
        return f"已暂停:{src['state_reason']}"
    if src["state"] == "tripped" and src["state_until"] >= today:
        return f"今天熔断:{src['state_reason']}"
    return None
