# -*- coding: utf-8 -*-
"""番茄小说:robots 全放行,但正文、简介、甚至书名都有字体加密(调研 2026-11-07-五平台内容获取实测.md)。
**只收元数据**(编号、名次、字数、作者);不反解字体、不 OCR(规避技术措施,不做)。"""
from __future__ import annotations

import re

from tools.refs.sources.base import Adapter, RankItem, WorkMeta, check_ranks, field, json_after

BASE = "https://fanqienovel.com"
BOARDS = {"rank": "阅读榜"}


def obfuscated(text: str) -> bool:
    return bool(re.search(r"[-]", text))      # 私用区字符 = 字体加密替换掉的字


def _state(page: str) -> dict:
    return json_after(page, "window.__INITIAL_STATE__=")


def parse_rank(page: str) -> list[RankItem]:
    books = field(_state(page), "rank", "book_list")
    return check_ranks([RankItem(str(field(b, "bookId")), b.get("bookName") or str(b["bookId"]), i + 1, None,
                                 {"wordNumber": b.get("wordNumber"), "author": b.get("author", ""),
                                  "title_obfuscated": obfuscated(b.get("bookName", ""))})
                        for i, b in enumerate(books)])


def _meta(f, ext: str) -> WorkMeta:
    # 前提不满足(不是网页改版):ValueError,不计连续解析失败、不熔断
    raise ValueError("番茄只收榜单元数据,没有详情抓取")


ADAPTER = Adapter(key="fanqie", boards=BOARDS, content="none",
                  list_ranks=lambda f, board: parse_rank(f.get(f"{BASE}/rank")), fetch_meta=_meta,
                  match=lambda url: (m.group(1) if (m := re.search(r"fanqienovel\.com/page/(\d+)", url)) else None))
