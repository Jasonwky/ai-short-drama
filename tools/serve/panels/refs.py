# -*- coding: utf-8 -*-
"""参考库面板(spec 2026-11-07-参考库-design.md 第 3 节):来源设置 / 榜单与趋势 / 作品详情(1a)、录屏(1b)、拆解卡(1c)。
面板只排任务、改设置;抓取由 Runtime 的 refs-fetch 线程跑,录屏由 refs-record 线程跑(永远人点才排),
拆解由 Runtime 的参考库 LLM 推进(`breakdown`)。拆解卡审阅页:人逐条采纳 / 驳回假设、补「不可借鉴」,批准才写卡;
创作视图预览只渲染不写库。
没配参考库就如实显示「未配置」,不偷偷建库。"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, RedirectResponse

from tools.llm import ref_card as rcard
from tools.llm import runs, service
from tools.refs import (breakdown, clock, evidence, heat, jobs, loginstate, manual, retention, rjournal, settings,
                        transcript, trends, works)
from tools.refs.sources import ADAPTERS
from tools.refs.sources.hongguo import WEB_EPISODES
from tools.serve.panels.common import TEMPLATES, confirm_page, render, rt

router = APIRouter()
LIVE = ("waiting", "running")     # 人点的录屏 / 重新转写只和还没跑完的去重:跑完(成功或失败)同一天还能再排


def _off(request: Request, key: str):
    return render(request, "_refs_off.html", key)


def _refs(request: Request):
    """写操作要参考库;没配就 400 说原因(页面本身渲染「未配置」)。"""
    r = rt(request)
    if r.refs is None:
        raise ValueError("未配置参考库 —— 启动控制台时加 --refs-db(start.ps1 默认已带)")
    return r


def _fetch_refusal(source: str) -> str | None:
    """人工投递没有适配器,番茄只有元数据:不抓正文。"""
    a = ADAPTERS.get(source)
    if a is None:
        return f"{settings.SOURCES[source]}没有抓取适配器,不抓正文 —— 在作品详情里人工投递文本"
    if a.content == "none":
        return f"{settings.SOURCES[source]}只收元数据,不抓正文"
    return None


def login_status(r) -> dict | None:
    """红果最近一次登录结论(录屏前检查 / 扫码登录写进 refs.db 的 meta);扫码中不算结论。从没查过 → None。"""
    return loginstate.latest(r.refs)


def todo(refs, today: str, errors: int = 0, *, login: dict | None = None) -> list[str]:
    """待办:后台错误条数、被挡住的来源、B 模式今天的候选数、红果没登录、待审的拆解卡。"""
    out = [f"参考库后台有 {errors} 条错误(含清理失败),见上方"] if errors else []
    if login is not None and not login["ok"]:
        out.append(f"红果没登录 / 登录过期({login['at'][:16]} {login['text']})—— 在「录屏」面板点「打开浏览器扫码」")
    out += [f"{settings.SOURCES[s['key']]}:{why}" for s in settings.all_sources(refs)
           if (why := settings.blocked_reason(s, today))]
    n = refs.conn.execute("SELECT COUNT(*) FROM ref_work WHERE candidate_day = ? AND candidate_reason != ''"
                          " AND candidate_reason NOT LIKE '建议录屏%'", (today,)).fetchone()[0]      # 建议录屏只标不挑,不算候选
    if n:
        out.append(f"今天有 {n} 部候选等你挑")
    n = refs.conn.execute("SELECT COUNT(*) FROM llm_run WHERE task = 'ref_card' AND state = 'proposed'").fetchone()[0]
    if n:
        out.append(f"有 {n} 张拆解卡待审 —— 在「拆解卡」面板逐条表态、批准")
    return out


def llm_usage(refs, today: str) -> dict[str, dict]:
    """每个来源的参考库 LLM 调用次数(spec 3.1):今天 / 近 7 天,各含失败数。
    单元标注(ref_content)归内容所属作品的来源,整部汇总(ref_work)归作品的来源;日子按北京时间。"""
    out: dict[str, dict] = {}
    for r in refs.conn.execute(
            "SELECT l.created_at, l.state, COALESCE(wc.source, ww.source) AS source FROM llm_run l"
            " LEFT JOIN ref_content c ON l.target_type = 'ref_content' AND c.id = l.target_id"
            " LEFT JOIN ref_work wc ON wc.id = c.work_id"
            " LEFT JOIN ref_work ww ON l.target_type = 'ref_work' AND ww.id = l.target_id"):
        d = clock.days_between(clock.today(r["created_at"]), today)
        u = out.setdefault(r["source"], {"today": 0, "today_failed": 0, "week": 0, "week_failed": 0})
        failed = r["state"] == "failed"
        if d == 0:
            u["today"] += 1
            u["today_failed"] += failed
        if 0 <= d < 7:
            u["week"] += 1
            u["week_failed"] += failed
    return out


def todo_of(r) -> tuple[list[str], str | None]:
    """(待办, 读不出的原因)。读不出不许冒成整站 400,也不悄悄当成 0:导航角标显示「!」,来源设置页写原因。"""
    try:
        return todo(r.refs, clock.today(), len(r.refs_errors), login=login_status(r)), None
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"


@router.get("/refs/sources")
def sources(request: Request):
    r = rt(request)
    if r.refs is None:
        return _off(request, "refs_sources")
    today = clock.today()
    usage = llm_usage(r.refs, today)
    rows = []
    for s in settings.all_sources(r.refs):
        robots = rjournal.read(r.refs_root, source=s["key"], event="robots", limit=1)
        rows.append({"s": s, "label": settings.SOURCES[s["key"]], "blocked": settings.blocked_reason(s, today),
                     "fetch_problems": settings.fetch_problems(s), "auto_problems": settings.auto_problems(s),
                     "used": jobs.auto_content_today(r.refs, s["key"], today),
                     "boards": ADAPTERS[s["key"]].boards if s["key"] in ADAPTERS else {},
                     "robots": robots[0] if robots else None,
                     "llm": usage.get(s["key"], {"today": 0, "today_failed": 0, "week": 0, "week_failed": 0}),
                     "log": rjournal.read(r.refs_root, source=s["key"], limit=20)})
    items, todo_error = todo_of(r)
    return render(request, "refs_sources.html", "refs_sources", rows=rows, fields=settings.INT_FIELDS,
                  todo=items, todo_error=todo_error, refs_errors=r.refs_errors)


@router.post("/refs/errors/clear")
def clear_errors(request: Request):
    r = _refs(request)
    r.refs_errors = []
    return RedirectResponse("/refs/sources", status_code=303)


@router.post("/refs/source/{key}")
async def save_source(request: Request, key: str):
    r = _refs(request)
    settings.get(r.refs, key)                  # 没有这个来源 → 404
    form = {k: str(v) for k, v in (await request.form()).items()}
    settings.update(r.refs, r.refs_root, key, form)
    return RedirectResponse("/refs/sources", status_code=303)


@router.post("/refs/source/{key}/reset")
def reset_source(request: Request, key: str):
    r = _refs(request)
    settings.get(r.refs, key)                  # 没有这个来源 → 404
    settings.reset_state(r.refs, r.refs_root, key)
    return RedirectResponse("/refs/sources", status_code=303)


@router.post("/refs/source/{key}/ranks")
def pull_ranks(request: Request, key: str):
    r = _refs(request)
    why = settings.blocked_reason(settings.get(r.refs, key), clock.today())
    if why:
        raise ValueError(f"现在不能拉:{why}")
    if key not in ADAPTERS:
        raise ValueError(f"{settings.SOURCES[key]}没有榜单可拉")
    for b in ADAPTERS[key].boards:
        jobs.enqueue(r.refs, key, "ranks", day=clock.today(), params={"board": b})
    return RedirectResponse("/refs/sources", status_code=303)


@router.get("/refs/ranks")
def ranks(request: Request, source: str = "hongguo", board: str = "", q: str = ""):
    r = rt(request)
    if r.refs is None:
        return _off(request, "refs_ranks")
    today = clock.today()
    a = ADAPTERS.get(source)
    board = board or (next(iter(a.boards)) if a else "")
    if q.strip():
        rows = [{"w": w, "rank": None, "heat": None, "rising": None} for w in works.search(r.refs, q)]
    else:
        rows = [{"w": works.get(r.refs, h["work_id"]), "rank": h["rank"], "heat": h["heat"],
                 "rising": heat.rising(r.refs, h["work_id"], board, today)}
                for h in r.refs.conn.execute("SELECT h.work_id, h.rank, h.heat FROM ref_heat h JOIN ref_work w ON w.id = h.work_id"
                                             " WHERE w.source = ? AND h.board = ? AND h.day = ? ORDER BY h.rank",
                                             (source, board, today))]
    return render(request, "refs_ranks.html", "refs_ranks", rows=rows, source=source, board=board, q=q,
                  adapters=ADAPTERS, labels=settings.SOURCES, today=today, mode=settings.get(r.refs, source)["mode"] if a else "C",
                  trend_rows=_trend_rows(r.refs, source), trend_fields=TREND_FIELDS)


TREND_FIELDS = {"genre": "题材", "payoff": "爽点", "conflict": "冲突", "hook_type": "开场钩子"}


def _trend_rows(refs, source: str) -> list[dict]:
    """跨作品趋势(spec 2.6):已批准卡片的闭集字段按月计数,代码统计、不经 LLM。"""
    return [{"month": m["month"], "n": m["n"],
             "cells": {k: "、".join(f"{v} {c}" for v, c in m[k].most_common()) for k in TREND_FIELDS}}
            for m in trends.by_month(refs, source)]


@router.post("/refs/add")
def add(request: Request, url: str = Form(...)):
    r = _refs(request)
    wid = works.add_by_link(r.refs, url)
    src = works.get(r.refs, wid)["source"]
    if ADAPTERS[src].content != "none":           # 只有元数据的来源(番茄)没有详情抓取
        jobs.enqueue(r.refs, src, "meta", work_id=wid, day=clock.today())
    return RedirectResponse(f"/refs/work?id={wid}", status_code=303)


def _curve(points: list[dict], w: int = 320, h: int = 60) -> list[list[tuple[float, float]]]:
    """热度曲线的折线段(缺口处断开),坐标由代码算好给模板画 SVG。"""
    vals = [p["heat"] for p in points if p["heat"] is not None]
    if len(points) < 2 or not vals:
        return []
    lo, hi = min(vals), max(vals)
    segs, cur = [], []
    for i, p in enumerate(points):
        if p["heat"] is None:
            if cur:
                segs.append(cur)
            cur = []
            continue
        x = i * w / (len(points) - 1)
        y = h - (p["heat"] - lo) / ((hi - lo) or 1) * h
        cur.append((round(x, 1), round(y, 1)))
    if cur:
        segs.append(cur)
    return segs


@router.get("/refs/work")
def work(request: Request, id: int | None = None):
    r = rt(request)
    if r.refs is None:
        return _off(request, "refs_work")
    if id is None:
        row = r.refs.conn.execute("SELECT work_id FROM ref_content ORDER BY id DESC LIMIT 1").fetchone() or \
            r.refs.conn.execute("SELECT id FROM ref_work ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return render(request, "refs_work.html", "refs_work", w=None, contents=[], curves=[], jobs=[], log=[])
        id = row[0]
    w = works.get(r.refs, id)
    curves = []
    for b in heat.boards_of(r.refs, id):
        pts = heat.series(r.refs, id, b)
        curves.append({"board": b, "points": pts, "gaps": sum(p["gap"] for p in pts), "segs": _curve(pts)})
    contents = [dict(x) for x in r.refs.conn.execute("SELECT * FROM ref_content WHERE work_id = ? ORDER BY kind, unit, id", (id,))]
    return render(request, "refs_work.html", "refs_work", w=w, tags=works.tags(w), contents=contents, curves=curves,
                  jobs=jobs.recent(r.refs, work_id=id), log=rjournal.read(r.refs_root, work_id=id, limit=30),
                  label=settings.SOURCES[w["source"]], fetch_refusal=_fetch_refusal(w["source"]), web_episodes=WEB_EPISODES,
                  today=clock.today(), live_n=len(retention.live_content(r.refs, id)), bd=_card_row(r.refs, id))


@router.post("/refs/work/{wid}/manual")      # 要在 /refs/work/{wid}/{action} 之前注册,否则被通配吃掉
def manual_text(request: Request, wid: int, title: str = Form(...), text: str = Form(...), origin: str = Form("")):
    r = _refs(request)
    manual.add_text(r.refs, r.refs_root, title=title, text=text, origin=origin, work_id=wid)
    return RedirectResponse(f"/refs/work?id={wid}", status_code=303)


@router.post("/refs/work/{wid}/record")      # 同上,要在通配之前
def record_work(request: Request, wid: int, episode: int | None = Form(None)):
    """「录这部」(前 WEB_EPISODES 集)/ 单集「重录」:只排任务,录屏线程去录。录屏永远人点(spec 3.2),所以失败后可以再排。"""
    r = _refs(request)
    w = works.get(r.refs, wid)
    if w["source"] != "hongguo":
        raise ValueError(f"只有红果能录屏 —— 这部是{settings.SOURCES[w['source']]}的")
    if episode is not None and not 1 <= episode <= WEB_EPISODES:
        raise ValueError(f"网页只能看前 {WEB_EPISODES} 集(收到第 {episode} 集)")
    jobs.enqueue(r.refs, "hongguo", "record", day=clock.today(), work_id=wid,
                 params={"episodes": [episode]} if episode is not None else None, dedupe_states=LIVE)
    return RedirectResponse("/refs/record", status_code=303)


@router.post("/refs/work/{wid}/purge")       # 同上,要在通配之前
async def purge_work(request: Request, wid: int):
    """「删除这部的原文」两态:没带 confirm=1 先给确认页(不删任何东西),带了才删。指纹保留、不拉黑、不改关注。"""
    r = _refs(request)
    w = works.get(r.refs, wid)
    back = f"/refs/work?id={wid}"
    if (await request.form()).get("confirm") != "1":
        live = retention.live_content(r.refs, wid)
        if not live:
            raise ValueError("这部作品没有可删的原文")
        kinds: dict[str, int] = {}
        for x in live:
            kinds[x["kind"]] = kinds.get(x["kind"], 0) + 1
        return confirm_page(request, panel_key="refs_work", action=f"/refs/work/{wid}/purge", fields={}, back=back,
                            summary=f"删除《{w['title'] or w['external_id']}》的原文:共 {len(live)} 个文件("
                                    + "、".join(f"{k} {n} 个" for k, n in kinds.items()) + ")。指纹保留(查重照样认),"
                                    "作品不拉黑、不改关注。删除后不可撤销。",
                            hint="删掉的是原文文件(章节、剧评、录屏、转写、关键帧等),内容行与指纹留着。要再看只能重新抓取 / 重录。")
    for e in retention.purge_work(r.refs, r.refs_root, wid)[1]:
        r.refs_fail(f"删除原文:{e}")
    return RedirectResponse(back, status_code=303)


@router.post("/refs/work/{wid}/{action}")
def work_action(request: Request, wid: int, action: str):
    r = _refs(request)
    w = works.get(r.refs, wid)
    if action == "follow":
        works.set_flag(r.refs, wid, followed=True)
    elif action == "unfollow":
        works.set_flag(r.refs, wid, followed=False)
    elif action == "block":
        works.set_flag(r.refs, wid, blocked=True)
        for e in retention.purge(r.refs, r.refs_root)[1]:
            r.refs_fail(f"清理:{e}")
    elif action == "fetch":
        why = _fetch_refusal(w["source"])
        if why:
            raise ValueError(why)
        jobs.enqueue(r.refs, w["source"], "content", work_id=wid, day=clock.today())
    elif action == "breakdown":                 # 「拆解这部」:人点的(auto = 0),不受每日上限;推进由 Runtime 做
        why = breakdown.progress(r.refs, wid)["blocked"]
        if why:
            raise ValueError(f"《{w['title'] or w['external_id']}》拆不了:{why}")
        breakdown.enqueue(r.refs, wid)
    else:
        raise KeyError(action)
    return RedirectResponse(request.headers.get("referer") or f"/refs/work?id={wid}", status_code=303)


TRANSCRIBE_STATE = {"waiting": "等 GPU 空闲", "running": "转写中", "done": "已转写", "failed": "失败"}


def _job_tstate(refs, rec: dict) -> str:
    """还没有转写行时:转写任务的状态(排队 / 失败原因 / 没排)。"""
    j = refs.conn.execute("SELECT state, error FROM ref_job WHERE kind = 'transcribe' AND params_json = ? ORDER BY id DESC LIMIT 1",
                          (json.dumps({"recording_id": rec["id"]}, ensure_ascii=False, sort_keys=True),)).fetchone()
    if j is not None:
        return TRANSCRIBE_STATE[j["state"]] + (f":{j['error']}" if j["error"] else "")
    return "录屏不收,不转写" if rec["verdict"] != "ok" else "没排转写"


def _episode_row(refs, root: Path, rec: dict) -> dict:
    """录屏面板一集一行:录屏行 + 第 3 秒关键帧 + 转写(结果或任务状态)。"""
    kf = refs.conn.execute("SELECT id FROM ref_content WHERE kind = 'keyframe' AND origin = ? AND purged_at = ''"
                           " AND json_extract(meta_json, '$.sec') = 3 ORDER BY id DESC LIMIT 1", (f"录屏 #{rec['id']}",)).fetchone()
    tr = refs.conn.execute("SELECT * FROM ref_content WHERE kind = 'transcript' AND origin = ? ORDER BY id DESC LIMIT 1",
                           (f"whisper 转写录屏 #{rec['id']}",)).fetchone()
    head = ""
    if tr is not None:
        p = Path(root) / tr["path"]
        if not tr["purged_at"] and p.is_file():
            head = json.loads(p.read_text(encoding="utf-8"))["text"][:60]
        tstate = ("已转写" if tr["verdict"] == "ok" else f"不收:{tr['reason']}") + ("(已到期清理)" if tr["purged_at"] else "")
    else:
        tstate = _job_tstate(refs, rec)
    return {"rec": rec, "meta": json.loads(rec["meta_json"] or "{}"), "kf": kf[0] if kf else None,
            "tstate": tstate, "head": head}


@router.get("/refs/record")
def record(request: Request):
    r = rt(request)
    if r.refs is None:
        return _off(request, "refs_record")
    latest: dict[tuple[int, int], dict] = {}          # 每部每集只看最新一次录屏(重录前的留在作品详情里)
    for x in r.refs.conn.execute("SELECT * FROM ref_content WHERE kind = 'recording' ORDER BY id"):
        latest[(x["work_id"], x["unit"])] = dict(x)
    groups: dict[int, list[dict]] = {}
    for (wid, _), rec in sorted(latest.items()):
        groups.setdefault(wid, []).append(_episode_row(r.refs, r.refs_root, rec))
    shelf = [{"w": works.get(r.refs, wid), "eps": eps} for wid, eps in groups.items()]
    queue = [{"j": dict(x), "w": works.get(r.refs, x["work_id"])}
             for x in r.refs.conn.execute("SELECT * FROM ref_job WHERE kind = 'record' ORDER BY id DESC LIMIT 20")]
    return render(request, "refs_record.html", "refs_record", shelf=shelf, queue=queue, login=login_status(r),
                  login_run=r.refs_login, browser=r.refs_browser, web_episodes=WEB_EPISODES)


@router.get("/refs/record/view")
def record_view(request: Request, id: int, t: float | None = None):
    """每集详情:播放器 + 关键帧整排 + 完整转写(逐句可点跳转)。只认录屏行。`t` = 打开即跳到第几秒(拆解卡的引用链过来)。"""
    r = rt(request)
    if r.refs is None:
        return _off(request, "refs_record")
    row = r.refs.conn.execute("SELECT * FROM ref_content WHERE id = ?", (id,)).fetchone()
    if row is None or row["kind"] != "recording":
        raise KeyError(f"没有这条录屏:{id}")
    rec = dict(row)
    meta = json.loads(rec["meta_json"] or "{}")
    w = works.get(r.refs, rec["work_id"])
    ctx = {"rec": rec, "meta": meta, "w": w, "kf_head": [], "kf_rest": [], "sentences": [], "tr_error": None, "tstate": None,
           "t": t}
    if not rec["purged_at"] and rec["path"]:
        prefix = rec["path"].removesuffix(".webm") + "-kf/"
        kfs = sorted(({"id": k["id"], "sec": json.loads(k["meta_json"] or "{}").get("sec", 0)}
                      for k in r.refs.conn.execute("SELECT id, meta_json FROM ref_content WHERE kind = 'keyframe' AND purged_at = ''"
                                                   " AND substr(path, 1, ?) = ?", (len(prefix), prefix))),
                     key=lambda k: k["sec"])
        ctx["kf_head"] = [k for k in kfs if k["sec"] <= 3]
        ctx["kf_rest"] = [k for k in kfs if k["sec"] > 3]
        tr = r.refs.conn.execute("SELECT * FROM ref_content WHERE kind = 'transcript' AND path = ? ORDER BY id DESC LIMIT 1",
                                 (Path(rec["path"]).with_suffix(".transcript.json").as_posix(),)).fetchone()
        if tr is None:
            ctx["tstate"] = _job_tstate(r.refs, rec)
        elif tr["purged_at"]:
            ctx["tr_error"] = "转写文件已到期清理"
        else:
            try:
                data = json.loads((Path(r.refs_root) / tr["path"]).read_text(encoding="utf-8"))
                ctx["sentences"] = transcript.sentences(data["chars"])
            except Exception as e:
                ctx["tr_error"] = f"{type(e).__name__}: {e}"
    return render(request, "refs_record_view.html", "refs_record", **ctx)


@router.post("/refs/content/{cid}/transcribe")
def retranscribe(request: Request, cid: int):
    """录屏面板「重新转写」:只给收了、还没清理的录屏排;等着 / 在跑的不重复排,跑完(成功或失败)可以再排。"""
    r = _refs(request)
    row = r.refs.conn.execute("SELECT * FROM ref_content WHERE id = ?", (cid,)).fetchone()
    if row is None:
        raise KeyError(f"没有这条参考库内容:{cid}")
    if row["kind"] != "recording":
        raise ValueError(f"#{cid} 不是录屏,不能转写")
    if row["verdict"] != "ok":
        raise ValueError(f"#{cid} 这集录屏不收({row['reason']}),不转写")
    if row["purged_at"]:
        raise ValueError(f"#{cid} 这集录屏已到期清理,转不了 —— 先重录")
    w = works.get(r.refs, row["work_id"])
    jobs.enqueue(r.refs, w["source"], "transcribe", day=clock.today(), work_id=w["id"], params={"recording_id": cid},
                 dedupe_states=LIVE)
    return RedirectResponse("/refs/record", status_code=303)


@router.post("/refs/login")
def login(request: Request):
    r = _refs(request)
    r.start_login()
    return RedirectResponse("/refs/record", status_code=303)


MEDIA = {".jpg": "image/jpeg", ".webm": "video/webm"}


@router.get("/refs/file/{cid}")
def ref_file(request: Request, cid: int):
    """参考库文件只读:只给没清理的关键帧 / 录屏,解析后的路径必须在 refs_root 里;其他一律 404(参考库文件不经 /media)。"""
    r = rt(request)
    row = r.refs.conn.execute("SELECT kind, path, purged_at FROM ref_content WHERE id = ?", (cid,)).fetchone() if r.refs else None
    if row is None or row["kind"] not in ("keyframe", "recording") or row["purged_at"] or not row["path"]:
        raise KeyError(f"没有这个参考库文件:{cid}")
    root = Path(r.refs_root).resolve()
    p = (root / row["path"]).resolve()
    if root not in p.parents or not p.is_file() or p.suffix.lower() not in MEDIA:
        raise KeyError(f"没有这个参考库文件:{cid}")
    return FileResponse(p, media_type=MEDIA[p.suffix.lower()])


# ---------- 拆解卡(1c):列表、审阅页、创作视图预览、批准 / 驳回 ----------

UNIT_STATES = (breakdown.APPROVED, breakdown.PROPOSED, breakdown.RUNNING, breakdown.MISSING, breakdown.FAILED)


def _card_row(refs, wid: int) -> dict:
    """一部作品的拆解状态:单元进度(按状态计数)、卡运行状态、已批准的最新版、最近一条拆解任务。"""
    p = breakdown.progress(refs, wid)
    n = Counter(u["state"] for u in p["units"])
    card = refs.conn.execute("SELECT version, coverage FROM ref_card WHERE work_id = ? AND state = 'approved'", (wid,)).fetchone()
    job = refs.conn.execute("SELECT * FROM ref_job WHERE kind = 'breakdown' AND work_id = ? ORDER BY id DESC LIMIT 1",
                            (wid,)).fetchone()
    return {"w": works.get(refs, wid), "p": p, "counts": [(s, n[s]) for s in UNIT_STATES if n[s]],
            "card": dict(card) if card else None, "job": dict(job) if job else None}


@router.get("/refs/cards")
def cards(request: Request):
    r = rt(request)
    if r.refs is None:
        return _off(request, "refs_cards")
    ids = [x[0] for x in r.refs.conn.execute(
        "SELECT id FROM ref_work WHERE id IN (SELECT work_id FROM ref_content WHERE kind IN ('chapter', 'transcript')"
        "                                     AND verdict = 'ok' AND purged_at = '')"
        " OR id IN (SELECT work_id FROM ref_card) OR id IN (SELECT work_id FROM ref_job WHERE kind = 'breakdown')"
        " OR id IN (SELECT target_id FROM llm_run WHERE task = 'ref_card') ORDER BY id DESC")]
    return render(request, "refs_cards.html", "refs_cards", rows=[_card_row(r.refs, i) for i in ids])


def _recording_of(refs, row) -> int | None:
    """转写对应的录屏(录屏 `x.webm` ↔ 转写 `x.transcript.json`);没有 / 已清理返回 None。"""
    if row["kind"] != "transcript":
        return None
    rec = refs.conn.execute("SELECT id FROM ref_content WHERE kind = 'recording' AND path = ? AND purged_at = ''"
                            " ORDER BY id DESC LIMIT 1", (row["path"].removesuffix(".transcript.json") + ".webm",)).fetchone()
    return rec[0] if rec else None


def _keyframe_at(refs, row, sec) -> int | None:
    prefix = row["path"].removesuffix(".transcript.json") + "-kf/"
    kf = refs.conn.execute("SELECT id FROM ref_content WHERE kind = 'keyframe' AND purged_at = '' AND substr(path, 1, ?) = ?"
                           " AND json_extract(meta_json, '$.sec') = ? ORDER BY id DESC LIMIT 1", (len(prefix), prefix, sec)).fetchone()
    return kf[0] if kf else None


def _unit_rows(refs, root: Path, units: list[dict]) -> list[dict]:
    """逐单元表:每单元运行的产物(闭集字段、摘要、引用、出场)+ 引用在第几秒(转写)、录屏链接、画面钩子那一帧。"""
    out = []
    for u in units:
        row = refs.conn.execute("SELECT * FROM ref_content WHERE id = ?", (u["content_id"],)).fetchone()
        a = runs.output(runs.get(refs, u["run_id"]))
        at, err = None, ""
        if row["verdict"] == "ok" and not row["purged_at"]:
            try:                                 # 文件缺失等:如实写在这一行,页面不 500(审查 M8)
                at = evidence.check_quote(evidence.unit_text(refs, root, row), a["quote"])[2]
            except Exception as e:
                err = f"读不出原文:{type(e).__name__}: {e}"
        out.append({"err": err, "unit": u["unit"], "content_id": u["content_id"], "run_id": u["run_id"], "kind": row["kind"],
                    "purged": bool(row["purged_at"]), "a": a, "at": at, "rec": _recording_of(refs, row),
                    "kf": _keyframe_at(refs, row, a["visual_sec"]) if "visual_sec" in a else None})
    return out


def _recs(refs, review: dict) -> dict[int, int]:
    """单元内容号 → 录屏号(剧情类假设的引用链到录屏详情 `&t=秒`)。"""
    out = {}
    for u in review["units"]:
        row = refs.conn.execute("SELECT * FROM ref_content WHERE id = ?", (u["content_id"],)).fetchone()
        rec = _recording_of(refs, row)
        if rec is not None:
            out[u["content_id"]] = rec
    return out


NO_CHOICE = {"adopt": [], "reject": []}


def _pending(refs, root: Path, row) -> dict:
    """待审的卡运行:按现在的数据重建上下文(与批准同一套 prepare),审阅视图由 `ref_card.review_of` 出(代码标注都在里面)。"""
    out = {"run": dict(row), "review": None, "error": None, "stale": False, "units": [], "recs": {}, "heat_text": ""}
    try:
        p = service.prepare(refs, "ref_card", row["target_id"], runs.params(row), root)
    except Exception as e:                      # 前提不满足、文件缺失(OSError)都如实写出来,不 500(审查 M8)
        out["error"] = f"按现在的数据重建不了这张卡的上下文,批不了 —— 重新拆解:{type(e).__name__}: {e}"
        return out
    review = rcard.review_of(refs, row["target_id"], runs.output(row), p.ctx, NO_CHOICE)
    out.update(review=review, stale=p.sha != row["input_sha"], units=_unit_rows(refs, root, review["units"]),
               recs=_recs(refs, review), heat_text=rcard._heat_text(review["heat"]))
    return out


@router.get("/refs/card")
def card(request: Request, work: int):
    r = rt(request)
    if r.refs is None:
        return _off(request, "refs_cards")
    w = works.get(r.refs, work)
    row = r.refs.conn.execute("SELECT * FROM llm_run WHERE task = 'ref_card' AND target_type = 'ref_work' AND target_id = ?"
                              " AND state = 'proposed' ORDER BY id DESC LIMIT 1", (work,)).fetchone()
    pending = _pending(r.refs, r.refs_root, row) if row is not None else None
    cur = r.refs.conn.execute("SELECT * FROM ref_card WHERE work_id = ? AND state = 'approved'", (work,)).fetchone()
    approved = None
    if cur is not None:
        review = json.loads(cur["review_json"])
        dec = json.loads(cur["decision_json"])
        stance = {**{i: "adopt" for i in dec["adopt"]}, **{i: "reject" for i in dec["reject"]}}
        approved = {"card": dict(cur), "review": review, "view": json.loads(cur["creation_json"]), "stance": stance,
                    "decision": dec, "units": _unit_rows(r.refs, r.refs_root, review["units"]), "recs": _recs(r.refs, review),
                    "heat_text": rcard._heat_text(review["heat"])}
    history = [dict(x) for x in r.refs.conn.execute(
        "SELECT id, run_id, version, state, creation_sha, coverage, created_at FROM ref_card WHERE work_id = ? ORDER BY version DESC",
        (work,))]
    return render(request, "refs_card.html", "refs_cards", w=w, bd=_card_row(r.refs, work), pending=pending,
                  approved=approved, history=history)


def _card_decision(form) -> dict:
    """审阅页上的选择 → decision(`ref_card.decision_problems` 再严格核一遍)。补充的「不可借鉴」一行一条,空行不算。"""
    stance, drop = {}, {"rt-": [], "rn-": []}
    for k, v in form.items():
        idx = k[4:] if k.startswith("hyp-") else ""
        if idx.isdigit():                       # 序号不是整数的键直接忽略,缺的表态由 decision_problems 报
            stance[int(idx)] = str(v)
        if k[:3] in drop and k[3:].isdigit():   # 「去掉」勾选:rt-<序号> 可借鉴,rn-<序号> 不可借鉴
            drop[k[:3]].append(int(k[3:]))
    return {"adopt": sorted(i for i, v in stance.items() if v == "adopt"),
            "reject": sorted(i for i, v in stance.items() if v == "reject"),
            "extra_not_borrowable": [s.strip() for s in str(form.get("extra_not_borrowable", "")).splitlines() if s.strip()],
            "reject_techniques": sorted(drop["rt-"]), "reject_not_borrowable": sorted(drop["rn-"]),
            "ack_fragments": form.get("ack_fragments") == "1"}


def _card_run(refs, run_id: int):
    row = runs.get(refs, run_id)                # 没有 → 404
    if row["task"] != "ref_card":
        raise ValueError(f"运行 {run_id} 是 {row['task']},这里只批整部汇总(拆解卡);单元标注随卡连带批准")
    return row


@router.post("/refs/card/preview")
async def card_preview(request: Request):
    """创作视图预览:按当前勾选由代码生成(换名、去引用、去驳回)并跑重叠闸门。只渲染,不写库、不批准。"""
    r = _refs(request)
    form = await request.form()
    raw = str(form.get("run", ""))
    if not raw.isdigit():
        raise ValueError(f"预览要带卡运行号,收到 {raw!r}")
    row = _card_run(r.refs, int(raw))
    decision = _card_decision(form)
    args = runs.output(row)
    p = service.prepare(r.refs, "ref_card", row["target_id"], runs.params(row), r.refs_root)
    review = rcard.review_of(r.refs, row["target_id"], args, p.ctx, decision)
    view, hits, err = None, [], None
    frags = rcard.fragments(review, decision, p.ctx)
    try:
        view, hits = rcard.preview(r.refs, review, decision, p.ctx)     # 与批准同一套:换名、高频专名、换名前后与连起来的重叠
    except ValueError as e:                     # 名字 / 专名没换掉:批准时同样会拒,这里如实写出来
        err = str(e)
    return TEMPLATES.TemplateResponse(request, "_refs_creation.html", {
        "v": view, "hits": hits, "err": err, "frags": frags, "ack": decision["ack_fragments"],
        "problems": rcard.decision_problems(args, decision),
        "adopted": ",".join(map(str, decision["adopt"])), "sha": None})


@router.post("/refs/card/{run_id}/approve")
async def card_approve(request: Request, run_id: int):
    """批准:带人的选择调 `service.approve`(连带批准单元运行、写卡、创作视图;重叠 / 换名残留 / 假产物都 ValueError → 400 说原因)。"""
    r = _refs(request)
    row = _card_run(r.refs, run_id)
    service.approve(r.refs, run_id, data_root=r.refs_root, decision=_card_decision(await request.form()))
    return RedirectResponse(f"/refs/card?work={row['target_id']}", status_code=303)


@router.post("/refs/unit/{cid}/rebreak")
def unit_rebreak(request: Request, cid: int):
    """「重拆这一集 / 章」:排一条只拆这个单元的 breakdown(人点的,不受上限);只给拆解失败的单元。"""
    r = _refs(request)
    breakdown.enqueue_unit(r.refs, cid)
    wid = r.refs.conn.execute("SELECT work_id FROM ref_content WHERE id = ?", (cid,)).fetchone()[0]
    return RedirectResponse(f"/refs/card?work={wid}", status_code=303)


@router.post("/refs/card/{run_id}/reject")
def card_reject(request: Request, run_id: int):
    r = _refs(request)
    row = _card_run(r.refs, run_id)
    runs.reject(r.refs, run_id)
    return RedirectResponse(f"/refs/card?work={row['target_id']}", status_code=303)
