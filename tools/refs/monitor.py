# -*- coding: utf-8 -*-
"""调度(spec 1.1 第 6 条、3.2):按来源的模式排任务。每轮跑一次,幂等(队列同日去重)。

- C:什么都不自动做;调度排的任务失败了当天不再排(`again_after_failure=False`),人点的可以重试;
  调度排的都标 auto = 1,跑之前 `jobs.next_waiting` 再核一遍(隔天不补跑、切了模式、上限调低就标失败);
- A / B:每天拉一次各榜单 + 已关注作品的详情;红果 sitemap 每周一次;控制台关过的日子不补,只拉今天(曲线上标缺口);
- 今天榜单齐了以后:A 按「前 K 名或涨得快」自动排抓正文,到每日上限就停(不补跑;上限改了马上生效);
  B 只标候选与理由,人勾选。人手动点的不算上限。
  红果 A 模式自动选中的作品另标「建议录屏:<理由>」—— 只标,不排录屏(录屏永远人点)。
- A / B 模式另排自动拆解(1c,spec 3.2「A 自动受上限、B 内容齐了就自动拆」,`_auto_breakdowns`):内容齐了、还没有卡、
  没有排着 / 在跑的拆解的作品,每天最多 `cap_breakdown` 部(用量现算、立即生效、不补跑;人点的不算)。今天自动排过的不再排;
  **自动拆解开跑过又失败的作品以后都不再自动排**(防空转,等人点「拆解这部」);最近一张卡被人驳回的也不再自动排。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from tools.refs import breakdown, clock, config, heat, jobs, retention, settings, works
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


def _has_card(store, wid: int) -> bool:
    """有已批准的卡,或有在跑 / 待审的卡运行,或最近一张卡运行**被人驳回**(审查 I5b:等人点,不自动重拆;
    被新一次拆解取代的是代码驳回、记了原因,不算):不再自动拆。清理顺延另用 `retention.has_breakdown`,只看已批准。"""
    if retention.has_breakdown(store, wid):
        return True
    row = store.conn.execute("SELECT state, error FROM llm_run WHERE task = 'ref_card' AND target_type = 'ref_work' AND target_id = ?"
                             " ORDER BY id DESC LIMIT 1", (wid,)).fetchone()
    return row is not None and (row["state"] in ("running", "proposed") or (row["state"] == "rejected" and not row["error"]))


def content_complete(store, wid: int) -> bool:
    """内容齐了(spec 3.2「内容齐了就自动拆」):有收进来的章节 / 转写;每集**最新**那份收进来、没清理的录屏都有转写
    (录屏 `x.webm` ↔ 转写 `x.transcript.json`;转写判了不收也算处理完了 —— 拆解照样出卡,依据范围写明没进);
    这部作品没有排着 / 在跑的抓取、录屏、转写任务。只看最新录屏与拆解只认最新内容(`breakdown.CURRENT`)同一口径。"""
    if store.conn.execute("SELECT 1 FROM ref_content WHERE work_id = ? AND kind IN ('chapter', 'transcript') AND verdict = 'ok'"
                          " AND purged_at = '' LIMIT 1", (wid,)).fetchone() is None:
        return False
    if store.conn.execute("SELECT 1 FROM ref_job WHERE work_id = ? AND kind IN ('content', 'record', 'transcribe')"
                          " AND state IN ('waiting', 'running') LIMIT 1", (wid,)).fetchone() is not None:
        return False
    for (path,) in store.conn.execute("SELECT path FROM ref_content WHERE id IN (SELECT MAX(id) FROM ref_content WHERE work_id = ?"
                                      " AND kind = 'recording' AND verdict = 'ok' AND purged_at = '' GROUP BY unit)", (wid,)).fetchall():
        if store.conn.execute("SELECT 1 FROM ref_content WHERE work_id = ? AND kind = 'transcript' AND path = ?",
                              (wid, path.removesuffix(".webm") + ".transcript.json")).fetchone() is None:
            return False
    return True


def _auto_content_done(store, wid: int) -> bool:
    return store.conn.execute("SELECT 1 FROM ref_job WHERE work_id = ? AND kind = 'content' AND auto = 1 AND state = 'done' LIMIT 1",
                              (wid,)).fetchone() is not None


def _auto_breakdowns(store, key: str, src: dict, today: str) -> list[str]:
    """A / B 模式自动拆解(spec 3.2),都按 cap_breakdown 计数;没填或填 0 = 不自动拆(B 模式可以不填,不报错)。
    条件:内容齐了(`content_complete`);A 模式另要自动抓过内容 —— 红果除外(正片来自人点录屏,齐了也算);
    还没有卡(`_has_card`);没有排着 / 在跑的拆解;今天没自动排过;自动拆解**开跑过**又失败的以后不再自动排
    (没开跑就被复核挡掉的不算,审查 I5a)。"""
    cap = src["cap_breakdown"]
    if not cap:
        return []
    did = []
    for (wid,) in store.conn.execute(
            "SELECT w.id FROM ref_work w WHERE w.source = ? AND w.blocked = 0"
            " AND NOT EXISTS (SELECT 1 FROM ref_job j WHERE j.work_id = w.id AND j.kind = 'breakdown'"
            "                 AND (j.state IN ('waiting', 'running')"
            "                      OR (j.auto = 1 AND (j.day = ? OR (j.state = 'failed' AND j.started_at != '')))))"
            " ORDER BY w.id", (key, today)).fetchall():
        if not content_complete(store, wid) or _has_card(store, wid):
            continue
        if src["mode"] == "A" and key != "hongguo" and not _auto_content_done(store, wid):
            continue
        if jobs.auto_breakdown_today(store, key, today) >= cap:
            did.append(f"{key}:今日自动拆解已满 {cap}/{cap}")
            break
        breakdown.enqueue(store, wid, auto=True, day=today)
        did.append(f"{key}:自动拆解 {wid}")
    return did


def plan(store, today: str | None = None, now: str | None = None) -> list[str]:
    today = today or clock.today()
    now = now or clock.now_iso()
    did: list[str] = []
    for src in settings.all_sources(store):
        key = src["key"]
        if key == "manual" or src["mode"] == "C" or settings.blocked_reason(src, today):
            continue
        a = ADAPTERS[key]
        if src["mode"] in ("A", "B"):
            did += _auto_breakdowns(store, key, src, today)
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
        capped = False
        for row in _today_ranked(store, key, today):
            if row["blocked"]:
                continue
            why = _reason(store, row, src["top_k"], today)
            if not why:
                continue
            if src["mode"] == "B":
                works.set_candidate(store, row["id"], why, today)
                continue
            if key == "hongguo":            # 正片要录屏,录屏永远人点(spec 3.2):只标「建议录屏」,不排 record;抓取满了也照标
                works.set_candidate(store, row["id"], f"建议录屏:{why}", today)
            if capped:
                continue
            if jobs.auto_content_today(store, key, today) >= src["cap_fetch"]:
                did.append(f"{key}:今日自动抓取已满 {src['cap_fetch']}/{src['cap_fetch']}")
                capped = True
                if key != "hongguo":
                    break
                continue
            if not _has_content(store, row["id"]):
                jobs.enqueue(store, key, "content", work_id=row["id"], day=today, auto=True, again_after_failure=False)
                did.append(f"{key}:自动抓 {row['id']}({why})")
    return did
