# -*- coding: utf-8 -*-
import pytest

from tools.refs import rjournal, settings


@pytest.fixture
def src(refs):
    settings.ensure(refs)
    return refs


def test_all_sources_exist_in_manual_mode_without_defaults(src):
    rows = settings.all_sources(src)
    assert [r["key"] for r in rows] == list(settings.SOURCES)
    assert all(r["mode"] == "C" and r["retention_days"] is None and r["cap_fetch"] is None for r in rows)


def test_fetching_needs_retention_and_novels_need_chapters(src):
    assert settings.fetch_problems(settings.get(src, "hongguo")) == ["保留天数没填(★ 必填)—— 不填不让抓"]
    assert len(settings.fetch_problems(settings.get(src, "jjwxc"))) == 2


def test_mode_a_refused_until_every_star_field_is_filled(src, tmp_path):
    with pytest.raises(ValueError, match="每天自动抓几部"):
        settings.update(src, tmp_path, "hongguo", {"mode": "A", "retention_days": "30"})
    changed = settings.update(src, tmp_path, "hongguo",
                              {"mode": "A", "retention_days": "30", "cap_fetch": "20", "cap_breakdown": "0", "top_k": "10"})
    assert set(changed) == {"mode", "retention_days", "cap_fetch", "cap_breakdown", "top_k"}
    assert settings.get(src, "hongguo")["cap_breakdown"] == 0          # 0 = 这一项不自动做


def test_changes_are_logged_with_old_and_new(src, tmp_path):
    settings.update(src, tmp_path, "k17", {"retention_days": "30"})
    settings.update(src, tmp_path, "k17", {"retention_days": "60"})
    log = src.conn.execute("SELECT field, old, new FROM ref_setting_log WHERE source='k17' ORDER BY id").fetchall()
    assert [tuple(r) for r in log] == [("retention_days", None, "30"), ("retention_days", "30", "60")]
    assert [e["new"] for e in rjournal.read(tmp_path, source="k17", event="setting")] == ["60", "30"]


def test_unchanged_fields_are_not_logged(src, tmp_path):
    settings.update(src, tmp_path, "k17", {"retention_days": "30"})
    assert settings.update(src, tmp_path, "k17", {"retention_days": "30"}) == []


@pytest.mark.parametrize("field,value", [("cap_fetch", "-1"), ("retention_days", "0"), ("top_k", "abc"), ("mode", "D")])
def test_bad_values_refused(src, tmp_path, field, value):
    with pytest.raises(ValueError):
        settings.update(src, tmp_path, "jjwxc", {field: value})


def test_manual_source_stays_manual(src, tmp_path):
    with pytest.raises(ValueError, match="人工投递只能是 C"):
        settings.update(src, tmp_path, "manual", {"mode": "B"})


def test_trip_lasts_through_the_day_then_lifts(src, tmp_path):
    settings.trip(src, tmp_path, "zongheng", "412 风控", "2026-11-07")
    s = settings.get(src, "zongheng")
    assert settings.blocked_reason(s, "2026-11-07") == "今天熔断:412 风控"
    assert settings.blocked_reason(s, "2026-11-08") is None


def test_pause_needs_a_human_reset(src, tmp_path):
    settings.pause(src, tmp_path, "qimao", "robots 改成禁止")
    assert settings.blocked_reason(settings.get(src, "qimao"), "2099-01-01") == "已暂停:robots 改成禁止"
    settings.reset_state(src, tmp_path, "qimao")
    assert settings.blocked_reason(settings.get(src, "qimao"), "2099-01-01") is None


def test_retention_forever_is_set_logged_and_satisfies_the_star(tmp_path):
    from tools.refs.db import RefStore
    src = RefStore(":memory:")
    settings.ensure(src)
    assert settings.fetch_problems(settings.get(src, "hongguo"))
    changed = settings.update(src, tmp_path, "hongguo", {"retention_kind": "forever", "retention_days": "30"})
    s = settings.get(src, "hongguo")
    assert changed == ["retention_days"] and s["retention_forever"] == 1 and s["retention_days"] is None
    assert settings.fetch_problems(s) == []
    assert settings.update(src, tmp_path, "hongguo", {"retention_kind": "forever"}) == []
    log = [tuple(r) for r in src.conn.execute("SELECT field, old, new FROM ref_setting_log")]
    assert log == [("retention_days", None, "永久")]
    settings.update(src, tmp_path, "hongguo", {"retention_kind": "days", "retention_days": "30"})
    s = settings.get(src, "hongguo")
    assert s["retention_forever"] == 0 and s["retention_days"] == 30
    assert [tuple(r) for r in src.conn.execute("SELECT old, new FROM ref_setting_log ORDER BY id")][-1] == ("永久", "30")
    settings.update(src, tmp_path, "hongguo", {"retention_kind": "days", "retention_days": ""})
    s = settings.get(src, "hongguo")
    assert s["retention_forever"] == 0 and s["retention_days"] is None and settings.fetch_problems(s)


def test_due_dates():
    at = "2026-11-07T00:00:00+00:00"
    assert settings.due_dates({"retention_days": 10, "retention_forever": 0}, at) == (
        "2026-11-17T00:00:00+00:00", "2026-11-27T00:00:00+00:00")
    assert settings.due_dates({"retention_days": None, "retention_forever": 1}, at) == ("9999-12-31T00:00:00+00:00",) * 2
