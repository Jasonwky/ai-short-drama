# -*- coding: utf-8 -*-
from tools.llm.premise import DIMS
from tools.refs import genres


def test_genres_cover_premise_genres_and_end_with_other():
    assert set(DIMS["genre"]) <= set(genres.GENRES)
    assert len(set(genres.GENRES)) == len(genres.GENRES)
    for g in ("玄幻", "仙侠", "科幻", "无限流", "末世", "宫斗宅斗", "种田", "校园青春", "豪门", "年代", "悬疑推理", "刑侦"):
        assert g in genres.GENRES
    assert genres.GENRES[-1] == "其他"


def test_every_genre_maps_to_a_premise_genre():
    assert set(genres.TO_PREMISE) == set(genres.GENRES)
    assert set(genres.TO_PREMISE.values()) <= set(DIMS["genre"])
    for g in DIMS["genre"]:                      # premise 自己的取值对应到自己
        assert genres.TO_PREMISE[g] == g


def test_tag_group_maps_platform_tags_or_returns_none():
    # 红果《掌上珠》的真实标签
    assert genres.tag_group("hongguo", ["爱情", "古风爱情", "日久生情", "逼婚", "古代"]) == "古装甜宠"
    assert genres.tag_group("jjwxc", ["重生", "古代"]) == "重生复仇"
    assert genres.tag_group("qimao", ["修仙", "重生"]) == "仙侠"      # 越具体的排在前面
    assert genres.tag_group("hongguo", ["爱情", "逼婚"]) is None      # 认不出不猜
    assert genres.tag_group("hongguo", []) is None
    for _, g in genres.TAG_RULES:
        assert g in genres.GENRES
