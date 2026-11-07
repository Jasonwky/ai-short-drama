# -*- coding: utf-8 -*-
"""适配器统一契约(spec 1.1 第 1 条)。

解析函数是**纯函数**(HTML 进、数据类出),单测吃脱敏快照;取页函数只负责拼地址、调 Fetcher。
解析不出该有的东西 → AdapterError(网站返回了 200,是我们的适配器坏了,多半网页改版)—— 不当作「今天没有新作品」。
"""
from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Callable
from dataclasses import dataclass


class AdapterError(Exception):
    """网站返回了 200,但没解析出该有的东西。"""


def need(cond, message: str) -> None:
    if not cond:
        raise AdapterError(message)


def json_after(page: str, marker: str):
    """页面里 marker 后面紧跟的那个 JSON 值;找不到 / 解析不了 → AdapterError。"""
    i = page.find(marker)
    need(i >= 0, f"页面里没有 {marker.strip()} —— 网页可能改版了")
    try:
        value, _ = json.JSONDecoder().raw_decode(page, i + len(marker))
    except ValueError as e:
        raise AdapterError(f"{marker.strip()} 后面的数据解析失败:{e}") from e
    return value


def field(d, *keys):
    """逐层取 d[k1][k2]…;哪一层不是字典、缺键或值为 null → AdapterError(写明缺哪个键)。"""
    cur = d
    for n, k in enumerate(keys, 1):
        if not isinstance(cur, dict) or cur.get(k) is None:
            raise AdapterError(f"数据块缺键 {'.'.join(keys[:n])} —— 网页可能改版了")
        cur = cur[k]
    return cur


@dataclass(frozen=True)
class RankItem:
    external_id: str
    title: str
    rank: int
    heat: float | None
    raw: dict


@dataclass(frozen=True)
class UnitRef:
    number: int
    title: str
    url: str
    free: bool


@dataclass
class WorkMeta:
    external_id: str
    title: str
    author: str
    url: str
    tags: list[str]
    intro: str
    heat: float | None                       # 本平台详情页的总热度口径(只在同平台内比)
    stats: dict = dataclasses.field(default_factory=dict)
    units: list[UnitRef] = dataclasses.field(default_factory=list)
    roles: list[str] = dataclasses.field(default_factory=list)     # 角色名(1c 换名用);不存演员昵称


@dataclass(frozen=True)
class Unit:
    number: int
    title: str
    text: str
    self_words: int | None                   # 页面自报字数(有才填)


@dataclass(frozen=True)
class Review:
    text: str
    rating: int | None
    likes: float | None
    at: str


@dataclass(frozen=True)
class Adapter:
    key: str
    boards: dict[str, str]                                   # 榜单键 → 中文名
    content: str                                             # "chapters" / "reviews" / "none"
    list_ranks: Callable                                      # (fetcher, board) -> list[RankItem]
    fetch_meta: Callable                                      # (fetcher, external_id) -> WorkMeta
    fetch_unit: Callable | None = None                        # (fetcher, meta, UnitRef) -> Unit
    fetch_reviews: Callable | None = None                     # (fetcher, external_id) -> list[Review]
    match: Callable[[str], str | None] = lambda url: None    # 作品链接 → external_id
    sitemap: Callable | None = None                           # (fetcher) -> list[external_id]


_UNIT = {"万": 1e4, "亿": 1e8}


def num(text) -> float | None:
    """「8339万热度」「314.3万」「22,274,893,824」→ 数;没有数字返回 None。"""
    if text is None:
        return None
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*([万亿])?", str(text))
    if not m:
        return None
    v = float(m.group(1).replace(",", "")) * _UNIT.get(m.group(2) or "", 1)
    return int(v) if v == int(v) else v


def hanzi(text: str) -> int:
    return len(re.findall(r"[一-鿿]", text))


def check_ranks(items: list[RankItem]) -> list[RankItem]:
    need(items, "榜单解析出 0 条 —— 网页可能改版了")
    for it in items:
        need(it.external_id and it.title and it.rank > 0, f"榜单条目缺编号 / 标题 / 名次:{it}")
    ranks = [it.rank for it in items]
    need(len(set(ranks)) == len(ranks), f"榜单名次重复:{ranks}")
    return items
