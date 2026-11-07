# -*- coding: utf-8 -*-
from pathlib import Path

import pytest

from tools.refs.sources import k17
from tools.refs.sources.base import AdapterError

FX = Path(__file__).parent / "fixtures"
rd = lambda n: (FX / n).read_text(encoding="utf-8")


def test_rank_dedupes_books_in_page_order():
    items = k17.parse_rank(rd("k17_rank.html"))
    ids = [i.external_id for i in items]
    assert len(ids) == len(set(ids)) >= 20 and ids[0] == "3583700"


def test_book_heat_and_chapter_list():
    meta = k17.parse_book(rd("k17_book.html"), rd("k17_list.html"), "3583700")
    assert meta.heat and meta.heat > 1e6
    assert meta.units and meta.units[0].free and any(not u.free for u in meta.units)


def test_chapter_text_and_self_reported_words():
    u = k17.parse_chapter(rd("k17_chapter.html"), 2)
    assert u.self_words == 3307 and len(u.text) > 2000 and "<p>" not in u.text
    assert "17K" not in u.text                          # 首发声明与「作者说」不算正文



def test_book_with_title_but_no_chapters_is_an_adapter_error():
    with pytest.raises(AdapterError, match="0 个章节"):
        k17.parse_book("<h1>书名</h1>", "<div>目录</div>", "1")


def test_chapter_without_body_block_is_an_adapter_error():
    with pytest.raises(AdapterError, match="正文"):
        k17.parse_chapter('<div class="readAreaBox"><h1>第1章</h1>本章共10字</div>', 1)
