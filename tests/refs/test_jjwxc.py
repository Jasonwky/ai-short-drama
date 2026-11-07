# -*- coding: utf-8 -*-
from pathlib import Path

import pytest

from tools.refs.sources import jjwxc
from tools.refs.sources.base import AdapterError

FX = Path(__file__).parent / "fixtures"


def rd(name):
    return (FX / name).read_text(encoding="utf-8")


def test_rank():
    items = jjwxc.parse_rank(rd("jjwxc_rank.html"))
    assert len(items) >= 100 and items[0].rank == 1
    assert all(i.external_id.isdigit() and i.heat and i.heat > 1e6 for i in items[:20])


def test_book_lists_chapters_and_marks_vip():
    meta = jjwxc.parse_book(rd("jjwxc_book.html"), "6771725")
    assert meta.title and meta.heat and meta.heat > 1e9
    assert meta.units[0].number == 1 and meta.units[0].free
    assert not meta.units[-1].free                      # 最新章是 VIP
    assert [u.number for u in meta.units] == sorted(u.number for u in meta.units)


def test_chapter_body_without_page_chrome():
    u = jjwxc.parse_chapter(rd("jjwxc_chapter.html"), 1)
    assert u.number == 1 and u.title and len(u.text) > 3000
    assert "添加书签" not in u.text and "推荐给朋友" not in u.text and "<" not in u.text



def test_chapter_body_without_closing_div_is_an_adapter_error():
    page = '<div class="noveltext"><h2>第1章</h2><div id="paragraph_comment_content">正文没有结尾'
    with pytest.raises(AdapterError):
        jjwxc.parse_chapter(page, 1)


def test_book_with_chapters_but_no_title_is_an_adapter_error():
    page = '<table><tr itemprop="chapter"><td>1</td><td>开端</td></tr></table>'
    with pytest.raises(AdapterError, match="书名"):
        jjwxc.parse_book(page, "1")
