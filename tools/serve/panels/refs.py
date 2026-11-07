# -*- coding: utf-8 -*-
"""参考库面板(spec 2026-11-07-参考库-design.md 第 3 节):来源设置 / 榜单与趋势 / 作品详情(1a)、录屏(1b)。
面板只排任务、改设置;抓取由 Runtime 的 refs-fetch 线程跑,录屏由 refs-record 线程跑(永远人点才排)。
没配参考库就如实显示「未配置」,不偷偷建库。"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, RedirectResponse

from tools.refs import clock, heat, jobs, loginstate, manual, retention, rjournal, settings, transcript, works
from tools.refs.sources import ADAPTERS
from tools.refs.sources.hongguo import WEB_EPISODES
from tools.serve.panels.common import confirm_page, render, rt

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
    """待办:后台错误条数、被挡住的来源、B 模式今天的候选数、红果没登录(1c 再加待审的卡)。"""
    out = [f"参考库后台有 {errors} 条错误(含清理失败),见上方"] if errors else []
    if login is not None and not login["ok"]:
        out.append(f"红果没登录 / 登录过期({login['at'][:16]} {login['text']})—— 在「录屏」面板点「打开浏览器扫码」")
    out += [f"{settings.SOURCES[s['key']]}:{why}" for s in settings.all_sources(refs)
           if (why := settings.blocked_reason(s, today))]
    n = refs.conn.execute("SELECT COUNT(*) FROM ref_work WHERE candidate_day = ? AND candidate_reason != ''"
                          " AND candidate_reason NOT LIKE '建议录屏%'", (today,)).fetchone()[0]      # 建议录屏只标不挑,不算候选
    if n:
        out.append(f"今天有 {n} 部候选等你挑")
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
    rows = []
    for s in settings.all_sources(r.refs):
        robots = rjournal.read(r.refs_root, source=s["key"], event="robots", limit=1)
        rows.append({"s": s, "label": settings.SOURCES[s["key"]], "blocked": settings.blocked_reason(s, today),
                     "fetch_problems": settings.fetch_problems(s), "auto_problems": settings.auto_problems(s),
                     "used": jobs.auto_content_today(r.refs, s["key"], today),
                     "boards": ADAPTERS[s["key"]].boards if s["key"] in ADAPTERS else {},
                     "robots": robots[0] if robots else None,
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
                  label=settings.SOURCES[w["source"]], fetch_refusal=_fetch_refusal(w["source"]), web_episodes=WEB_EPISODES,
                  today=clock.today(), live_n=len(retention.live_content(r.refs, id)))


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
def record_view(request: Request, id: int):
    """每集详情:播放器 + 关键帧整排 + 完整转写(逐句可点跳转)。只认录屏行。"""
    r = rt(request)
    if r.refs is None:
        return _off(request, "refs_record")
    row = r.refs.conn.execute("SELECT * FROM ref_content WHERE id = ?", (id,)).fetchone()
    if row is None or row["kind"] != "recording":
        raise KeyError(f"没有这条录屏:{id}")
    rec = dict(row)
    meta = json.loads(rec["meta_json"] or "{}")
    w = works.get(r.refs, rec["work_id"])
    ctx = {"rec": rec, "meta": meta, "w": w, "kf_head": [], "kf_rest": [], "sentences": [], "tr_error": None, "tstate": None}
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
