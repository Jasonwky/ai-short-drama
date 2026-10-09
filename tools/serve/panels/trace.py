# -*- coding: utf-8 -*-
"""留痕:失败归类 · 时间线 · 原始流水(面板),从产物倒查,任务详情(设计 specs/2026-10-09-操作留痕查看-design.md)。

全部只读。库与日志里是 UTC,页面按北京时间显示、悬停看原始字符串;筛选框填北京时间,这里换回 UTC 再查。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from tools.serve import journal, trace, trace_views
from tools.serve.panels.common import TEMPLATES, render, rt

router = APIRouter()
BJ = timezone(timedelta(hours=8))
PREFIXES = {"": "全部", "host=": "worker(各云机)", "console ": "控制台", "workflows ": "工作流", "runtime ": "后台"}


def bj(utc_iso: str | None) -> str:
    if not utc_iso:
        return ""
    return datetime.fromisoformat(utc_iso).astimezone(BJ).strftime("%m-%d %H:%M:%S")


def _utc(local: str) -> str:
    """筛选框里的北京时间(`2026-10-09T12:00`)→ 与库里同格式的 UTC 字符串;空的原样。"""
    if not local:
        return ""
    return datetime.fromisoformat(local).replace(tzinfo=BJ).astimezone(timezone.utc).isoformat(timespec="seconds")


TEMPLATES.env.filters["bj"] = bj
TEMPLATES.env.globals["journal_links"] = journal.links


def _int(v: str | None) -> int | None:
    return int(v) if v and v.isdigit() else None


@router.get("/trace")
def page(request: Request):
    q = request.query_params
    runtime = rt(request)
    flt = trace.Filter(drama_id=_int(q.get("d")), episode_id=_int(q.get("ep")), host_id=_int(q.get("host")),
                       kinds=tuple(k for k in q.getlist("kind") if k in trace.KINDS), since=_utc(q.get("since", "")),
                       until=_utc(q.get("until", "")), failed_only=q.get("failed") == "1")
    page_no = _int(q.get("page")) or 1
    events, total = trace.timeline(runtime.store, flt, page=page_no)
    prefix = q.get("prefix", "")
    raw = journal.read(runtime.data_root, since=flt.since, until=flt.until,
                       prefixes=[prefix] if prefix in PREFIXES and prefix else None)
    hosts = runtime.store.conn.execute("SELECT id, label FROM host ORDER BY id").fetchall()
    return render(request, "trace.html", "trace", groups=trace.failures(runtime.store, flt), events=events,
                  total=total, page=page_no, pages=max(1, -(-total // trace.PER_PAGE)), q=q, flt=flt,
                  qs=urlencode([(k, v) for k, v in q.multi_items() if k != "page"]),
                  kinds=trace.KINDS, hosts=hosts, raw=raw, prefixes=PREFIXES, prefix=prefix)


@router.get("/trace/product")
def product(request: Request, path: str):
    runtime = rt(request)
    eid = trace_views.export_for_path(runtime.store, path)
    if eid is not None:                    # 成片:看导出时的快照,不重新追
        return RedirectResponse(f"/trace/export/{eid}", status_code=303)
    return render(request, "trace_product.html", "trace", tree=trace_views.product(runtime.store, runtime.data_root, path))


@router.get("/trace/export/{export_id}")
def export_page(request: Request, export_id: int):
    runtime = rt(request)
    return render(request, "trace_export.html", "trace", v=trace_views.export_view(runtime.store, runtime.data_root,
                                                                                    export_id))


@router.get("/task/{task_id}")
def task_page(request: Request, task_id: int):
    runtime = rt(request)
    return render(request, "task.html", "trace", v=trace_views.task_view(runtime.store, runtime.data_root, task_id))
