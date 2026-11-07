# -*- coding: utf-8 -*-
import pytest

from tools.refs import heat, settings, works
from tools.refs.sources.base import RankItem, WorkMeta


@pytest.fixture
def r(refs):
    settings.ensure(refs)
    return refs


def test_upsert_is_idempotent_and_keeps_human_flags(r):
    wid = works.upsert(r, "jjwxc", "100", title="甲")
    works.set_flag(r, wid, followed=True)
    assert works.upsert(r, "jjwxc", "100", title="甲乙") == wid
    w = works.get(r, wid)
    assert w["title"] == "甲乙" and w["followed"] == 1


def test_apply_meta_fills_fields(r):
    wid = works.upsert(r, "hongguo", "7")
    works.apply_meta(r, wid, WorkMeta("7", "掌上珠", "", "u", ["古风"], "简介", 4.6e7, {}, [], ["沈明珠"]))
    w = works.get(r, wid)
    assert w["title"] == "掌上珠" and works.tags(w) == ["古风"] and works.roles(w) == ["沈明珠"]


def test_heat_once_per_board_per_day(r):
    wid = works.upsert(r, "jjwxc", "1", title="甲")
    assert heat.record(r, wid, "b", "2026-11-07", 3, 100.0, {}) is True
    assert heat.record(r, wid, "b", "2026-11-07", 2, 120.0, {}) is False      # 当天已记,不改
    assert [p["rank"] for p in heat.series(r, wid, "b")] == [3]


def test_series_marks_gaps(r):
    wid = works.upsert(r, "jjwxc", "1", title="甲")
    heat.record(r, wid, "b", "2026-11-01", 5, 10.0, {})
    heat.record(r, wid, "b", "2026-11-04", 4, 12.0, {})
    s = heat.series(r, wid, "b")
    assert [p["day"] for p in s] == ["2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04"]
    assert [p["gap"] for p in s] == [False, True, True, False]


@pytest.mark.parametrize("y,t,why", [((10, 100.0), (9, 125.0), "热度涨 25%"), ((30, 100.0), (15, 101.0), "名次升 15 名"),
                                     (None, (5, None), "新上榜第 5 名"), ((10, 100.0), (9, 110.0), None)])
def test_rising(r, y, t, why):
    wid = works.upsert(r, "jjwxc", "1", title="甲")
    if y:
        heat.record(r, wid, "b", "2026-11-06", y[0], y[1], {})
    else:                                    # 「新上榜」要这个榜单之前有过数据:同榜另一部前一天在榜
        heat.record(r, works.upsert(r, "jjwxc", "2", title="乙"), "b", "2026-11-06", 1, 1.0, {})
    heat.record(r, wid, "b", "2026-11-07", t[0], t[1], {})
    got = heat.rising(r, wid, "b", "2026-11-07")
    assert (got is None) if why is None else (why in got)


def test_first_day_of_a_board_has_no_new_entries(r):
    wid = works.upsert(r, "jjwxc", "1", title="甲")
    heat.record(r, wid, "b", "2026-11-07", 5, None, {})
    heat.record(r, works.upsert(r, "k17", "2", title="乙"), "b", "2026-11-06", 1, 1.0, {})     # 别的来源的同名榜单不算
    heat.record(r, works.upsert(r, "jjwxc", "3", title="丙"), "c", "2026-11-06", 1, 1.0, {})   # 同来源别的榜单不算
    assert heat.rising(r, wid, "b", "2026-11-07") is None


def test_search_only_covers_seen_works(r):
    a = works.upsert(r, "jjwxc", "1", title="逃离玫瑰岛")
    works.apply_meta(r, a, WorkMeta("1", "逃离玫瑰岛", "木苏里", "u", ["纯爱"], "海岛求生", None, {}))
    works.upsert(r, "k17", "2", title="星痕之门")
    assert [w["id"] for w in works.search(r, "玫瑰")] == [a]
    assert [w["id"] for w in works.search(r, "求生")] == [a]
    assert works.search(r, "没见过的") == []


def test_add_by_link(r):
    wid = works.add_by_link(r, "https://www.jjwxc.net/onebook.php?novelid=6771725")
    assert works.get(r, wid)["external_id"] == "6771725" and works.get(r, wid)["followed"] == 1
    with pytest.raises(ValueError, match="认不出"):
        works.add_by_link(r, "https://example.com/book/1")
