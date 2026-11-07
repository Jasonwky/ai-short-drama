# -*- coding: utf-8 -*-
"""七猫中文网:书页 SSR 内嵌第 1 章明文;后面章节导 App / 会员,1a 只收第 1 章。没有可靠的热度数字,「涨得快」只看名次。"""
from __future__ import annotations

import html as htmllib
import re

from tools.refs.sources.base import Adapter, RankItem, Unit, UnitRef, WorkMeta, check_ranks, need

BASE = "http://www.qimao.com"
BOARDS = {"paihang": "大热榜"}


def _text(fragment: str) -> str:
    s = htmllib.unescape(re.sub(r"<[^>]+>", "", re.sub(r"</p>|<br\s*/?>", "\n", fragment)))
    return "\n".join(x.strip() for x in s.splitlines() if x.strip())


def parse_rank(page: str) -> list[RankItem]:
    ids = list(dict.fromkeys(re.findall(r"/shuku/(\d+)/", page)))
    items = []
    for i, bid in enumerate(ids):
        # 封面链接里的文字是名次数字,书名在 s-book-title 那个链接里
        m = (re.search(r'/shuku/' + re.escape(bid) + r'/"[^>]*class="s-book-title"[^>]*>([^<]+)<', page)
             or re.search(r'/shuku/' + re.escape(bid) + r'/"[^>]*>\s*(?:<[^>]+>\s*)*([^<\s][^<]*)<', page))
        need(m, f"榜单里 {bid} 没有书名 —— 网页可能改版了")
        items.append(RankItem(bid, m.group(1).strip(), i + 1, None, {}))
    return check_ranks(items)


def parse_book(page: str, ext: str) -> tuple[WorkMeta, Unit]:
    t = re.search(r'class="title clearfix"[^>]*>\s*<span class="txt"[^>]*>([^<]+)<', page)   # <title> 带「免费阅读」后缀
    title = htmllib.unescape(t.group(1)).strip() if t else ""
    need(title, "书页缺书名")
    # 书页有好几个 qm-with-title-th(简介、标签……),第 1 章那个后面紧跟正文 div.article
    m = re.search(r'class="qm-with-title-th"[^>]*>\s*<span[^>]*>([^<]+)</span>\s*</div>\s*'
                  r'<div class="qm-with-title-tb"[^>]*>\s*<div class="article[^"]*"[^>]*>(.*?)</div>', page, re.S)
    need(m, "书页里没有第 1 章")
    name = re.sub(r"\s+", " ", m.group(1)).strip()
    meta = WorkMeta(ext, title, "", f"{BASE}/shuku/{ext}/", [], "", None, {}, [UnitRef(1, name, f"{BASE}/shuku/{ext}/", True)])
    return meta, Unit(1, name, _text(m.group(2)), None)


ADAPTER = Adapter(
    key="qimao", boards=BOARDS, content="chapters",
    list_ranks=lambda f, board: parse_rank(f.get(f"{BASE}/paihang")),
    fetch_meta=lambda f, ext: parse_book(f.get(f"{BASE}/shuku/{ext}/"), ext)[0],
    fetch_unit=lambda f, meta, ref: parse_book(f.get(ref.url), meta.external_id)[1],
    match=lambda url: (m.group(1) if (m := re.search(r"qimao\.com/shuku/(\d+)", url)) else None))
