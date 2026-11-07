# -*- coding: utf-8 -*-
"""到期清理(spec 1.3):删原文文件、置 purged_at;指纹与内容行留着。
每天一次(`daily`,Runtime 的 refs-fetch 线程调);拉黑时面板直接调 `purge` 立即清。
顺延:还没拆解的作品顺延到 hard_due_at(保留天数 × 2);拉黑的作品立即删。1a 没有拆解 → has_breakdown 恒假。"""
from __future__ import annotations

from pathlib import Path

from tools.refs import clock, rjournal


def has_breakdown(store, work_id: int) -> bool:
    return False          # 1c:查 ref_card 里这部作品有没有已批准 / 待审的卡


def purge(store, root: Path, *, now: str | None = None) -> tuple[list[int], list[str]]:
    now = now or clock.now_iso()
    done, errors = [], []
    rows = store.conn.execute(
        "SELECT c.id, c.path, c.work_id, c.due_at, c.hard_due_at, w.blocked FROM ref_content c JOIN ref_work w ON w.id = c.work_id"
        " WHERE c.purged_at = ''").fetchall()
    for r in rows:
        due = r["due_at"] if has_breakdown(store, r["work_id"]) else r["hard_due_at"]
        if not (r["blocked"] or due <= now):
            continue
        p = Path(root) / r["path"]
        try:
            p.unlink()
        except FileNotFoundError:
            if store.conn.execute("SELECT purged_at FROM ref_content WHERE id = ?", (r["id"],)).fetchone()[0]:
                continue                  # 另一个线程(拉黑时的立即清理)刚清掉了这一行
            errors.append(f"内容 {r['id']} 的文件找不到:{r['path']}(没有标已清理,请人看一下)")
            continue
        except OSError as e:
            errors.append(f"内容 {r['id']} 删除失败:{e}")
            continue
        with store.write() as c:
            c.execute("UPDATE ref_content SET purged_at = ? WHERE id = ?", (now, r["id"]))
        done.append(r["id"])
    if done or errors:
        rjournal.append(root, "purge", purged=done, errors=errors)
    return done, errors


def daily(store, root: Path, *, today: str | None = None) -> tuple[list[int], list[str]] | None:
    """到期清理每个北京日一次(spec 4.1);今天跑过返回 None。日期记在 refs.db 的 meta.last_purge_day。"""
    today = today or clock.today()
    row = store.conn.execute("SELECT value FROM meta WHERE key = 'last_purge_day'").fetchone()
    if row and row[0] == today:
        return None
    res = purge(store, root)
    with store.write() as c:
        c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_purge_day', ?)", (today,))
    return res
