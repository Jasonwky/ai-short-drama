# -*- coding: utf-8 -*-
from pathlib import Path

import pytest

from tools.refs.sources import qimao, zongheng
from tools.refs.sources.base import AdapterError

FX = Path(__file__).parent / "fixtures"
rd = lambda n: (FX / n).read_text(encoding="utf-8")


def test_zongheng_rank_and_first_chapter():
    items = zongheng.parse_rank(rd("zongheng_rank.html"))
    assert items[0].external_id == "1336976" and len({i.external_id for i in items}) == len(items) >= 10
    meta, unit = zongheng.parse_book(rd("zongheng_book.html"), "1336976")
    assert meta.title and "阅读" not in meta.title                 # 不带 <title> 的「最新章节……阅读」后缀
    assert meta.heat == 193529962 and len(meta.units) == 1 and meta.units[0].free
    assert len(unit.text) > 1500 and "<p>" not in unit.text and "\\u003C" not in unit.text


def test_qimao_rank_and_first_chapter():
    items = qimao.parse_rank(rd("qimao_rank.html"))
    assert items[0].external_id == "195958" and len(items) >= 10
    assert not any(i.title.isdigit() for i in items)               # 不是封面上的名次数字
    meta, unit = qimao.parse_book(rd("qimao_book.html"), "195958")
    assert meta.title and "阅读" not in meta.title and meta.heat is None and len(meta.units) == 1
    assert len(unit.text) > 1000 and unit.title.startswith("第1章")



def test_zongheng_rank_entry_without_title_is_an_adapter_error():
    with pytest.raises(AdapterError):
        zongheng.parse_rank('<a href="/detail/123"></a>')


def test_zongheng_broken_first_chapter_string_is_an_adapter_error():
    page = '<meta name="og:title" content="书"><script>firstChapterContent:"abc\\q"</script>'
    with pytest.raises(AdapterError):
        zongheng.parse_book(page, "1")


def test_qimao_rank_entry_without_title_is_an_adapter_error():
    with pytest.raises(AdapterError):
        qimao.parse_rank('<a href="/shuku/123/"></a>')


def test_qimao_book_with_chapter_but_no_title_is_an_adapter_error():
    page = ('<div class="qm-with-title-th"><span>第1章</span></div>'
            '<div class="qm-with-title-tb"><div class="article">正文</div></div>')
    with pytest.raises(AdapterError, match="书名"):
        qimao.parse_book(page, "1")
