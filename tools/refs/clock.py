# -*- coding: utf-8 -*-
"""参考库的「当天」一律按北京时间零点切换(spec 1.1 第 6 条):每日上限、熔断、补拉都用它。"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

BJ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today(at: str | None = None) -> str:
    """at 是带时区的 ISO 时间(默认现在),返回北京日期 YYYY-MM-DD。"""
    t = datetime.fromisoformat(at) if at else datetime.now(timezone.utc)
    return t.astimezone(BJ).date().isoformat()


def days_between(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def add_days(at: str, days: int) -> str:
    return (datetime.fromisoformat(at) + timedelta(days=days)).isoformat(timespec="seconds")
