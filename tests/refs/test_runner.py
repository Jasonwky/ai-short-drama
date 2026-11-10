# -*- coding: utf-8 -*-
import json

import pytest

from tools.refs import jobs, net, rjournal, runner, settings, works
from tools.refs.sources.base import Adapter, AdapterError, RankItem, Review, Unit, UnitRef, WorkMeta

BODY = "青州叶家少年看着眼前主事平静说道" * 30
DAY = "2026-11-07"


def fake_adapter(**over):
    meta = WorkMeta("9", "甲书", "作者", "u", ["古风"], "简介", 500.0, {},
                    [UnitRef(1, "第一章", "u1", True), UnitRef(2, "第二章", "u2", True), UnitRef(3, "VIP", "u3", False)])
    base = dict(key="jjwxc", boards={"b": "榜"}, content="chapters",
                list_ranks=lambda f, b: [RankItem("9", "甲书", 1, 100.0, {"x": 1}), RankItem("8", "乙书", 2, 90.0, {})],
                fetch_meta=lambda f, ext: meta,
                fetch_unit=lambda f, m, ref: Unit(ref.number, ref.title, BODY if ref.number == 1 else BODY + "请至App阅读", None))
    return Adapter(**{**base, **over})


@pytest.fixture
def env(refs, tmp_path):
    settings.ensure(refs)
    settings.update(refs, tmp_path, "jjwxc", {"retention_days": "30", "chapters_n": "5"})
    settings.update(refs, tmp_path, "hongguo", {"retention_days": "30"})
    return refs, tmp_path


def run(env, adapter, kind, **kw):
    refs, root = env
    jid = jobs.enqueue(refs, adapter.key, kind, day=DAY, **kw)
    runner.run_one(refs, root, adapters={adapter.key: adapter}, fetcher=lambda s, w: None, today=DAY)
    return jobs.get(refs, jid)


def test_ranks_create_works_and_heat(env):
    j = run(env, fake_adapter(), "ranks", params={"board": "b"})
    refs, _ = env
    assert j["state"] == "done"
    assert refs.conn.execute("SELECT COUNT(*) FROM ref_heat WHERE board='b' AND day=?", (DAY,)).fetchone()[0] == 2
    assert settings.get(refs, "jjwxc")["last_ranks_day"] == DAY


def test_ranks_day_is_set_only_when_every_board_succeeded(env):
    refs, root = env
    a = fake_adapter(boards={"b1": "榜一", "b2": "榜二"})
    jobs.enqueue(refs, "jjwxc", "ranks", day=DAY, params={"board": "b1"})
    runner.run_one(refs, root, adapters={"jjwxc": a}, fetcher=lambda s, w: None, today=DAY)
    assert settings.get(refs, "jjwxc")["last_ranks_day"] == ""
    jobs.enqueue(refs, "jjwxc", "ranks", day=DAY, params={"board": "b2"})
    runner.run_one(refs, root, adapters={"jjwxc": a}, fetcher=lambda s, w: None, today=DAY)
    assert settings.get(refs, "jjwxc")["last_ranks_day"] == DAY


def test_content_stores_free_chapters_with_verdicts_and_fingerprints(env):
    refs, root = env
    wid = works.upsert(refs, "jjwxc", "9")
    j = run(env, fake_adapter(), "content", work_id=wid)
    assert j["state"] == "done"
    rows = refs.conn.execute("SELECT unit, verdict, reason, path, due_at, hard_due_at, fetched_at FROM ref_content ORDER BY unit").fetchall()
    assert [(r["unit"], r["verdict"]) for r in rows] == [(1, "ok"), (2, "rejected")]      # VIP 章没抓
    assert "截断" in rows[1]["reason"] and (root / rows[0]["path"]).read_text(encoding="utf-8") == BODY
    assert refs.conn.execute("SELECT COUNT(*) FROM ref_fingerprint").fetchone()[0] > 0
    assert refs.conn.execute("SELECT COUNT(DISTINCT content_id) FROM ref_fingerprint").fetchone()[0] == 1   # 不收的不算指纹
    assert rows[0]["due_at"] < rows[0]["hard_due_at"]


def test_content_refused_without_retention(env):
    refs, root = env
    settings.update(refs, root, "jjwxc", {"retention_days": ""})
    wid = works.upsert(refs, "jjwxc", "9")
    j = run(env, fake_adapter(), "content", work_id=wid)
    assert j["state"] == "failed" and "保留天数" in j["error"]


def test_reviews_are_stored_without_names(env):
    refs, root = env
    a = fake_adapter(key="hongguo", content="reviews", fetch_reviews=lambda f, ext: [Review("剧情很上头" * 10, 8, 47, "t")])
    wid = works.upsert(refs, "hongguo", "9")
    assert run(env, a, "content", work_id=wid)["state"] == "done"
    path = refs.conn.execute("SELECT path FROM ref_content WHERE kind='review'").fetchone()[0]
    rec = json.loads((root / path).read_text(encoding="utf-8").splitlines()[0])
    assert set(rec) == {"text", "rating", "likes", "at"}


def test_blocked_trips_source_for_the_day(env):
    def boom(f, b):
        raise net.FetchError("blocked", "412 风控", 412)
    j = run(env, fake_adapter(list_ranks=boom), "ranks", params={"board": "b"})
    refs, _ = env
    assert j["state"] == "failed" and settings.blocked_reason(settings.get(refs, "jjwxc"), DAY)


def test_robots_disallow_pauses_source(env):
    def no(f, b):
        raise net.FetchError("disallowed", "robots.txt 禁止")
    run(env, fake_adapter(list_ranks=no), "ranks", params={"board": "b"})
    assert settings.get(env[0], "jjwxc")["state"] == "paused"


def test_unreachable_retries_then_fails(env):
    refs, root = env
    def down(f, b):
        raise net.FetchError("unreachable", "超时")
    a = fake_adapter(list_ranks=down)
    jid = jobs.enqueue(refs, "jjwxc", "ranks", day=DAY, params={"board": "b"})
    for _ in range(3):
        runner.run_one(refs, root, adapters={"jjwxc": a}, fetcher=lambda s, w: None, today=DAY)
    assert jobs.get(refs, jid)["state"] == "failed" and jobs.get(refs, jid)["attempts"] == 2


def test_three_parse_failures_trip_the_source(env):
    refs, root = env
    def broken(f, b):
        raise AdapterError("榜单解析出 0 条")
    a = fake_adapter(list_ranks=broken)
    for i in range(3):
        jobs.enqueue(refs, "jjwxc", "ranks", day=DAY, params={"board": f"b{i}"})
        runner.run_one(refs, root, adapters={"jjwxc": a}, fetcher=lambda s, w: None, today=DAY)
    s = settings.get(refs, "jjwxc")
    assert s["state"] == "tripped" and "连续 3 次" in s["state_reason"]
    assert [e["state"] for e in rjournal.read(root, source="jjwxc", event="state")] == ["tripped"]


def test_blocked_work_is_not_fetched(env):
    refs, _ = env
    wid = works.upsert(refs, "jjwxc", "9")
    works.set_flag(refs, wid, blocked=True)
    j = run(env, fake_adapter(), "content", work_id=wid)
    assert j["state"] == "failed" and "拉黑" in j["error"]


def _chapter_rows(refs):
    return [dict(r) for r in refs.conn.execute("SELECT * FROM ref_content WHERE kind = 'chapter' ORDER BY id")]


def test_retry_mid_content_does_not_duplicate_rows_or_fingerprints(env):
    refs, root = env
    n = {"c": 0}
    def unit(f, m, ref):
        if ref.number == 2 and n["c"] == 0:
            n["c"] += 1
            raise net.FetchError("unreachable", "超时")
        return Unit(ref.number, ref.title, BODY + str(ref.number), None)
    a = fake_adapter(fetch_unit=unit)
    wid = works.upsert(refs, "jjwxc", "9")
    jid = jobs.enqueue(refs, "jjwxc", "content", work_id=wid, day=DAY)
    for _ in range(3):
        runner.run_one(refs, root, adapters={"jjwxc": a}, fetcher=lambda s, w: None, today=DAY)
    assert jobs.get(refs, jid)["state"] == "done"
    rows = _chapter_rows(refs)
    assert [r["unit"] for r in rows] == [1, 2]
    fp = refs.conn.execute("SELECT content_id, COUNT(*) FROM ref_fingerprint GROUP BY content_id").fetchall()
    assert [r[0] for r in fp] == [r["id"] for r in rows]
    assert refs.conn.execute("SELECT COUNT(DISTINCT h) FROM ref_fingerprint").fetchone()[0] > 0


def test_refetch_next_day_keeps_unchanged_and_versions_changed(env):
    refs, root = env
    text = {"v": BODY}
    a = fake_adapter(fetch_unit=lambda f, m, ref: Unit(ref.number, ref.title, text["v"] + str(ref.number), None))
    wid = works.upsert(refs, "jjwxc", "9")
    jobs.enqueue(refs, "jjwxc", "content", work_id=wid, day=DAY)
    runner.run_one(refs, root, adapters={"jjwxc": a}, fetcher=lambda s, w: None, today=DAY)
    first = _chapter_rows(refs)
    jobs.enqueue(refs, "jjwxc", "content", work_id=wid, day="2026-11-08")
    runner.run_one(refs, root, adapters={"jjwxc": a}, fetcher=lambda s, w: None, today="2026-11-08")
    assert _chapter_rows(refs) == first                                   # 内容没变:不新增行
    text["v"] = BODY + "改"
    jobs.enqueue(refs, "jjwxc", "content", work_id=wid, day="2026-11-09")
    runner.run_one(refs, root, adapters={"jjwxc": a}, fetcher=lambda s, w: None, today="2026-11-09")
    rows = _chapter_rows(refs)
    assert len(rows) == 4 and len({r["path"] for r in rows}) == 4
    assert (root / first[0]["path"]).read_text(encoding="utf-8") == BODY + "1"   # 旧文件没被覆盖


def test_unexpected_exception_fails_the_job_and_is_raised(env):
    refs, root = env
    def bad(f, b):
        raise TypeError("oops")
    jid = jobs.enqueue(refs, "jjwxc", "ranks", day=DAY, params={"board": "b"})
    with pytest.raises(TypeError):
        runner.run_one(refs, root, adapters={"jjwxc": fake_adapter(list_ranks=bad)}, fetcher=lambda s, w: None, today=DAY)
    j = jobs.get(refs, jid)
    assert j["state"] == "failed" and j["error"] == "意外异常:TypeError: oops"
    assert rjournal.read(root, event="job")[0]["error"] == "意外异常:TypeError: oops"


def test_fanqie_meta_is_a_precondition_not_a_parse_failure(env):
    from tools.refs.sources import ADAPTERS
    refs, root = env
    wid = works.upsert(refs, "fanqie", "1")
    jid = jobs.enqueue(refs, "fanqie", "meta", work_id=wid, day=DAY)
    runner.run_one(refs, root, fetcher=lambda s, w: None, today=DAY)
    assert jobs.get(refs, jid)["state"] == "failed" and settings.get(refs, "fanqie")["fail_streak"] == 0
    assert ADAPTERS["fanqie"].content == "none"


def test_fetcher_factory_gets_the_job_work_id(env):
    refs, root = env
    seen = []
    wid = works.upsert(refs, "jjwxc", "9")
    jobs.enqueue(refs, "jjwxc", "meta", work_id=wid, day=DAY)
    jobs.enqueue(refs, "jjwxc", "ranks", day=DAY, params={"board": "b"})
    for _ in range(2):
        runner.run_one(refs, root, adapters={"jjwxc": fake_adapter()}, fetcher=lambda s, w: seen.append((s, w)), today=DAY)
    assert seen == [("jjwxc", wid), ("jjwxc", None)]


def test_detail_heat_raw_leaves_out_episode_ids(env):
    """红果详情的 stats 带 vids(各集集号,录屏用):每天每部多 2 KB 没用,不进热度 raw。"""
    import dataclasses
    refs, _ = env
    wid = works.upsert(refs, "jjwxc", "9")
    meta = dataclasses.replace(fake_adapter().fetch_meta(None, "9"), stats={"vids": ["1", "2"], "plays": 3})
    assert run(env, fake_adapter(fetch_meta=lambda f, ext: meta), "meta", work_id=wid)["state"] == "done"
    raw = refs.conn.execute("SELECT raw_json FROM ref_heat WHERE work_id = ? AND board = 'detail'", (wid,)).fetchone()[0]
    free = sum(u.free for u in meta.units)
    assert json.loads(raw) == {"plays": 3, "units_free": free}                  # 免费章数:拆解卡依据范围写「共 N 章免费」
