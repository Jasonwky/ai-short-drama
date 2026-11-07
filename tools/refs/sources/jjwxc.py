# -*- coding: utf-8 -*-
"""晋江文学城(调研 2026-11-07-小说平台免费章节实测.md):robots 允许,免费章明文,页面编码 GB18030。
只抓免费章(章节行没有 [VIP]);积分作热度(只在晋江内比)。"""
from __future__ import annotations

import html as htmllib
import re

from tools.refs.sources.base import Adapter, RankItem, Unit, UnitRef, WorkMeta, check_ranks, need, num

BASE = "https://www.jjwxc.net"
ENC = "gb18030"
BOARDS = {"topten7": "总分排行榜"}
CHROME = ("[添加书签]", "[推荐给朋友]", "[收藏此章节]", "[投诉]")


def text_of(fragment: str) -> str:
    s = re.sub(r"<br\s*/?>|</p>", "\n", fragment)
    s = htmllib.unescape(re.sub(r"<[^>]+>", "", s))
    for c in CHROME:
        s = s.replace(c, "")
    return "\n".join(x.strip() for x in s.splitlines() if x.strip())


def parse_rank(page: str) -> list[RankItem]:
    items = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        row = re.sub(r'rel="[^"]*"', "", row)          # 简介悬浮框在 rel 里,带 <br>,会挡住下面的 [^>]
        m = re.search(r'href="onebook\.php\?novelid=(\d+)"[^>]*?class="tooltip">([^<]+)</a>', row, re.S)
        if not m:
            continue
        plain = text_of(row)
        nums = [num(x) for x in re.findall(r"\d{1,3}(?:,\d{3})+", plain)]
        items.append(RankItem(m.group(1), m.group(2).strip(), len(items) + 1, max(nums) if nums else None, {}))
    return check_ranks(items)


def parse_book(page: str, ext: str) -> WorkMeta:
    t = re.search(r'<span itemprop="articleSection">([^<]*)</span>', page)
    need(t and t.group(1).strip(), "书页缺书名")
    genre = re.search(r'itemprop="genre">\s*([^<]+?)\s*</span>', page)
    score = re.search(r"文章积分[:：]\s*(?:<[^>]+>\s*)*([\d,]+)", page)
    intro = re.search(r'<div[^>]*id="novelintro"[^>]*>(.*?)</div>', page, re.S)
    author = re.search(r'itemprop="author"[^>]*>\s*(?:<[^>]+>\s*)*([^<\s][^<]*)', page)
    units = []
    for row in re.findall(r'<tr[^>]*itemprop="chapter[^"]*"[^>]*>(.*?)</tr>', page, re.S):
        plain = text_of(row)
        n = re.match(r"(\d+)\s+(\S+)", plain)
        if n:
            units.append(UnitRef(int(n.group(1)), n.group(2), f"{BASE}/onebook.php?novelid={ext}&chapterid={n.group(1)}",
                                 "[VIP]" not in plain and "vip" not in row.lower()))
    need(units, "书页解析出 0 个章节")
    return WorkMeta(ext, t.group(1).strip(), author.group(1).strip() if author else "", f"{BASE}/onebook.php?novelid={ext}",
                    genre.group(1).split("-") if genre else [], text_of(intro.group(1)) if intro else "",
                    num(score.group(1)) if score else None, {}, units)


def parse_chapter(page: str, number: int) -> Unit:
    i = page.find('class="noveltext')
    need(i >= 0, "章节页没有正文块")
    area = page[i:]
    j = area.find('id="paragraph_comment_content"')     # 正文本身;前面是书签 / 投诉 / 收藏浮层
    need(j >= 0, "章节页没有正文块")
    body = area[area.find(">", j) + 1:]
    end = body.find("</div>")
    need(end >= 0, "章节正文块没有结尾 —— 网页可能改版了")
    body = body[:end]
    h2 = re.search(r"<h2[^>]*>(.*?)</h2>", area, re.S)
    text = text_of(body)
    need(text, "章节正文为空")
    return Unit(number, text_of(h2.group(1)) if h2 else f"第{number}章", text, None)


ADAPTER = Adapter(
    key="jjwxc", boards=BOARDS, content="chapters",
    list_ranks=lambda f, board: parse_rank(f.get(f"{BASE}/topten.php?orderstr=7", encoding=ENC)),
    fetch_meta=lambda f, ext: parse_book(f.get(f"{BASE}/onebook.php?novelid={ext}", encoding=ENC), ext),
    fetch_unit=lambda f, meta, ref: parse_chapter(f.get(ref.url, encoding=ENC), ref.number),
    match=lambda url: (m.group(1) if (m := re.search(r"jjwxc\.net/onebook\.php\?novelid=(\d+)", url)) else None))
