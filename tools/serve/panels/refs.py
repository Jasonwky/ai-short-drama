# -*- coding: utf-8 -*-
"""参考库面板(spec 2026-11-07-参考库-design.md 第 3 节,1a 的三个):来源设置 / 榜单与趋势 / 作品详情。
面板只排任务、改设置;抓取由 Runtime 的 refs-fetch 线程跑。没配参考库就如实显示「未配置」,不偷偷建库。"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from tools.refs import clock, heat, jobs, manual, retention, rjournal, settings, works
from tools.refs.sources import ADAPTERS
from tools.serve.panels.common import render, rt

router = APIRouter()


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


def todo(refs, today: str, errors: int = 0) -> list[str]:
    """待办:后台错误条数、被挡住的来源、B 模式今天的候选数(1b / 1c 再加登录过期、待审的卡)。"""
    out = [f"参考库后台有 {errors} 条错误(含清理失败),见上方"] if errors else []
    out += [f"{settings.SOURCES[s['key']]}:{why}" for s in settings.all_sources(refs)
           if (why := settings.blocked_reason(s, today))]
    n = refs.conn.execute("SELECT COUNT(*) FROM ref_work WHERE candidate_day = ? AND candidate_reason != ''", (today,)).fetchone()[0]
    if n:
        out.append(f"今天有 {n} 部候选等你挑")
    return out


@router.get("/refs/sources")
def sources(request: Request):
    r = rt(request)
    if r.refs is None:
        return _off(request, "refs_sources")
    today = clock.today()
    rows = []
    for s in settings.all_sources(r.refs):
        robots = rjournal.read(r.refs_root, source=s["key"], event="robots", limit=1)
        rows.append({"s": s, "label": settings.SOURCES[s["key"]], "blocked": settings.blocked_reason(s, today),
                     "fetch_problems": settings.fetch_problems(s), "auto_problems": settings.auto_problems(s),
                     "used": jobs.auto_content_today(r.refs, s["key"], today),
                     "boards": ADAPTERS[s["key"]].boards if s["key"] in ADAPTERS else {},
                     "robots": robots[0] if robots else None,
                     "log": rjournal.read(r.refs_root, source=s["key"], limit=20)})
    return render(request, "refs_sources.html", "refs_sources", rows=rows, fields=settings.INT_FIELDS,
                  todo=todo(r.refs, today, len(r.refs_errors)), refs_errors=r.refs_errors)


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
                  adapters=ADAPTERS, labels=settings.SOURCES, today=today, mode=settings.get(r.refs, source)["mode"] if a else "C")


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
                  label=settings.SOURCES[w["source"]], fetch_refusal=_fetch_refusal(w["source"]))


@router.post("/refs/work/{wid}/manual")      # 要在 /refs/work/{wid}/{action} 之前注册,否则被通配吃掉
def manual_text(request: Request, wid: int, title: str = Form(...), text: str = Form(...), origin: str = Form("")):
    r = _refs(request)
    manual.add_text(r.refs, r.refs_root, title=title, text=text, origin=origin, work_id=wid)
    return RedirectResponse(f"/refs/work?id={wid}", status_code=303)


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
    else:
        raise KeyError(action)
    return RedirectResponse(request.headers.get("referer") or f"/refs/work?id={wid}", status_code=303)
