# -*- coding: utf-8 -*-
"""17K 小说网(调研 2026-11-07-小说平台免费章节实测.md):robots 允许(禁 /azapp*、/ck),免费章明文,自报「本章共 N 字」。
https 主域实测连接超时,一律用 http。只抓目录里非 VIP 的章。"""
from __future__ import annotations

import html as htmllib
import re

from tools.refs.sources.base import Adapter, RankItem, Unit, UnitRef, WorkMeta, check_ranks, need, num

BASE = "http://www.17k.com"
BOARDS = {"top": "排行榜"}


def _text(fragment: str) -> str:
    s = htmllib.unescape(re.sub(r"<[^>]+>", "", re.sub(r"</p>|<br\s*/?>", "\n", fragment)))
    return "\n".join(x.strip() for x in s.splitlines() if x.strip())


def parse_rank(page: str) -> list[RankItem]:
    seen: dict[str, str] = {}
    for bid, title in re.findall(r'href="//www\.17k\.com/book/(\d+)\.html"[^>]*>([^<]*)<', page):
        if title.strip() and bid not in seen:
            seen[bid] = title.strip()
    return check_ranks([RankItem(b, t, i + 1, None, {}) for i, (b, t) in enumerate(seen.items())])


def parse_book(book: str, listing: str, ext: str) -> WorkMeta:
    title = re.search(r"<h1[^>]*>\s*(?:<a[^>]*>)?([^<]+)", book)
    need(title, "书页缺书名")
    reads = re.search(r'id="howmuchreadBook"[^>]*>(\d+)<', book)
    intro = re.search(r'<p class="intro"[^>]*>(.*?)</p>', book, re.S) or re.search(r'<dd[^>]*class="intro"[^>]*>(.*?)</dd>', book, re.S)
    units = []
    for i, (cid, attrs, span) in enumerate(re.findall(
            r'href="/chapter/' + re.escape(ext) + r'/(\d+)\.html"([^>]*)>\s*<span class="([^"]*)"', listing), 1):
        t = re.search(r'title="([^&"]*)', attrs)
        units.append(UnitRef(i, t.group(1).strip() if t else f"第{i}章", f"{BASE}/chapter/{ext}/{cid}.html", "vip" not in span))
    need(units, "目录解析出 0 个章节")
    return WorkMeta(ext, title.group(1).strip(), "", f"{BASE}/book/{ext}.html", [], _text(intro.group(1)) if intro else "",
                    num(reads.group(1)) if reads else None, {}, units)


def parse_chapter(page: str, number: int) -> Unit:
    i = page.find('class="readAreaBox')
    need(i >= 0, "章节页没有正文块")
    area = page[i:]
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", area, re.S)
    words = re.search(r"本章共\s*(\d+)\s*字", area)
    body = re.search(r'<div class="p">(.*?)<(?:/div|div)', area, re.S)     # 正文后紧跟「作者说」div
    need(body, "章节正文块为空")
    text = _text(re.sub(r'<p class="copy[^"]*">.*?</p>', "", body.group(1), flags=re.S))   # 去「首发 17K」那行
    return Unit(number, _text(h1.group(1)) if h1 else f"第{number}章", text, int(words.group(1)) if words else None)


ADAPTER = Adapter(
    key="k17", boards=BOARDS, content="chapters",
    list_ranks=lambda f, board: parse_rank(f.get(f"{BASE}/top/")),
    fetch_meta=lambda f, ext: parse_book(f.get(f"{BASE}/book/{ext}.html"), f.get(f"{BASE}/list/{ext}.html"), ext),
    fetch_unit=lambda f, meta, ref: parse_chapter(f.get(ref.url), ref.number),
    match=lambda url: (m.group(1) if (m := re.search(r"17k\.com/(?:book|list)/(\d+)\.html", url)) else None))
