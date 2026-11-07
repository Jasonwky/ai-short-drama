# -*- coding: utf-8 -*-
"""到期清理(spec 1.3):删原文文件、置 purged_at;指纹与内容行留着。
每天一次(`daily`,Runtime 的 refs-fetch 线程调);拉黑时面板直接调 `purge` 立即清。
顺延:还没拆解的作品顺延到 hard_due_at(保留天数 × 2);拉黑的作品立即删。1a 没有拆解 → has_breakdown 恒假。"""
from __future__ import annotations

from pathlib import Path

from tools.refs import clock, rjournal


def has_breakdown(store, work_id: int) -> bool:
    return False          # 1c:查 ref_card 里这部作品有没有已批准 / 待审的卡


def _delete_files(store, root: Path, rows, now: str) -> tuple[list[int], list[str]]:
    """删这些内容行的文件、置 purged_at;到期清理与「删除这部的原文」共用。文件找不到不标已清理、报错。"""
    done, errors = [], []
    for r in rows:
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
    return done, errors


def purge(store, root: Path, *, now: str | None = None) -> tuple[list[int], list[str]]:
    now = now or clock.now_iso()
    rows = [r for r in store.conn.execute(
        "SELECT c.id, c.path, c.work_id, c.due_at, c.hard_due_at, w.blocked FROM ref_content c JOIN ref_work w ON w.id = c.work_id"
        " WHERE c.purged_at = ''")
        if r["blocked"] or (r["due_at"] if has_breakdown(store, r["work_id"]) else r["hard_due_at"]) <= now]
    done, errors = _delete_files(store, root, rows, now)
    if done or errors:
        rjournal.append(root, "purge", purged=done, errors=errors)
    return done, errors


def live_content(store, work_id: int) -> list[dict]:
    """这部作品还没清理的内容行(「删除这部的原文」要删的就是它们)。"""
    return [dict(r) for r in store.conn.execute(
        "SELECT id, kind, path FROM ref_content WHERE work_id = ? AND purged_at = '' ORDER BY id", (work_id,))]


def purge_work(store, root: Path, work_id: int, *, now: str | None = None) -> tuple[list[int], list[str]]:
    """人手动删一部作品的全部原文:不看到期、不拉黑、不改关注;指纹与内容行留着。"""
    done, errors = _delete_files(store, root, live_content(store, work_id), now or clock.now_iso())
    if done or errors:
        rjournal.append(root, "purge", work_id=work_id, manual=True, purged=done, errors=errors)
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
