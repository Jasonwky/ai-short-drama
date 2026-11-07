# -*- coding: utf-8 -*-
from tools.refs import jobs, settings
from tools.refs.db import RefStore
from tools.serve.fakes import fake_backend
from tools.serve.runtime import Runtime


def test_refs_tick_plans_then_runs_one_job(store, tmp_path, monkeypatch):
    refs = RefStore(":memory:")
    settings.ensure(refs)
    calls = []
    monkeypatch.setattr("tools.refs.runner.run_one", lambda s, root, **kw: calls.append(root))
    rt = Runtime(store, tmp_path, fake_backend()[0], refs=refs, refs_root=tmp_path / "refs")
    rt.refs_tick()
    assert calls == [tmp_path / "refs"]


def test_start_fails_jobs_left_running(store, tmp_path):
    refs = RefStore(":memory:")
    settings.ensure(refs)
    jid = jobs.enqueue(refs, "k17", "ranks", day="d")
    jobs.start(refs, jid)
    rt = Runtime(store, tmp_path, fake_backend()[0], refs=refs, refs_root=tmp_path / "refs")
    rt.start()
    rt.stop()
    assert jobs.get(refs, jid)["state"] == "failed"


def test_runtime_without_refs_is_unchanged(store, tmp_path):
    rt = Runtime(store, tmp_path, fake_backend()[0])
    assert rt.refs is None
    rt.refs_tick()           # 没配参考库:什么都不做


def test_refs_errors_keep_the_last_five_and_survive_a_good_tick(store, tmp_path, monkeypatch):
    refs = RefStore(":memory:")
    settings.ensure(refs)
    rt = Runtime(store, tmp_path, fake_backend()[0], refs=refs, refs_root=tmp_path / "refs", refs_poll_sec=0.01)
    n = {"c": 0}
    def tick():
        n["c"] += 1
        if n["c"] <= 7:
            raise RuntimeError(f"坏了 {n['c']}")
    monkeypatch.setattr(rt, "refs_tick", tick)
    rt.start()
    import time
    deadline = time.time() + 10
    while n["c"] < 9 and time.time() < deadline:
        time.sleep(0.02)
    rt.stop()
    assert len(rt.refs_errors) == 5
    assert "RuntimeError: 坏了 7" in rt.refs_errors[-1] and "坏了 3" in rt.refs_errors[0]     # 成功一轮不清空


def test_sources_page_lists_refs_errors_and_they_can_be_cleared(store, tmp_path):
    from fastapi.testclient import TestClient
    from tools.serve.app import create_app
    refs = RefStore(":memory:")
    settings.ensure(refs)
    rt = Runtime(store, tmp_path, fake_backend()[0], refs=refs, refs_root=tmp_path / "refs")
    rt.refs_fail("TypeError: oops")
    c = TestClient(create_app(rt))
    t = c.get("/refs/sources").text
    assert "TypeError: oops" in t and "data-ref-errors" in t
    assert c.post("/refs/errors/clear", follow_redirects=False).status_code == 303
    assert rt.refs_errors == [] and "TypeError: oops" not in c.get("/refs/sources").text


BODY = "青州叶家少年看着眼前主事平静说道" * 30


def _manual_row(refs, root):
    from tools.refs import manual, works
    settings.update(refs, root, "manual", {"retention_days": "10"})
    cid = manual.add_text(refs, root, title="t", text=BODY, origin="o")
    wid = refs.conn.execute("SELECT work_id FROM ref_content WHERE id = ?", (cid,)).fetchone()[0]
    works.set_flag(refs, wid, blocked=True)          # 拉黑的立即到期
    return cid


def test_refs_tick_purges_once_a_day(store, tmp_path, monkeypatch):
    from tools.refs import clock
    refs = RefStore(":memory:")
    settings.ensure(refs)
    root = tmp_path / "refs"
    rt = Runtime(store, tmp_path, fake_backend()[0], refs=refs, refs_root=root)
    cid = _manual_row(refs, root)
    rt.refs_tick()
    assert refs.conn.execute("SELECT purged_at FROM ref_content WHERE id = ?", (cid,)).fetchone()[0]
    assert refs.conn.execute("SELECT value FROM meta WHERE key = 'last_purge_day'").fetchone()[0] == clock.today()
    calls = []
    monkeypatch.setattr("tools.refs.retention.purge", lambda *a, **k: calls.append(1) or ([], []))
    rt.refs_tick()
    rt.refs_tick()
    assert calls == []                               # 当天跑过就不再跑
    with refs.write() as c:
        c.execute("UPDATE meta SET value = '2000-01-01' WHERE key = 'last_purge_day'")
    rt.refs_tick()
    assert calls == [1]


def test_purge_errors_go_to_refs_errors_and_todo(store, tmp_path):
    from fastapi.testclient import TestClient
    from tools.serve.app import create_app
    refs = RefStore(":memory:")
    settings.ensure(refs)
    root = tmp_path / "refs"
    rt = Runtime(store, tmp_path, fake_backend()[0], refs=refs, refs_root=root)
    cid = _manual_row(refs, root)
    path = refs.conn.execute("SELECT path FROM ref_content WHERE id = ?", (cid,)).fetchone()[0]
    (root / path).unlink()
    rt.refs_tick()
    assert len(rt.refs_errors) == 1 and "找不到" in rt.refs_errors[0]
    t = TestClient(create_app(rt)).get("/refs/sources").text
    assert "参考库后台有 1 条错误" in t
