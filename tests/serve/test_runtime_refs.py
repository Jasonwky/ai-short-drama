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


def test_idle_gpu_worker_runs_one_reference_transcription(store, host_id, tmp_path):
    """GPU worker 机器空着 → 跑一条参考库转写:脚本(嵌着真录屏转出的 PCM)经对齐 runner 送云机,结果入库。"""
    import dataclasses
    import json
    import shutil
    import subprocess
    import pytest
    from tools.refs import works
    if shutil.which("ffmpeg") is None:
        pytest.skip("本机没有 ffmpeg:录屏转 PCM 靠它,跳过")
    refs = RefStore(":memory:")
    settings.ensure(refs)
    root = tmp_path / "refs"
    rel = "content/hongguo/77/1.webm"
    (root / rel).parent.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x120:d=2", "-f", "lavfi", "-i", "sine=f=440:d=2",
                    "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-c:a", "libopus", "-shortest", str(root / rel)],
                   check=True)
    wid = works.upsert(refs, "hongguo", "77")
    with refs.write() as c:
        rid = c.execute("INSERT INTO ref_content (work_id, kind, unit, title, path, sha256, origin, verdict, fetched_at, due_at, hard_due_at)"
                        " VALUES (?, 'recording', 1, '第 1 集', ?, 'x', 'u', 'ok', 't', '2026-12-07', '2027-01-06')", (wid, rel)).lastrowid
    jid = jobs.enqueue(refs, "hongguo", "transcribe", day="2026-11-07", work_id=wid, params={"recording_id": rid})
    text = "你怎么还在这里我不是让你走了吗今天这件事没完我们走着瞧"
    got = []
    def runner(args, script):
        got.append(script)
        return 0, json.dumps({"text": text, "chars": [[c, 0.0, 0.1] for c in text], "chunks": 1, "asr_sec": 1.0}, ensure_ascii=False)
    backend = dataclasses.replace(fake_backend()[0], align_for=lambda h: runner)
    rt = Runtime(store, tmp_path, backend, refs=refs, refs_root=root)
    rt.tick_all()
    assert len(got) == 1 and b"_AUDIO_B64" in got[0] and len(got[0]) > 2 * 16000 * 2   # 2 秒 16 kHz s16le 的 base64 嵌在里面
    assert jobs.get(refs, jid)["state"] == "done"
    assert refs.conn.execute("SELECT verdict FROM ref_content WHERE kind = 'transcript'").fetchone()[0] == "ok"


SID = "7561390625552092222"


def _record_env(tmp_path, monkeypatch, robots_txt=b"User-agent: *\nDisallow: /player/*/*\n"):
    """红果作品 + 一条 record 任务;recorder 取详情换成假的、robots 走假网络(Runtime 的 refs_fetcher),driver 由 Runtime 给。
    返回 (refs, root, jid, Runtime 的关键字参数)。"""
    import dataclasses
    from tools.refs import net, recorder, works
    from tools.refs.sources import ADAPTERS
    from tools.refs.sources.base import WorkMeta
    refs = RefStore(":memory:")
    settings.ensure(refs)
    root = tmp_path / "refs"
    settings.update(refs, root, "hongguo", {"retention_days": "30"})
    wid = works.upsert(refs, "hongguo", SID)
    jid = jobs.enqueue(refs, "hongguo", "record", day="2026-11-07", work_id=wid, params={"episodes": [1]})
    meta = WorkMeta(SID, "掌上珠", "", "", [], "", None, stats={"vids": ["1", "2", "3", "4", "5"]})
    ad = {"hongguo": dataclasses.replace(ADAPTERS["hongguo"], fetch_meta=lambda f, ext: meta)}
    robots = lambda url, h, t: (200, robots_txt, url)
    real = recorder.run_job
    monkeypatch.setattr("tools.refs.recorder.run_job", lambda refs_, root_, job, factory, **kw: real(
        refs_, root_, job, factory, adapters=ad, **kw))
    fetcher = lambda s, w: net.Fetcher(root, s, opener=robots, sleep=lambda x: None, work_id=w)
    return refs, root, jid, {"refs": refs, "refs_root": root, "refs_fetcher": fetcher}


class _Driver:
    def __init__(self, clip=None, gate=None):
        self.clip, self.gate, self.calls = clip, gate, []

    def open(self, sid):
        self.calls.append("open")
        if self.gate is not None:
            self.gate.wait(10)

    def logged_in(self):
        return self.clip is not None

    def go_episode(self, n, vid):
        self.calls.append(("go", n))

    def locked(self):
        return None

    def record_current(self, out, on_tick=None):
        import shutil
        shutil.copyfile(self.clip, out)
        return {"duration": 3.0, "box": (0, 0, 160, 120), "viewport": (160, 120)}

    def close(self):
        self.calls.append("close")


def test_record_tick_runs_one_record_job_with_the_runtime_driver(store, tmp_path, monkeypatch):
    import shutil
    import subprocess
    import pytest
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("本机没有 ffmpeg / ffprobe:录屏检查靠它,跳过")
    clip = tmp_path / "clip.webm"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x120:d=3", "-f", "lavfi", "-i", "sine=f=440:d=3",
                    "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-c:a", "libopus", "-shortest", str(clip)], check=True)
    refs, root, jid, kw = _record_env(tmp_path, monkeypatch)
    d = _Driver(clip)
    rt = Runtime(store, tmp_path, fake_backend()[0], record_driver=lambda: d, **kw)
    rt.refs_record_tick()
    assert jobs.get(refs, jid)["state"] == "done", jobs.get(refs, jid)["error"]
    assert d.calls == ["open", "close"] and rt.refs_browser is None
    assert refs.conn.execute("SELECT verdict FROM ref_content WHERE kind = 'recording'").fetchone()[0] == "ok"
    rt.refs_record_tick()                            # 没有任务了:什么都不做
    assert d.calls == ["open", "close"]


def test_login_is_refused_while_recording(store, tmp_path, monkeypatch):
    import threading
    import time
    import pytest
    refs, root, jid, kw = _record_env(tmp_path, monkeypatch)
    gate = threading.Event()
    d = _Driver(gate=gate)                           # 没登录:open 卡住,放开后任务以「没登录」失败
    called = []
    rt = Runtime(store, tmp_path, fake_backend()[0], record_driver=lambda: d,
                 login_fn=lambda *a, **k: called.append(a) or True, **kw)
    t = threading.Thread(target=rt.refs_record_tick)
    t.start()
    deadline = time.time() + 10
    while rt.refs_browser != "record" and time.time() < deadline:
        time.sleep(0.01)
    with pytest.raises(ValueError, match="正在录屏,录完再扫码"):
        rt.start_login()
    gate.set()
    t.join(10)
    assert called == [] and rt.refs_browser is None
    assert jobs.get(refs, jid)["state"] == "failed" and "扫码" in jobs.get(refs, jid)["error"]


def test_start_login_runs_login_once_and_holds_the_profile(store, tmp_path, monkeypatch):
    import threading
    import time
    import pytest
    refs, root, jid, kw = _record_env(tmp_path, monkeypatch)
    gate, called = threading.Event(), []
    def login(profile, sid):
        called.append((profile, sid))
        gate.wait(10)
        return True
    rt = Runtime(store, tmp_path, fake_backend()[0], record_driver=lambda: _Driver(), login_fn=login, **kw)
    rt.start_login()
    assert rt.refs_login["state"] == "running"
    with pytest.raises(ValueError, match="已经开着"):
        rt.start_login()
    rt.refs_record_tick()                            # 扫码中:录屏不开,任务留着等
    assert jobs.get(refs, jid)["state"] == "waiting"
    gate.set()
    deadline = time.time() + 10
    while rt.refs_login["state"] == "running" and time.time() < deadline:
        time.sleep(0.01)
    assert rt.refs_login["state"] == "ok" and rt.refs_browser is None
    assert called == [(root / "profile", SID)]
    from tools.refs import loginstate
    assert loginstate.latest(refs)["ok"] is True and loginstate.latest(refs)["text"] == "扫码登录"


def test_start_login_needs_a_hongguo_work(store, tmp_path):
    import pytest
    refs = RefStore(":memory:")
    settings.ensure(refs)
    rt = Runtime(store, tmp_path, fake_backend()[0], refs=refs, refs_root=tmp_path / "refs")
    with pytest.raises(ValueError, match="还没有红果作品"):
        rt.start_login()


def test_record_thread_errors_go_to_refs_errors(store, tmp_path, monkeypatch):
    import time
    refs = RefStore(":memory:")
    settings.ensure(refs)
    rt = Runtime(store, tmp_path, fake_backend()[0], refs=refs, refs_root=tmp_path / "refs", refs_poll_sec=0.01)
    monkeypatch.setattr(rt, "refs_tick", lambda: None)
    def boom():
        raise RuntimeError("录崩了")
    monkeypatch.setattr(rt, "refs_record_tick", boom)
    rt.start()
    deadline = time.time() + 10
    while not rt.refs_errors and time.time() < deadline:
        time.sleep(0.02)
    rt.stop()
    assert "录屏:RuntimeError: 录崩了" in rt.refs_errors[0]


def test_transcription_crash_in_idle_hook_does_not_escape_worker_tick(store, host_id, tmp_path, monkeypatch):
    import dataclasses
    from tools.refs import works
    refs = RefStore(":memory:")
    settings.ensure(refs)
    root = tmp_path / "refs"
    rel = "content/hongguo/77/1.webm"
    (root / rel).parent.mkdir(parents=True)
    (root / rel).write_bytes(b"webm")
    wid = works.upsert(refs, "hongguo", "77")
    with refs.write() as c:
        rid = c.execute("INSERT INTO ref_content (work_id, kind, unit, title, path, sha256, origin, verdict, fetched_at, due_at, hard_due_at)"
                        " VALUES (?, 'recording', 1, '第 1 集', ?, 'x', 'u', 'ok', 't', '2026-12-07', '2027-01-06')", (wid, rel)).lastrowid
    jid = jobs.enqueue(refs, "hongguo", "transcribe", day="2026-11-07", work_id=wid, params={"recording_id": rid})
    import tools.refs.transcribe as tr
    orig = tr.pcm16k
    tr.pcm16k = lambda p: b"\x00\x00" * 160
    try:
        def runner(args, script):
            raise FileNotFoundError("ssh 不在 PATH 上")
        backend = dataclasses.replace(fake_backend()[0], align_for=lambda h: runner)
        rt = Runtime(store, tmp_path, backend, refs=refs, refs_root=root)
        rt.tick_all()                                # 不抛
    finally:
        tr.pcm16k = orig
    assert jobs.get(refs, jid)["state"] == "failed"
    assert any("转写:" in e and "FileNotFoundError" in e for e in rt.refs_errors)
    assert any("参考库转写" in l and "FileNotFoundError" in l for l in rt.status[host_id].last_lines)


def test_login_refused_by_robots_pauses_hongguo_without_opening_a_browser(store, tmp_path, monkeypatch):
    import time
    refs, root, jid, kw = _record_env(tmp_path, monkeypatch, robots_txt=b"User-agent: *\nDisallow: /player/\n")
    called = []
    rt = Runtime(store, tmp_path, fake_backend()[0], record_driver=lambda: _Driver(),
                 login_fn=lambda *a, **k: called.append(a) or True, **kw)
    rt.start_login()
    deadline = time.time() + 10
    while rt.refs_login["state"] == "running" and time.time() < deadline:
        time.sleep(0.01)
    assert called == [] and rt.refs_browser is None
    assert rt.refs_login["state"] == "failed" and "robots 禁止打开播放页" in rt.refs_login["message"]
    src = settings.get(refs, "hongguo")
    assert src["state"] == "paused" and "robots" in src["state_reason"]


def test_record_tick_stops_when_the_console_is_closing(store, tmp_path, monkeypatch):
    refs, root, jid, kw = _record_env(tmp_path, monkeypatch)
    d = _Driver(clip="unused")                       # 登录着;第 1 集开录前就该停
    rt = Runtime(store, tmp_path, fake_backend()[0], record_driver=lambda: d, **kw)
    rt._stop.set()
    rt.refs_record_tick()
    j = jobs.get(refs, jid)
    assert j["state"] == "failed" and j["error"] == "控制台关闭,录屏中断" and d.calls == ["open", "close"]


def test_default_login_fn_is_the_real_qr_login(store, tmp_path):
    from tools.refs import hgdriver
    refs = RefStore(":memory:")
    assert Runtime(store, tmp_path, fake_backend()[0], refs=refs, refs_root=tmp_path / "refs").login_fn is hgdriver.login
