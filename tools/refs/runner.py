# -*- coding: utf-8 -*-
"""跑一条参考库任务(spec 1.1、4.1)。一次只跑一条;出错不吞,按种类落成熔断 / 暂停 / 重试 / 失败,原因写进任务与 journal/refs。"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from tools.refs import clock, config, fingerprint, heat, jobs, judge, rjournal, settings, works
from tools.refs.net import FetchError, Fetcher
from tools.refs.sources import ADAPTERS
from tools.refs.sources.base import AdapterError


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _content_row(store, root, src, wid, kind, unit, title, base, ext, text, origin, verdict, reason) -> int:
    """一份内容一行。文件名带内容 sha256 前 12 位(`{base}-{sha12}{ext}`),内容变了是新文件、旧的不被覆盖;
    同一作品同一单元同一内容还没清理的行已经有了(重试、隔天再抓没变)就返回它,不再写文件、不再算指纹。"""
    sha = _sha(text)
    row = store.conn.execute("SELECT id FROM ref_content WHERE work_id = ? AND kind = ? AND unit IS ? AND sha256 = ? AND purged_at = ''",
                             (wid, kind, unit, sha)).fetchone()
    if row:
        return row[0]
    rel = f"{base}-{sha[:12]}{ext}"
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    at = clock.now_iso()
    due, hard_due = settings.due_dates(src, at)
    with store.write() as c:
        cid = c.execute(
            "INSERT INTO ref_content (work_id, kind, unit, title, path, sha256, origin, verdict, reason, fetched_at, due_at, hard_due_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (wid, kind, unit, title, rel, sha, origin, verdict, reason, at, due, hard_due)).lastrowid
    if verdict == "ok":
        fingerprint.add(store, cid, text)
    return cid


def _ranks(store, root, adapter, f, job, today):
    board = jobs.params(job)["board"]
    items = adapter.list_ranks(f, board)
    for it in items:
        wid = works.upsert(store, adapter.key, it.external_id, title=it.title)
        heat.record(store, wid, board, today, it.rank, it.heat, it.raw)
    # 这个来源今天**所有榜单**都成功了才算「今天拉过榜单」(只成功一个不算,别的榜单下一轮照排)
    done = {jobs.params(j)["board"] for j in store.conn.execute(
        "SELECT * FROM ref_job WHERE source = ? AND kind = 'ranks' AND day = ? AND state = 'done'", (adapter.key, today))}
    if set(adapter.boards) <= done | {board}:
        with store.write() as c:
            c.execute("UPDATE ref_source SET last_ranks_day = ? WHERE key = ?", (today, adapter.key))
    return f"榜单 {board}:{len(items)} 条"


def _meta(store, root, adapter, f, job, today):
    w = works.get(store, job["work_id"])
    m = adapter.fetch_meta(f, w["external_id"])
    works.apply_meta(store, w["id"], m)
    heat.record(store, w["id"], "detail", today, None, m.heat, {k: v for k, v in m.stats.items() if k != "vids"})
    return f"详情:{m.title}"


def _content(store, root, adapter, f, job, today):
    src = settings.get(store, adapter.key)
    p = settings.fetch_problems(src)
    if p:
        raise ValueError(";".join(p))
    w = works.get(store, job["work_id"])
    if w["blocked"]:
        raise ValueError("这部作品已拉黑,不抓")
    base = f"content/{adapter.key}/{w['external_id']}"
    if adapter.content == "reviews":
        reviews = adapter.fetch_reviews(f, w["external_id"])
        text = "\n".join(json.dumps({"text": r.text, "rating": r.rating, "likes": r.likes, "at": r.at}, ensure_ascii=False)
                         for r in reviews)
        _content_row(store, root, src, w["id"], "review", None, f"剧评 {len(reviews)} 条", f"{base}/reviews-{today}", ".jsonl",
                     text, adapter.key, "ok" if reviews else "rejected", "" if reviews else "没有剧评")
        return f"剧评 {len(reviews)} 条"
    if adapter.content != "chapters":
        raise ValueError("这个来源只有元数据,不抓正文")
    m = adapter.fetch_meta(f, w["external_id"])
    works.apply_meta(store, w["id"], m)
    free = [u for u in m.units if u.free][: src["chapters_n"]]
    ok = 0
    for ref in free:
        u = adapter.fetch_unit(f, m, ref)
        verdict, reason = judge.chapter(u)
        _content_row(store, root, src, w["id"], "chapter", u.number, u.title, f"{base}/{u.number}", ".txt", u.text,
                     ref.url, verdict, reason)
        ok += verdict == "ok"
    return f"免费章 {len(free)} 个,收 {ok} 个"


def _sitemap(store, root, adapter, f, job, today):
    ids = adapter.sitemap(f)
    for ext in ids:
        works.upsert(store, adapter.key, ext)
    with store.write() as c:
        c.execute("UPDATE ref_source SET last_sitemap_at = ? WHERE key = ?", (clock.now_iso(), adapter.key))
    return f"sitemap {len(ids)} 条"


KINDS: dict[str, Callable] = {"ranks": _ranks, "meta": _meta, "content": _content, "sitemap": _sitemap}


def _streak(store, key: str, value: int | None) -> int:
    with store.write() as c:
        if value is None:
            c.execute("UPDATE ref_source SET fail_streak = fail_streak + 1 WHERE key = ?", (key,))
        else:
            c.execute("UPDATE ref_source SET fail_streak = ? WHERE key = ?", (value, key))
        return c.execute("SELECT fail_streak FROM ref_source WHERE key = ?", (key,)).fetchone()[0]


def run_one(store, root: Path, *, adapters: dict | None = None, fetcher: Callable[[str, int | None], object] | None = None,
            today: str | None = None) -> dict | None:
    """跑下一条等待中的任务;没有可跑的返回 None。fetcher(来源, 任务的 work_id) 造这一轮的取页器。"""
    adapters = adapters or ADAPTERS
    fetcher = fetcher or (lambda src, wid: Fetcher(root, src, work_id=wid))
    today = today or clock.today()
    job = jobs.next_waiting(store, today)
    if job is None:
        return None
    key = job["source"]
    jobs.start(store, job["id"])
    try:
        msg = KINDS[job["kind"]](store, root, adapters[key], fetcher(key, job["work_id"]), job, today)
    except FetchError as e:
        if e.kind == "unreachable" and job["attempts"] < config.FETCH_RETRIES:
            jobs.retry(store, job["id"], str(e))
        else:
            jobs.finish(store, job["id"], error=str(e))
            if e.kind == "disallowed":
                settings.pause(store, root, key, str(e))
            elif e.kind == "blocked":
                settings.trip(store, root, key, str(e), today)
        rjournal.append(root, "job", source=key, work_id=job["work_id"], job=job["id"], kind=job["kind"], error=str(e))
        return jobs.get(store, job["id"])
    except AdapterError as e:
        # AdapterError 不是 ValueError 的子类;放在 ValueError 分支前面,确保解析失败一定计入 fail_streak
        jobs.finish(store, job["id"], error=f"解析失败:{e}")
        if _streak(store, key, None) >= config.TRIP_AFTER:
            settings.trip(store, root, key, f"连续 {config.TRIP_AFTER} 次解析失败,多半网页改版 —— 最后一次:{e}", today)
        rjournal.append(root, "job", source=key, work_id=job["work_id"], job=job["id"], kind=job["kind"], error=str(e))
        return jobs.get(store, job["id"])
    except (ValueError, KeyError) as e:
        # 前提不满足(保留天数没填、作品已拉黑、只有元数据):给人看的原因,不计解析失败
        jobs.finish(store, job["id"], error=str(e))
        rjournal.append(root, "job", source=key, work_id=job["work_id"], job=job["id"], kind=job["kind"], error=str(e))
        return jobs.get(store, job["id"])
    except Exception as e:
        # 意外异常(代码 bug):任务不能卡在 running,原因写进任务与 journal;照样抛出,由 Runtime 挂到来源设置页
        msg = f"意外异常:{type(e).__name__}: {e}"
        jobs.finish(store, job["id"], error=msg)
        rjournal.append(root, "job", source=key, work_id=job["work_id"], job=job["id"], kind=job["kind"], error=msg)
        raise
    _streak(store, key, 0)
    jobs.finish(store, job["id"])
    rjournal.append(root, "job", source=key, work_id=job["work_id"], job=job["id"], kind=job["kind"], done=msg)
    return jobs.get(store, job["id"])
