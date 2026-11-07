# -*- coding: utf-8 -*-
"""纵横中文网:书页 __NUXT__ 内嵌第 1 章明文;后面章节只给开头 + 导 App,1a 只收第 1 章。
注意:纵横用户协议明文禁止自动抓取(调研 2026-11-07-参考来源用户协议条款.md),用户 2026-11-07 决定风险自担。"""
from __future__ import annotations

import html as htmllib
import json
import re

from tools.refs.sources.base import Adapter, AdapterError, RankItem, Unit, UnitRef, WorkMeta, check_ranks, need

BASE = "http://www.zongheng.com"
BOARDS = {"rank": "排行榜"}


def _js_str(page: str, key: str) -> str | None:
    m = re.search(re.escape(key) + r':"((?:[^"\\]|\\.)*)"', page)
    if not m:
        return None
    try:
        return json.loads('"' + m.group(1) + '"')
    except ValueError as e:
        raise AdapterError(f"书页里 {key} 的字符串解析失败:{e}") from e


def _text(fragment: str) -> str:
    s = htmllib.unescape(re.sub(r"<[^>]+>", "", re.sub(r"</p>|<br\s*/?>", "\n", fragment)))
    return "\n".join(x.strip() for x in s.splitlines() if x.strip())


def _title(page: str) -> str:
    og = re.search(r'name="og:title" content="([^"]+)"', page)    # <title> 带「(作者)最新章节……」后缀
    if og:
        return htmllib.unescape(og.group(1)).strip()
    t = re.search(r"<title>([^<]+)</title>", page)
    return re.split(r"[_\-|]", t.group(1))[0].strip() if t else ""


def parse_rank(page: str) -> list[RankItem]:
    ids = list(dict.fromkeys(re.findall(r"/detail/(\d+)", page)))
    titles = {}
    for bid in ids:
        m = re.search(r'/detail/' + re.escape(bid) + r'[^>]*>\s*(?:<[^>]+>\s*)*([^<\s][^<]*)<', page)
        need(m, f"榜单里 {bid} 没有书名 —— 网页可能改版了")
        titles[bid] = m.group(1).strip()
    return check_ranks([RankItem(b, titles[b], i + 1, None, {}) for i, b in enumerate(ids)])


def parse_book(page: str, ext: str) -> tuple[WorkMeta, Unit]:
    name, content = _js_str(page, "firstChapterName"), _js_str(page, "firstChapterContent")
    need(content, "书页里没有第 1 章内容(firstChapterContent)")
    click = re.search(r"totalClick:(\d+)", page)
    title = _title(page)
    need(title, "书页缺书名")
    meta = WorkMeta(ext, title, "", f"{BASE}/detail/{ext}", [], "", int(click.group(1)) if click else None, {},
                    [UnitRef(1, name or "第1章", f"{BASE}/detail/{ext}", True)])
    return meta, Unit(1, name or "第1章", _text(content), None)


ADAPTER = Adapter(
    key="zongheng", boards=BOARDS, content="chapters",
    list_ranks=lambda f, board: parse_rank(f.get(f"{BASE}/rank")),
    fetch_meta=lambda f, ext: parse_book(f.get(f"{BASE}/detail/{ext}"), ext)[0],
    fetch_unit=lambda f, meta, ref: parse_book(f.get(ref.url), meta.external_id)[1],
    match=lambda url: (m.group(1) if (m := re.search(r"zongheng\.com/detail/(\d+)", url)) else None))
