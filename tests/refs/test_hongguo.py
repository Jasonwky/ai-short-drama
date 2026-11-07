# -*- coding: utf-8 -*-
import dataclasses
from pathlib import Path

import pytest

from tools.refs.sources import hongguo
from tools.refs.sources.base import AdapterError

FX = Path(__file__).parent / "fixtures"


# 两种交付形态:fn-args 直接给数据块(单引号属性)/ 只有 mergeLoaderData(双引号属性,数据在 routerDataFnArgs[0] 的 JSON 字符串里)
@pytest.mark.parametrize("name", ["hongguo_rank.html", "hongguo_rank_merge.html"])
def test_rank_page_gives_twenty_items_with_heat(name):
    items = hongguo.parse_rank((FX / name).read_text(encoding="utf-8"))
    assert len(items) == 20 and [i.rank for i in items] == list(range(1, 21))
    assert all(i.external_id.isdigit() and i.heat and i.heat > 1e4 for i in items)
    assert {"scoreText", "likeText", "episodeCount"} <= set(items[0].raw)


def test_detail_gives_meta_reviews_without_nicknames():
    meta, reviews = hongguo.parse_detail((FX / "hongguo_detail.html").read_text(encoding="utf-8"))
    assert meta.external_id == "7561390625552092222" and meta.heat == 46804547
    assert meta.stats["episodes"] == 84 and meta.stats["reviews_total"] == 142
    assert len(meta.units) == 5 and all(u.free for u in meta.units)       # 网页登录后前 5 集(1b 录屏用)
    assert meta.roles and all(not r.startswith("饰") for r in meta.roles)
    assert reviews and all(r.text for r in reviews)
    assert {f.name for f in dataclasses.fields(reviews[0])} == {"text", "rating", "likes", "at"}   # 没有昵称 / 头像


def test_sitemap_lists_ids():
    ids = hongguo.parse_sitemap((FX / "hongguo_sitemap.xml").read_text(encoding="utf-8"))
    assert len(ids) > 1000 and ids[0] == "7561390625552092222"


def test_page_without_data_is_an_adapter_error():
    with pytest.raises(AdapterError):
        hongguo.parse_rank("<html></html>")
    with pytest.raises(AdapterError):
        hongguo.parse_detail("<html></html>")


@pytest.mark.parametrize("url,ext", [("https://hongguoduanju.com/detail?series_id=123", "123"),
                                     ("https://hongguoduanju.com/player/456", "456"), ("https://x.com/1", None)])
def test_match(url, ext):
    assert hongguo.ADAPTER.match(url) == ext


def _rank_page(args: str) -> str:
    return f"<div data-fn-args='{args}'></div>"


@pytest.mark.parametrize("args", [
    '[0,"content",{"isSuccess":true,"rankList":[{"title":"a","rank":1}]}]',              # 缺 seriesId
    '[0,"content",{"isSuccess":true,"rankList":[{"seriesId":1,"rank":1}]}]',             # 缺 title
    '[0,"content",{"isSuccess":true,"rankList":[{"seriesId":1,"title":"a","rank":"x"}]}]',
    '[0,"content",{"isSuccess":true,"rankList":[{"seriesId":1,"title":"a"',              # 截断的 JSON
    '{"a":1}',
    '[0,[{"key":"content","routerDataFnName":"s","routerDataFnArgs":["\\"x\\""]}]]',      # merge 形态但不是数据块
])
def test_rank_block_with_missing_fields_is_an_adapter_error(args):
    with pytest.raises(AdapterError):
        hongguo.parse_rank(_rank_page(args))


@pytest.mark.parametrize("data", ['{"loaderData":{"detail_page":{"seriesDetail":{"series_name":"a"}}}}',
                                  '{"loaderData":{"detail_page":{}}}', "[]", "{bad"])
def test_detail_block_with_missing_fields_is_an_adapter_error(data):
    with pytest.raises(AdapterError):
        hongguo.parse_detail(f"<script>window._ROUTER_DATA = {data};</script>")
