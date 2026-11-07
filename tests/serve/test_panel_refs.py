# -*- coding: utf-8 -*-
import pytest
from fastapi.testclient import TestClient

from tools.refs import heat, jobs, rjournal, settings, works
from tools.refs.db import RefStore
from tools.serve.app import create_app
from tools.serve.fakes import fake_backend
from tools.serve.runtime import Runtime

BODY = "青州叶家少年看着眼前主事平静说道" * 30


@pytest.fixture
def rc(store, tmp_path):
    refs = RefStore(":memory:")
    settings.ensure(refs)
    rt = Runtime(store, tmp_path, fake_backend()[0], refs=refs, refs_root=tmp_path / "refs")
    return TestClient(create_app(rt)), refs, tmp_path / "refs"


def test_sources_page_marks_missing_star_fields(rc):
    c, _, _ = rc
    t = c.get("/refs/sources").text
    assert "data-ref-source" in t and "★ 必填" in t and "红果短剧" in t


def test_setting_mode_a_without_caps_is_a_400_with_reason(rc):
    c, _, _ = rc
    r = c.post("/refs/source/hongguo", data={"mode": "A", "retention_days": "30"})
    assert r.status_code == 400 and "每天自动抓几部" in r.text


def test_settings_saved_and_logged(rc):
    c, refs, root = rc
    c.post("/refs/source/k17", data={"retention_days": "30", "chapters_n": "10"})
    assert settings.get(refs, "k17")["chapters_n"] == 10
    log = [tuple(x) for x in refs.conn.execute("SELECT field, old, new FROM ref_setting_log WHERE source = 'k17' ORDER BY id")]
    assert log == [("retention_days", None, "30"), ("chapters_n", None, "10")]


def test_ranks_page_lists_today_and_search_says_its_scope(rc):
    c, refs, _ = rc
    from tools.refs import clock
    wid = works.upsert(refs, "jjwxc", "1", title="逃离玫瑰岛")
    heat.record(refs, wid, "topten7", clock.today(), 1, 4.5e10, {})
    t = c.get("/refs/ranks?source=jjwxc&board=topten7").text
    assert "data-ref-rank-row" in t and "逃离玫瑰岛" in t
    assert "只覆盖已见过的作品" in c.get("/refs/ranks?q=玫瑰").text


def test_add_by_link_queues_meta_and_opens_detail(rc):
    c, refs, _ = rc
    r = c.post("/refs/add", data={"url": "http://www.17k.com/book/3583700.html"}, follow_redirects=False)
    assert r.status_code == 303 and "/refs/work?id=" in r.headers["location"]
    assert jobs.recent(refs)[0]["kind"] == "meta"


def test_manual_text_shows_on_work_page(rc):
    c, refs, root = rc
    settings.update(refs, root, "manual", {"retention_days": "10"})
    wid = works.upsert(refs, "manual", "manual-1", title="我的梗概")
    c.post(f"/refs/work/{wid}/manual", data={"title": "梗概", "text": BODY, "origin": "自己看完整理"})
    t = c.get(f"/refs/work?id={wid}").text
    assert "data-ref-content" in t and "自己看完整理" in t


def test_block_purges_immediately(rc):
    c, refs, root = rc
    settings.update(refs, root, "manual", {"retention_days": "10"})
    wid = works.upsert(refs, "manual", "manual-1", title="x")
    c.post(f"/refs/work/{wid}/manual", data={"title": "梗概", "text": BODY, "origin": "o"})
    c.post(f"/refs/work/{wid}/block")
    assert refs.conn.execute("SELECT purged_at FROM ref_content").fetchone()[0]


def test_panels_say_not_configured_without_refs(client):
    for url in ("/refs/sources", "/refs/ranks", "/refs/work"):
        r = client.get(url)
        assert r.status_code == 200 and "未配置参考库" in r.text


def test_work_page_draws_curve_with_gap(rc):
    c, refs, root = rc
    wid = works.upsert(refs, "jjwxc", "9", title="有缺口")
    for day, v in (("2026-11-01", 10.0), ("2026-11-02", 20.0), ("2026-11-04", 15.0), ("2026-11-05", 30.0)):
        heat.record(refs, wid, "topten7", day, 3, v, {})
    t = c.get(f"/refs/work?id={wid}").text
    assert t.count("<polyline") == 2 and "缺 1 天" in t


def test_pull_ranks_queues_every_board_and_logs_on_sources_page(rc):
    c, refs, root = rc
    from tools.refs.sources import ADAPTERS
    assert c.post("/refs/source/jjwxc/ranks", follow_redirects=False).status_code == 303
    assert {jobs.params(j)["board"] for j in jobs.recent(refs) if j["kind"] == "ranks"} == set(ADAPTERS["jjwxc"].boards)
    settings.pause(refs, root, "jjwxc", "robots 禁止")
    assert "robots 禁止" in c.post("/refs/source/jjwxc/ranks").text
    assert "robots 禁止" in c.get("/refs/sources").text


def test_ranks_page_only_shows_the_chosen_source(rc):
    c, refs, _ = rc
    from tools.refs import clock
    a = works.upsert(refs, "jjwxc", "1", title="晋江的书")
    b = works.upsert(refs, "k17", "2", title="十七的书")
    heat.record(refs, a, "same", clock.today(), 1, 1.0, {})
    heat.record(refs, b, "same", clock.today(), 1, 1.0, {})
    t = c.get("/refs/ranks?source=jjwxc&board=same").text
    assert "晋江的书" in t and "十七的书" not in t


def test_meta_only_link_queues_nothing(rc):
    c, refs, _ = rc
    r = c.post("/refs/add", data={"url": "https://fanqienovel.com/page/7143038691944959011"}, follow_redirects=False)
    assert r.status_code == 303 and jobs.recent(refs) == []


def test_no_fetch_for_manual_or_meta_only_sources(rc):
    c, refs, root = rc
    for src in ("manual", "fanqie"):
        wid = works.upsert(refs, src, "x1", title="只有元数据")
        assert ">抓正文</button>" not in c.get(f"/refs/work?id={wid}").text
        r = c.post(f"/refs/work/{wid}/fetch")
        assert r.status_code == 400 and "不抓正文" in r.text
    assert jobs.recent(refs) == []


def test_pull_ranks_for_manual_is_a_400_with_reason(rc):
    c, refs, _ = rc
    assert "data-ref-pull-ranks" not in c.get("/refs/sources").text.split('data-ref-source="manual"')[1]
    r = c.post("/refs/source/manual/ranks")
    assert r.status_code == 400 and "没有榜单" in r.text and "'manual'" not in r.text


def test_manual_text_for_a_missing_work_is_404_without_orphans(rc):
    c, refs, root = rc
    settings.update(refs, root, "manual", {"retention_days": "10"})
    r = c.post("/refs/work/999/manual", data={"title": "t", "text": BODY, "origin": "o"})
    assert r.status_code == 404 and not (root / "content").exists()


def test_unknown_source_is_404_everywhere(rc):
    c, refs, root = rc
    for url in ("/refs/source/nope", "/refs/source/nope/reset", "/refs/source/nope/ranks"):
        assert c.post(url, data={"mode": "B"}).status_code == 404, url
    assert not any(e.get("source") == "nope" for e in rjournal.read(root))


def test_ranks_page_shows_only_todays_candidate_reason(rc):
    c, refs, _ = rc
    from tools.refs import clock
    old = works.upsert(refs, "jjwxc", "1", title="昨天的候选")
    new = works.upsert(refs, "jjwxc", "2", title="今天的候选")
    works.set_candidate(refs, old, "昨天第 1 名(前 2 名)", "2000-01-01")
    works.set_candidate(refs, new, "今天第 2 名(前 2 名)", clock.today())
    for w, rank in ((old, 1), (new, 2)):
        heat.record(refs, w, "topten7", clock.today(), rank, 1.0, {})
    t = c.get("/refs/ranks?source=jjwxc&board=topten7").text
    assert "今天第 2 名" in t and "昨天第 1 名" not in t
