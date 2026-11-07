# -*- coding: utf-8 -*-
"""调度(spec 1.1 第 6 条、3.2):按来源的模式排任务。每轮跑一次,幂等(队列同日去重)。

- C:什么都不自动做;调度排的任务失败了当天不再排(`again_after_failure=False`),人点的可以重试;
  调度排的都标 auto = 1,跑之前 `jobs.next_waiting` 再核一遍(隔天不补跑、切了模式、上限调低就标失败);
- A / B:每天拉一次各榜单 + 已关注作品的详情;红果 sitemap 每周一次;控制台关过的日子不补,只拉今天(曲线上标缺口);
- 今天榜单齐了以后:A 按「前 K 名或涨得快」自动排抓正文,到每日上限就停(不补跑;上限改了马上生效);
  B 只标候选与理由,人勾选。人手动点的不算上限。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from tools.refs import clock, config, heat, jobs, settings, works
from tools.refs.sources import ADAPTERS


def _today_ranked(store, key: str, today: str) -> list[dict]:
    return [dict(r) for r in store.conn.execute(
        "SELECT w.id, w.source, w.blocked, MIN(h.rank) AS best, h.board FROM ref_heat h JOIN ref_work w ON w.id = h.work_id"
        " WHERE w.source = ? AND h.day = ? AND h.board != 'detail' GROUP BY w.id ORDER BY best", (key, today))]


def _reason(store, row: dict, top_k: int | None, today: str) -> str:
    if top_k is not None and row["best"] is not None and row["best"] <= top_k:
        return f"今天第 {row['best']} 名(前 {top_k} 名)"
    return heat.rising(store, row["id"], row["board"], today) or ""


def _has_content(store, wid: int) -> bool:
    return store.conn.execute("SELECT 1 FROM ref_content WHERE work_id = ? LIMIT 1", (wid,)).fetchone() is not None or \
        store.conn.execute("SELECT 1 FROM ref_job WHERE work_id = ? AND kind = 'content' AND state != 'failed' LIMIT 1",
                           (wid,)).fetchone() is not None


def plan(store, today: str | None = None, now: str | None = None) -> list[str]:
    today = today or clock.today()
    now = now or clock.now_iso()
    did: list[str] = []
    for src in settings.all_sources(store):
        key = src["key"]
        if key == "manual" or src["mode"] == "C" or settings.blocked_reason(src, today):
            continue
        a = ADAPTERS[key]
        if src["last_ranks_day"] != today:
            for b in a.boards:
                jobs.enqueue(store, key, "ranks", day=today, params={"board": b}, auto=True, again_after_failure=False)
            did.append(f"{key}:排今天的榜单")
        for (wid,) in store.conn.execute("SELECT id FROM ref_work WHERE source = ? AND followed = 1 AND blocked = 0", (key,)).fetchall():
            if a.content != "none":
                jobs.enqueue(store, key, "meta", work_id=wid, day=today, auto=True, again_after_failure=False)
        if a.sitemap and (not src["last_sitemap_at"] or
                          datetime.fromisoformat(now) - datetime.fromisoformat(src["last_sitemap_at"]) >= timedelta(days=config.SITEMAP_DAYS)):
            jobs.enqueue(store, key, "sitemap", day=today, auto=True, again_after_failure=False)
        if src["last_ranks_day"] != today or a.content == "none":      # 今天所有榜单都成功了才选作品(见 runner._ranks)
            continue
        for row in _today_ranked(store, key, today):
            if row["blocked"]:
                continue
            why = _reason(store, row, src["top_k"], today)
            if not why:
                continue
            if src["mode"] == "B":
                works.set_candidate(store, row["id"], why, today)
                continue
            if jobs.auto_content_today(store, key, today) >= src["cap_fetch"]:
                did.append(f"{key}:今日自动抓取已满 {src['cap_fetch']}/{src['cap_fetch']}")
                break
            if not _has_content(store, row["id"]):
                jobs.enqueue(store, key, "content", work_id=row["id"], day=today, auto=True, again_after_failure=False)
                did.append(f"{key}:自动抓 {row['id']}({why})")
    return did
