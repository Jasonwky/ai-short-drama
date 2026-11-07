# -*- coding: utf-8 -*-
import pytest

from tools.refs import heat, jobs, monitor, settings, works

DAY = "2026-11-07"


@pytest.fixture
def r(refs, tmp_path):
    settings.ensure(refs)
    return refs, tmp_path


def kinds(refs):
    return sorted((j["source"], j["kind"], j["auto"]) for j in jobs.recent(refs, limit=999))


def test_manual_mode_plans_nothing(r):
    refs, _ = r
    monitor.plan(refs, DAY)
    assert kinds(refs) == []


def test_b_mode_plans_every_board_once_a_day(r):
    refs, root = r
    settings.update(refs, root, "hongguo", {"mode": "B"})
    monitor.plan(refs, DAY)
    monitor.plan(refs, DAY)
    ranks = [j for j in jobs.recent(refs, limit=99) if j["kind"] == "ranks"]
    assert len(ranks) == 4
    assert any(j["kind"] == "sitemap" for j in jobs.recent(refs, limit=99))


def _ranked(refs, n, source="jjwxc", board="topten7"):
    ids = []
    for i in range(1, n + 1):
        wid = works.upsert(refs, source, str(i), title=f"书{i}")
        heat.record(refs, wid, board, DAY, i, 100.0, {})
        ids.append(wid)
    with refs.write() as c:
        c.execute("UPDATE ref_source SET last_ranks_day = ? WHERE key = ?", (DAY, source))
    return ids


def test_a_mode_picks_top_k_up_to_the_daily_cap(r):
    refs, root = r
    settings.update(refs, root, "jjwxc", {"mode": "A", "retention_days": "30", "chapters_n": "5", "cap_fetch": "2",
                                          "cap_breakdown": "0", "top_k": "5"})
    _ranked(refs, 8)
    monitor.plan(refs, DAY)
    auto = [j for j in jobs.recent(refs, limit=99) if j["kind"] == "content"]
    assert len(auto) == 2 and all(j["auto"] for j in auto)
    assert [works.get(refs, j["work_id"])["external_id"] for j in sorted(auto, key=lambda j: j["id"])] == ["1", "2"]


def test_raising_the_cap_takes_effect_the_same_day(r):
    refs, root = r
    settings.update(refs, root, "jjwxc", {"mode": "A", "retention_days": "30", "chapters_n": "5", "cap_fetch": "1",
                                          "cap_breakdown": "0", "top_k": "5"})
    _ranked(refs, 8)
    monitor.plan(refs, DAY)
    settings.update(refs, root, "jjwxc", {"cap_fetch": "3"})
    monitor.plan(refs, DAY)
    assert jobs.auto_content_today(refs, "jjwxc", DAY) == 3


def test_b_mode_marks_candidates_without_fetching(r):
    refs, root = r
    settings.update(refs, root, "jjwxc", {"mode": "B", "top_k": "2"})
    ids = _ranked(refs, 4)
    monitor.plan(refs, DAY)
    assert not [j for j in jobs.recent(refs, limit=99) if j["kind"] == "content"]
    assert works.get(refs, ids[0])["candidate_reason"] == "今天第 1 名(前 2 名)"
    assert works.get(refs, ids[3])["candidate_reason"] == ""


def test_blocked_source_plans_nothing(r):
    refs, root = r
    settings.update(refs, root, "k17", {"mode": "B"})
    settings.trip(refs, root, "k17", "412", DAY)
    monitor.plan(refs, DAY)
    assert kinds(refs) == []


def test_followed_work_gets_daily_meta(r):
    refs, root = r
    settings.update(refs, root, "k17", {"mode": "B"})
    wid = works.upsert(refs, "k17", "3583700")
    works.set_flag(refs, wid, followed=True)
    monitor.plan(refs, DAY)
    assert any(j["kind"] == "meta" and j["work_id"] == wid for j in jobs.recent(refs, limit=99))


def test_new_entry_counts_as_rising_once_the_board_has_history(r):
    refs, root = r
    settings.update(refs, root, "jjwxc", {"mode": "B"})
    old = works.upsert(refs, "jjwxc", "100", title="老书")
    heat.record(refs, old, "topten7", "2026-11-06", 1, 100.0, {})
    ids = _ranked(refs, 4)
    monitor.plan(refs, DAY)
    assert works.get(refs, ids[3])["candidate_reason"] == "新上榜第 4 名"


def test_failed_ranks_job_is_not_replanned_every_tick(r):
    import dataclasses
    from tools.refs import net, runner
    from tools.refs.sources import ADAPTERS
    refs, root = r
    settings.update(refs, root, "jjwxc", {"mode": "B", "retention_days": "30", "chapters_n": "2"})
    calls = []
    def boom(f, b):
        calls.append(b)
        raise net.FetchError("http", "HTTP 500")
    ad = {"jjwxc": dataclasses.replace(ADAPTERS["jjwxc"], list_ranks=boom)}
    for _ in range(10):
        monitor.plan(refs, DAY)
        runner.run_one(refs, root, adapters=ad, fetcher=lambda k, w: None, today=DAY)
    ranks = [j for j in jobs.recent(refs, limit=99) if j["source"] == "jjwxc" and j["kind"] == "ranks"]
    boards = ADAPTERS["jjwxc"].boards
    assert len(boards) < 10 and sorted(calls) == sorted(boards)          # 每个榜单只打了一次网站
    assert sorted(jobs.params(j)["board"] for j in ranks) == sorted(boards)
