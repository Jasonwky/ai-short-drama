# -*- coding: utf-8 -*-
"""本机抓取队列(spec 1.1 第 2 条):单独的后台线程一次跑一条,不进 GPU worker。参数写下不可改(触发器)。"""
from __future__ import annotations

import json

from tools.refs import clock, settings

FETCH_KINDS = ("ranks", "meta", "content", "sitemap")     # 抓取线程只取这些;录屏 / 转写各有自己的线程


def enqueue(store, source: str, kind: str, *, day: str, work_id: int | None = None, params: dict | None = None,
            auto: bool = False, again_after_failure: bool = True, dedupe_states: tuple[str, ...] | None = None) -> int:
    """同日同键已有任务就返回它。人点的(again_after_failure=True)失败后可以再排一条;
    调度排的传 False:失败了今天就不再排(否则每轮都重排、每 5 秒打一次网站)。
    dedupe_states 给了就只和这些状态的旧任务去重(录屏「重录」、重新转写:done 之后同一天还能再排)。"""
    pj = json.dumps(params or {}, ensure_ascii=False, sort_keys=True)
    if dedupe_states is not None:
        cond, extra = f" AND state IN ({', '.join('?' * len(dedupe_states))})", tuple(dedupe_states)
    else:
        cond, extra = (" AND state != 'failed'" if again_after_failure else ""), ()
    with store.write() as c:
        row = c.execute("SELECT id FROM ref_job WHERE source = ? AND kind = ? AND work_id IS ? AND params_json = ? AND day = ?"
                        + cond + " ORDER BY id DESC", (source, kind, work_id, pj, day, *extra)).fetchone()
        if row:
            return row[0]
        return c.execute("INSERT INTO ref_job (source, kind, work_id, params_json, auto, day, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (source, kind, work_id, pj, int(auto), day, clock.now_iso())).lastrowid


def get(store, jid: int) -> dict:
    return dict(store.conn.execute("SELECT * FROM ref_job WHERE id = ?", (jid,)).fetchone())


def params(job: dict) -> dict:
    return json.loads(job["params_json"])


def _auto_refusal(store, job: dict, src: dict, today: str) -> str | None:
    """调度排的任务(auto = 1)到跑的时候再核一遍(spec 3.2:改了马上生效、不补跑)。人点的不核。"""
    if job["day"] != today:
        return f"自动任务不补跑(排于 {job['day']} 日)"
    if job["kind"] == "content":
        if src["mode"] != "A":
            return "来源已不是 A 模式"
        used = store.conn.execute("SELECT COUNT(*) FROM ref_job WHERE source = ? AND kind = 'content' AND auto = 1 AND day = ?"
                                  " AND state IN ('running', 'done')", (job["source"], today)).fetchone()[0]
        if src["cap_fetch"] is None or used >= src["cap_fetch"]:
            return f"今日自动抓取已满 {used}/{src['cap_fetch'] if src['cap_fetch'] is not None else '未填'}"
    elif job["kind"] == "breakdown":                   # A、B 都自动拆(spec 3.2),切到 C 才不跑
        if src["mode"] == "C":
            return "来源已切到 C 全手动"
        used = store.conn.execute("SELECT COUNT(*) FROM ref_job WHERE source = ? AND kind = 'breakdown' AND auto = 1 AND day = ?"
                                  " AND started_at != ''", (job["source"], today)).fetchone()[0]     # 开跑过的(含跑了失败的)才算用量
        if src["cap_breakdown"] is None or used >= src["cap_breakdown"]:
            return f"今日自动拆解已满 {used}/{src['cap_breakdown'] if src['cap_breakdown'] is not None else '未填'}"
    elif src["mode"] == "C":
        return "来源已切到 C 全手动"
    return None


def next_waiting(store, today: str, kinds: tuple[str, ...] = FETCH_KINDS) -> dict | None:
    """kinds 里下一条能跑的。来源被挡住的留着等;调度排的任务前提已不成立就标失败(原因写进 error)再接着找。"""
    marks = ", ".join("?" * len(kinds))
    for row in store.conn.execute(f"SELECT * FROM ref_job WHERE state = 'waiting' AND kind IN ({marks}) ORDER BY id",
                                  kinds).fetchall():
        job = dict(row)
        src = settings.get(store, job["source"])
        if job["kind"] != "breakdown" and settings.blocked_reason(src, today) is not None:
            continue                     # 拆解只调本机 LLM、不碰网站:来源熔断 / 暂停不挡它
        why = _auto_refusal(store, job, src, today) if job["auto"] else None
        if why:
            finish(store, job["id"], error=why)
            continue
        return job
    return None


def start(store, jid: int) -> None:
    with store.write() as c:
        c.execute("UPDATE ref_job SET state = 'running', started_at = ? WHERE id = ?", (clock.now_iso(), jid))


def set_progress(store, jid: int, text: str) -> None:
    """录屏进度(可变列,参数触发器不含它)。"""
    with store.write() as c:
        c.execute("UPDATE ref_job SET progress = ? WHERE id = ?", (text, jid))


def finish(store, jid: int, *, error: str = "") -> None:
    with store.write() as c:
        c.execute("UPDATE ref_job SET state = ?, error = ?, finished_at = ? WHERE id = ?",
                  ("failed" if error else "done", error, clock.now_iso(), jid))


def retry(store, jid: int, error: str) -> None:
    with store.write() as c:
        c.execute("UPDATE ref_job SET state = 'waiting', attempts = attempts + 1, error = ? WHERE id = ?", (error, jid))


def fail_stale(store) -> int:
    """控制台重启:上次停在 running 的标失败(不重跑,人可以手动再点)。"""
    with store.write() as c:
        return c.execute("UPDATE ref_job SET state = 'failed', error = '控制台重启时没跑完', finished_at = ? WHERE state = 'running'",
                         (clock.now_iso(),)).rowcount


def auto_content_today(store, source: str, day: str) -> int:
    """今天自动抓取的用量:排着的 + 跑过的(含跑了失败的);没开跑就被标失败的(上限调低、切模式)不算。"""
    return store.conn.execute("SELECT COUNT(*) FROM ref_job WHERE source = ? AND kind = 'content' AND auto = 1 AND day = ?"
                              " AND (state != 'failed' OR started_at != '')", (source, day)).fetchone()[0]


def auto_breakdown_today(store, source: str, day: str) -> int:
    """今天自动拆解的用量,口径同 `auto_content_today`:排着的 + 开跑过的;没开跑就被标失败的不算。"""
    return store.conn.execute("SELECT COUNT(*) FROM ref_job WHERE source = ? AND kind = 'breakdown' AND auto = 1 AND day = ?"
                              " AND (state != 'failed' OR started_at != '')", (source, day)).fetchone()[0]


def recent(store, *, source: str | None = None, work_id: int | None = None, limit: int = 30) -> list[dict]:
    q, a = "SELECT * FROM ref_job WHERE 1 = 1", []
    if source:
        q += " AND source = ?"; a.append(source)
    if work_id is not None:
        q += " AND work_id = ?"; a.append(work_id)
    return [dict(r) for r in store.conn.execute(q + " ORDER BY id DESC LIMIT ?", (*a, limit))]
