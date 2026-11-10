# -*- coding: utf-8 -*-
"""冷门对照与跨作品趋势(spec 2026-11-07-参考库-design.md 2.2、2.6;用户 2026-11-08 定:只用库里已有标注的作品)。"""
import json

import pytest

from tools.refs import config, contrast, trends

GOOD = {"hook": "有", "reversal": "大", "emotion": "放", "paywall": "是"}
FLAT = {"hook": "无", "reversal": "无", "emotion": "压", "paywall": "否"}


def _work(refs, wid, source="hongguo", tags=("古风爱情", "逼婚")):
    with refs.write() as c:
        c.execute("INSERT OR IGNORE INTO ref_source (key) VALUES (?)", (source,))
        c.execute("INSERT INTO ref_work (id, source, external_id, title, tags_json, first_seen) VALUES (?, ?, ?, ?, ?, 't')",
                  (wid, source, str(wid), f"作品{wid}", json.dumps(list(tags), ensure_ascii=False)))
    return wid


def _units(refs, wid, fields_list, visual=True):
    with refs.write() as c:
        for n, f in enumerate(fields_list, 1):
            cid = c.execute("INSERT INTO ref_content (work_id, kind, unit, path, sha256, origin, verdict, fetched_at, due_at,"
                            " hard_due_at) VALUES (?, 'transcript', ?, 'p', 's', 'o', 'ok', 't', 't', 't')", (wid, n)).lastrowid
            vis = {"hook": f["hook"], "sec": 1.0} if visual else {}
            c.execute("INSERT INTO ref_unit (work_id, content_id, unit, run_id, fields_json, summary, quote, names_json,"
                      " visual_json, created_at) VALUES (?, ?, ?, 1, ?, 's', 'q', '[]', ?, 't')",
                      (wid, cid, n, json.dumps(f, ensure_ascii=False), json.dumps(vis, ensure_ascii=False)))


def _heat(refs, wid, day, *, heat=None, rank=None, board="detail"):
    with refs.write() as c:
        c.execute("INSERT INTO ref_heat (work_id, board, day, at, rank, heat) VALUES (?, ?, ?, 't', ?, ?)",
                  (wid, board, day, rank, heat))


def test_features_counts_closed_fields_and_pacing():
    rows = [{"unit": n, "fields_json": json.dumps(f), "visual_json": json.dumps(v)} for n, f, v in (
        (1, GOOD, {"hook": "有", "sec": 1.0}), (2, FLAT, {"hook": "无", "sec": 0.5}), (3, {**GOOD, "reversal": "小"}, {}))]
    f = contrast.features(rows)
    assert f["units"] == 3 and f["reversals"] == 2 and f["hook_rate"] == pytest.approx(2 / 3)
    assert f["press_share"] == pytest.approx(1 / 3) and f["release_share"] == pytest.approx(2 / 3)
    assert f["visual_hook_rate"] == 0.5
    assert f["pacing"]["reversal_gaps"] == [2] and f["pacing"]["paywall_episodes"] == [1, 3]
    flags = f["flags"]
    assert flags["前 10 个单元结尾都有钩子"] is False and flags["有大反转"] is True
    assert flags["每 5 个单元内都有反转(含开头、结尾两段)"] is True
    assert flags["「放」多于「压」"] is True and flags["开头画面有钩子"] is True
    chapters = [{"unit": 1, "fields_json": json.dumps(FLAT), "visual_json": "{}"}]
    flags = contrast.features(chapters)["flags"]
    assert flags["开头画面有钩子"] is None                                      # 小说没有画面钩子:不判
    assert flags["每 5 个单元内都有反转(含开头、结尾两段)"] is False


def test_groups_split_annotated_same_tag_works_by_latest_detail_heat_median(refs):
    for wid, h in ((1, 100), (2, 200), (3, 300), (4, 400)):
        _work(refs, wid)
        _units(refs, wid, [GOOD])
        _heat(refs, wid, "2026-11-07", heat=h)
    _heat(refs, 1, "2026-11-01", heat=9999)                       # 旧的一天不算,取最新一天
    _work(refs, 5); _units(refs, 5, [GOOD]); _heat(refs, 5, "2026-11-07", heat=None)     # 热度为空:不进分组
    _work(refs, 6); _heat(refs, 6, "2026-11-07", heat=50)                                 # 没标注:不进分组
    _work(refs, 7, tags=("都市", "总裁")); _units(refs, 7, [GOOD]); _heat(refs, 7, "2026-11-07", heat=1)   # 别的题材
    _work(refs, 8, source="jjwxc", tags=("古代言情",)); _units(refs, 8, [GOOD]); _heat(refs, 8, "2026-11-07", heat=1)
    _heat(refs, 2, "2026-11-08", heat=1000, rank=1, board="hot")   # 榜单热度不是详情热度
    assert contrast.groups(refs, "hongguo", "古装甜宠") == ([3, 4], [1, 2])


def test_qimao_has_no_heat_so_groups_go_by_rank_median(refs):
    assert "qimao" in config.CONTRAST["by_rank"]
    for wid, rank in ((1, 1), (2, 5), (3, 9)):
        _work(refs, wid, source="qimao", tags=("玄幻",))
        _units(refs, wid, [GOOD], visual=False)
        _heat(refs, wid, "2026-11-07", rank=rank, board="hot")
        _heat(refs, wid, "2026-11-07", rank=rank + 20, board="new")      # 同一天取最好名次
    _heat(refs, 3, "2026-11-01", rank=1, board="hot")                    # 旧的一天不算
    assert contrast.groups(refs, "qimao", "玄幻") == ([1, 2], [3])


def test_table_says_no_contrast_without_cold_samples_or_group(refs):
    _work(refs, 1); _units(refs, 1, [GOOD]); _heat(refs, 1, "2026-11-07", heat=100)
    assert contrast.table(refs, 1) == {"rows": [], "note": contrast.NO_COLD}
    assert "无对照" in contrast.NO_COLD and "2026-11-08" in contrast.NO_COLD
    _work(refs, 2, tags=("逼婚",)); _units(refs, 2, [GOOD])
    assert contrast.table(refs, 2) == {"rows": [], "note": contrast.NO_GROUP}


def test_table_rows_count_hot_and_cold_with_distinctness(refs):
    for wid, h, fields in ((1, 400, [GOOD, GOOD]), (2, 300, [GOOD, FLAT]), (3, 200, [FLAT, {**FLAT, "emotion": "放"}]),
                           (4, 100, [FLAT, FLAT])):
        _work(refs, wid)
        _units(refs, wid, fields)
        _heat(refs, wid, "2026-11-07", heat=h)
    t = contrast.table(refs, 3)
    rows = {r["feature"]: r for r in t["rows"]}
    assert rows["有大反转"] == {"feature": "有大反转", "hot": "2/2", "cold": "0/2", "distinct": "高"}
    assert rows["开头画面有钩子"]["hot"] == "2/2" and rows["开头画面有钩子"]["cold"] == "0/2"
    assert rows["前 10 个单元结尾都有钩子"] == {"feature": "前 10 个单元结尾都有钩子", "hot": "1/2", "cold": "0/2", "distinct": "高"}
    assert rows["「放」多于「压」"]["distinct"] == "高"
    assert "热门 2 部、冷门 2 部" in t["note"] and "假设不对照" in t["note"]


def test_distinct_levels_follow_config():
    assert config.CONTRAST["high"] == 0.30 and config.CONTRAST["mid"] == 0.15
    assert contrast._distinct(0.8, 0.5) == "高"
    assert contrast._distinct(0.8, 0.55) == "中"
    assert contrast._distinct(0.8, 0.65) == "中"
    assert contrast._distinct(0.8, 0.70) == "低"
    assert contrast._distinct(0.7, 0.7) == "低"


def test_latest_units_takes_the_newest_row_per_content(refs):
    _work(refs, 1)
    _units(refs, 1, [FLAT])
    with refs.write() as c:                       # 同一内容重拆:追加一行
        c.execute("INSERT INTO ref_unit (work_id, content_id, unit, run_id, fields_json, summary, quote, created_at)"
                  " SELECT work_id, content_id, unit, 2, ?, 's', 'q', 't' FROM ref_unit", (json.dumps(GOOD),))
    rows = contrast.latest_units(refs, 1)
    assert len(rows) == 1 and rows[0]["run_id"] == 2


def _card(refs, wid, version, month, review, state="approved"):
    with refs.write() as c:
        c.execute("INSERT INTO ref_card (work_id, run_id, version, review_json, creation_json, creation_sha, coverage, state,"
                  " decision_json, created_at) VALUES (?, 1, ?, ?, '{}', 'x', 'c', ?, '{}', ?)",
                  (wid, version, json.dumps(review, ensure_ascii=False), state, f"{month}-05T10:00:00"))


def test_trends_count_approved_cards_by_month_and_source(refs):
    _work(refs, 1); _work(refs, 2); _work(refs, 3, source="qimao")
    a = {"genre": "古装甜宠", "payoff": "打脸", "conflict": "身份", "hook_type": "悬念"}
    b = {**a, "genre": "重生复仇"}
    _card(refs, 1, 1, "2026-10", b, state="superseded")          # 被取代的不数
    _card(refs, 1, 2, "2026-11", a)
    _card(refs, 2, 1, "2026-11", b)
    _card(refs, 3, 1, "2026-10", a)
    got = trends.by_month(refs)
    assert [(m["month"], m["n"]) for m in got] == [("2026-10", 1), ("2026-11", 2)]
    assert got[1]["genre"] == {"古装甜宠": 1, "重生复仇": 1} and got[1]["payoff"] == {"打脸": 2}
    got = trends.by_month(refs, "hongguo")
    assert [(m["month"], m["n"]) for m in got] == [("2026-11", 2)]
    assert trends.by_month(refs, "jjwxc") == []
