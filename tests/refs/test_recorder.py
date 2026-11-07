# -*- coding: utf-8 -*-
import dataclasses
import json
import shutil
import subprocess

import pytest

from tools.refs import avcheck, jobs, net, recorder, rjournal, settings, works
from tools.refs.sources import ADAPTERS
from tools.refs.sources.base import WorkMeta

DAY = "2026-11-07"
SID = "7561390625552092222"
VIDS = [f"90{i}" for i in range(1, 8)]
ROBOTS_OK = "User-agent: *\nDisallow: /player/*/*\nDisallow: /series/\n"      # 红果真实口径:剧号页允许、集号页禁止
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                                  reason="本机没有 ffmpeg / ffprobe:录屏检查与关键帧靠它,跳过")


def test_crop_box_scales_css_to_pixels_and_keeps_it_even_and_inside():
    # 视口 640×480 的网页录成 320×240:CSS 坐标减半;奇数往里收成偶数
    assert recorder.crop_box((101, 51, 400, 300), (640, 480), (320, 240)) == (52, 26, 198, 148)
    # 超出画面的部分夹回画面内
    assert recorder.crop_box((-10, 100, 700, 900), (640, 480), (320, 240)) == (0, 50, 320, 190)
    # 高分屏:视口 1280×900 录成 2560×1800
    assert recorder.crop_box((340, 0, 600, 900), (1280, 900), (2560, 1800)) == (680, 0, 1200, 1800)


def test_crop_box_full_frame_means_no_crop_and_empty_box_is_an_error():
    assert recorder.crop_box((0, 0, 640, 480), (640, 480), (320, 240)) is None
    with pytest.raises(RuntimeError, match="视频区域"):
        recorder.crop_box((700, 0, 100, 100), (640, 480), (320, 240))


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("本机没有 ffmpeg / ffprobe:录屏检查与关键帧靠它,跳过")
    p = tmp_path_factory.mktemp("rec") / "clip.webm"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x120:d=10", "-f", "lavfi", "-i", "sine=f=440:d=10",
                    "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-c:a", "libopus", "-shortest", str(p)], check=True)
    return p


class FakeDriver:
    def __init__(self, clip, *, logged=True, locked_at=None, slow_at=None, duration=10.0, boom=None,
                 box=(0, 0, 320, 240), viewport=(320, 240)):
        self.clip, self.logged, self.locked_at, self.duration, self.boom = clip, logged, locked_at, duration, boom
        self.slow_at = slow_at
        self.box, self.viewport = box, viewport
        self.calls, self.ep = [], None

    def open(self, sid):
        self.calls.append(("open", sid))
        self.ep = 1

    def logged_in(self):
        return self.logged

    def go_episode(self, n, vid):
        self.calls.append(("go", n, vid))
        self.ep = n

    def locked(self):
        if self.ep == self.locked_at:
            return "网页带锁(扫码进入 App 端)"
        if self.ep == self.slow_at:
            raise RuntimeError("30 秒内没加载出视频(网慢或页面变了)")
        return None

    def record_current(self, out, on_tick=None):
        if self.boom:
            raise self.boom
        shutil.copyfile(self.clip, out)
        return {"duration": self.duration, "box": self.box, "viewport": self.viewport}

    def close(self):
        self.calls.append(("close",))


class Net:
    def __init__(self, robots):
        self.robots, self.calls = robots, []

    def __call__(self, url, headers, timeout):
        self.calls.append(url)
        assert url.endswith("/robots.txt"), f"录屏只许读 robots,不许取别的页:{url}"
        return 200, self.robots.encode("utf-8"), url


@pytest.fixture
def env(refs, tmp_path):
    settings.ensure(refs)
    settings.update(refs, tmp_path, "hongguo", {"retention_days": "30"})
    wid = works.upsert(refs, "hongguo", SID, title="掌上珠")
    return refs, tmp_path, wid


def adapters(vids=VIDS):
    meta = WorkMeta(SID, "掌上珠", "", "", [], "", None, stats={"vids": list(vids)})
    return {"hongguo": dataclasses.replace(ADAPTERS["hongguo"], fetch_meta=lambda f, ext: meta)}


def run(env, driver, *, robots=ROBOTS_OK, params=None):
    refs, root, wid = env
    jid = jobs.enqueue(refs, "hongguo", "record", day=DAY, work_id=wid, params=params or {})
    made = []
    def factory():
        made.append(driver)
        return driver
    netw = Net(robots)
    recorder.run_job(refs, root, jobs.get(refs, jid), factory, adapters=adapters(),
                     fetcher=lambda src, w: net.Fetcher(root, src, opener=netw, sleep=lambda s: None, work_id=w))
    return jobs.get(refs, jid), made, netw


def rows(refs, kind):
    return [dict(r) for r in refs.conn.execute("SELECT * FROM ref_content WHERE kind = ? ORDER BY unit, id", (kind,))]


@needs_ffmpeg
def test_five_episodes_are_recorded_checked_keyframed_and_queued(env, clip):
    refs, root, wid = env
    d = FakeDriver(clip)
    job, made, netw = run(env, d)
    assert job["state"] == "done", job["error"]
    assert job["progress"] == "完成:收 5 集 / 不收 0 集"
    # 每集一个新浏览器:先开 /player/<剧号>、再点到这一集、再录(录制流一断 Chrome 就关标签页,不能跨集跳转)
    assert [c for c in d.calls if c[0] == "open"] == [("open", SID)] * 5
    assert [c for c in d.calls if c[0] == "go"] == [("go", n, VIDS[n - 1]) for n in range(2, 6)]
    seq = [c[0] for c in d.calls if c[0] in ("open", "go", "close")]
    assert seq == ["open"] + ["close", "open", "go"] * 4 + ["close"]
    assert d.calls[-1] == ("close",) and netw.calls == ["https://hongguoduanju.com/robots.txt"]
    rec = rows(refs, "recording")
    assert [(r["unit"], r["verdict"]) for r in rec] == [(n, "ok") for n in range(1, 6)]
    for r in rec:
        assert r["path"].startswith(f"content/hongguo/{SID}/ep{r['unit']}-") and r["path"].endswith(".webm")
        assert (root / r["path"]).is_file()
        m = json.loads(r["meta_json"])
        assert abs(m["duration"] - 10) < 1 and m["expected_sec"] == 10.0
    kf = rows(refs, "keyframe")
    assert kf and all(len([k for k in kf if k["unit"] == n]) <= 13 for n in range(1, 6))
    assert all((root / k["path"]).is_file() and "sec" in json.loads(k["meta_json"]) for k in kf)
    tr = [j for j in jobs.recent(refs, limit=99) if j["kind"] == "transcribe"]
    assert sorted(jobs.params(j)["recording_id"] for j in tr) == sorted(r["id"] for r in rec)
    assert not list(root.rglob("*.part.webm"))                       # 临时录屏删掉了
    ev = rjournal.read(root, event="record")
    assert len(ev) == 5 and all(e["work_id"] == wid for e in ev)


@needs_ffmpeg
def test_not_logged_in_fails_with_scan_hint(env, clip):
    refs, root, _ = env
    d = FakeDriver(clip, logged=False)
    job, _, _ = run(env, d)
    assert job["state"] == "failed" and "扫码" in job["error"]
    assert rows(refs, "recording") == [] and d.calls[-1] == ("close",)
    assert rjournal.read(root, event="login")[0]["ok"] is False
    from tools.refs import loginstate
    assert loginstate.latest(refs)["ok"] is False and loginstate.latest(refs)["text"] == "录屏前检查"


@needs_ffmpeg
def test_locked_episode_stops_the_run(env, clip):
    refs, _, _ = env
    d = FakeDriver(clip, locked_at=3)
    job, _, _ = run(env, d)
    assert job["state"] == "done" and job["progress"] == "完成:收 2 集 / 不收 1 集"
    rec = rows(refs, "recording")
    assert [(r["unit"], r["verdict"]) for r in rec] == [(1, "ok"), (2, "ok"), (3, "rejected")]
    assert rec[2]["reason"] == "网页带锁(扫码进入 App 端),这一集起网页看不了" and rec[2]["purged_at"]   # 没有文件:直接标已清理
    assert not [c for c in d.calls if c[0] == "go" and c[1] > 3]
    ev = rjournal.read(env[1], event="record")
    assert ev[0]["episode"] == 3 and ev[0]["verdict"] == "rejected" and "网页带锁" in ev[0]["reason"]


@needs_ffmpeg
def test_slow_page_fails_the_job_instead_of_counting_as_locked(env, clip):
    refs, _, _ = env
    job, _, _ = run(env, FakeDriver(clip, slow_at=2))
    assert job["state"] == "failed" and job["error"] == "第 2 集:30 秒内没加载出视频(网慢或页面变了)"
    assert [(r["unit"], r["verdict"]) for r in rows(refs, "recording")] == [(1, "ok")]


@needs_ffmpeg
def test_duration_mismatch_is_rejected_without_keyframes_or_transcription(env, clip):
    refs, _, _ = env
    job, _, _ = run(env, FakeDriver(clip, duration=20.0), params={"episodes": [1]})
    assert job["state"] == "done" and job["progress"] == "完成:收 0 集 / 不收 1 集"
    (r,) = rows(refs, "recording")
    assert r["verdict"] == "rejected" and "时长对不上" in r["reason"]
    assert rows(refs, "keyframe") == [] and not [j for j in jobs.recent(refs, limit=99) if j["kind"] == "transcribe"]


@needs_ffmpeg
def test_video_area_is_cropped(env, clip):
    refs, root, _ = env
    job, _, _ = run(env, FakeDriver(clip, box=(41, 21, 200, 160), viewport=(320, 240)), params={"episodes": [1]})
    assert job["state"] == "done", job["error"]
    (r,) = rows(refs, "recording")
    assert avcheck.frame_size(root / r["path"]) == (98, 78)          # 320 视口录成 160:(41,21,200,160) → (22,12,98,78)
    assert json.loads(r["meta_json"])["box"] == [22, 12, 98, 78]


@needs_ffmpeg
def test_single_episode_rerecord_clicks_that_episode(env, clip):
    d = FakeDriver(clip)
    job, _, _ = run(env, d, params={"episodes": [3]})
    assert job["state"] == "done" and [c for c in d.calls if c[0] == "go"] == [("go", 3, VIDS[2])]


def test_robots_forbidding_the_player_page_fails_before_opening(env, clip):
    refs, root, _ = env
    d = FakeDriver(clip)
    job, made, _ = run(env, d, robots="User-agent: *\nDisallow: /player/\n")
    assert job["state"] == "failed" and "robots 禁止打开播放页" in job["error"]
    assert made == [] and d.calls == []
    src = settings.get(refs, "hongguo")
    assert src["state"] == "paused" and "robots 禁止打开播放页" in src["state_reason"]        # 与 runner 一致:暂停来源
    assert rjournal.read(root, event="job")[0]["error"] == job["error"]


def test_blocked_work_and_missing_retention_fail(env, clip):
    refs, root, wid = env
    works.set_flag(refs, wid, blocked=True)
    job, made, _ = run(env, FakeDriver(clip))
    assert job["state"] == "failed" and "拉黑" in job["error"] and made == []
    works.set_flag(refs, wid, blocked=False)
    settings.update(refs, root, "hongguo", {"retention_days": ""})
    job, made, _ = run(env, FakeDriver(clip), params={"episodes": [1]})
    assert job["state"] == "failed" and "保留天数" in job["error"] and made == []


def test_episode_beyond_the_web_limit_fails(env, clip):
    job, made, _ = run(env, FakeDriver(clip), params={"episodes": [6]})
    assert job["state"] == "failed" and "前 5 集" in job["error"] and made == []


def test_unexpected_error_fails_the_job_closes_the_driver_and_raises(env, clip):
    d = FakeDriver(clip, boom=ZeroDivisionError("x"))
    with pytest.raises(ZeroDivisionError):
        run(env, d)
    refs = env[0]
    job = jobs.recent(refs, limit=1)[0]
    assert job["state"] == "failed" and job["error"].startswith("意外异常:ZeroDivisionError")
    assert d.calls[-1] == ("close",)


def test_driver_runtime_error_fails_the_job_naming_the_episode(env, clip):
    class Stuck(FakeDriver):
        def go_episode(self, n, vid):
            raise RuntimeError(f"点不到第 {n} 集")
    d = Stuck(clip)
    job, _, _ = run(env, d, params={"episodes": [2]})
    assert job["state"] == "failed" and job["error"] == "第 2 集:点不到第 2 集" and d.calls[-1] == ("close",)


@needs_ffmpeg
def test_keyframe_failure_still_keeps_the_recording_and_queues_transcription(env, clip, monkeypatch):
    refs, root, _ = env
    def bad(src, sec, dst):
        raise RuntimeError(f"第 {sec:.2f} 秒抽不出关键帧")
    monkeypatch.setattr(avcheck, "extract_frame", bad)
    job, _, _ = run(env, FakeDriver(clip), params={"episodes": [1]})
    assert job["state"] == "done", job["error"]
    (r,) = rows(refs, "recording")
    assert r["verdict"] == "ok" and "抽不出关键帧" in json.loads(r["meta_json"])["keyframe_error"]
    tr = [j for j in jobs.recent(refs, limit=99) if j["kind"] == "transcribe"]
    assert [jobs.params(j)["recording_id"] for j in tr] == [r["id"]]


@needs_ffmpeg
def test_check_failure_after_the_move_deletes_the_unregistered_file(env, clip, monkeypatch):
    refs, root, _ = env
    def bad(path, expected):
        raise RuntimeError("ffprobe 失败:坏文件")
    monkeypatch.setattr(avcheck, "recording_problems", bad)
    job, _, _ = run(env, FakeDriver(clip), params={"episodes": [1]})
    assert job["state"] == "failed" and job["error"] == "第 1 集:ffprobe 失败:坏文件"
    assert rows(refs, "recording") == []
    assert not list((root / "content").rglob("*.webm"))                 # 没入库的录屏不留在最终路径上
    assert job["progress"] == "失败:第 1 集:ffprobe 失败:坏文件"


@needs_ffmpeg
def test_check_failure_keeps_an_identical_file_an_earlier_row_owns(env, clip, monkeypatch):
    refs, root, _ = env
    job, _, _ = run(env, FakeDriver(clip), params={"episodes": [1]})
    (r,) = rows(refs, "recording")
    monkeypatch.setattr(avcheck, "recording_problems", lambda p, e: (_ for _ in ()).throw(RuntimeError("x")))
    run(env, FakeDriver(clip), params={"episodes": [1]})               # 同一份片子再录一次:同名文件是上一行的
    assert (root / r["path"]).is_file()


def test_part_files_are_removed_when_recording_fails(env, clip):
    refs, root, _ = env
    class Partial(FakeDriver):
        def record_current(self, out, on_tick=None):
            out.write_bytes(b"half")
            (out.parent / "ep1.part.webm.chunk").write_bytes(b"x")
            raise RuntimeError("标签页录制停不下来")
    job, _, _ = run(env, Partial(clip), params={"episodes": [1]})
    assert job["state"] == "failed" and job["error"] == "第 1 集:标签页录制停不下来"
    assert not list(root.rglob("*.part*"))


def test_playwright_error_fails_the_job_naming_the_episode(env, clip):
    from playwright.sync_api import Error as PlaywrightError
    class Closed(FakeDriver):
        def go_episode(self, n, vid):
            raise PlaywrightError("Target page, context or browser has been closed")
    d = Closed(clip)
    job, _, _ = run(env, d, params={"episodes": [2]})                  # 不再冒出去当「意外异常」
    assert job["state"] == "failed" and job["error"] == "第 2 集:Target page, context or browser has been closed"
    assert d.calls[-1] == ("close",)


@needs_ffmpeg
def test_should_stop_interrupts_before_the_next_episode(env, clip):
    refs, root, wid = env
    jid = jobs.enqueue(refs, "hongguo", "record", day=DAY, work_id=wid, params={"episodes": [1, 2]})
    d = FakeDriver(clip)
    asked = []
    def stop():
        asked.append(1)
        return len(asked) > 1                                            # 第 1 集录完后控制台在关
    netw = Net(ROBOTS_OK)
    recorder.run_job(refs, root, jobs.get(refs, jid), lambda: d, adapters=adapters(), should_stop=stop,
                     fetcher=lambda src, w: net.Fetcher(root, src, opener=netw, sleep=lambda s: None, work_id=w))
    job = jobs.get(refs, jid)
    assert job["state"] == "failed" and job["error"] == "控制台关闭,录屏中断"
    assert [(r["unit"], r["verdict"]) for r in rows(refs, "recording")] == [(1, "ok")]
    assert d.calls[-1] == ("close",)


def test_missing_vids_fails_the_job(env, clip, monkeypatch):
    refs, root, wid = env
    jid = jobs.enqueue(refs, "hongguo", "record", day=DAY, work_id=wid, params={"episodes": [3]})
    netw = Net(ROBOTS_OK)
    made = []
    recorder.run_job(refs, root, jobs.get(refs, jid), lambda: made.append(1), adapters=adapters(vids=["1", "2"]),
                     fetcher=lambda src, w: net.Fetcher(root, src, opener=netw, sleep=lambda s: None, work_id=w))
    job = jobs.get(refs, jid)
    assert job["state"] == "failed" and job["error"].startswith("详情里没有集号") and made == []


@needs_ffmpeg
def test_progress_shows_time_within_the_episode(env, clip, monkeypatch):
    refs, root, _ = env
    seen = []
    real = jobs.set_progress
    monkeypatch.setattr(jobs, "set_progress", lambda s, j, t: (seen.append(t), real(s, j, t)))
    class Ticking(FakeDriver):
        def record_current(self, out, on_tick=None):
            on_tick(42.4, 76.0)
            on_tick(43.0, 76.0)                                          # 5 秒内第二次:不写
            return super().record_current(out)
    job, _, _ = run(env, Ticking(clip), params={"episodes": [2]})
    assert job["state"] == "done", job["error"]
    assert seen[:2] == ["第 2 集(1/1):录制中", "第 2 集(1/1):0:42 / 1:16"] and "第 2 集(1/1):0:43 / 1:16" not in seen


def test_ticker_writes_at_most_every_five_seconds(refs):
    settings.ensure(refs)
    jid = jobs.enqueue(refs, "hongguo", "record", day=DAY)
    t = iter([0.0, 1.0, 4.9, 5.0, 12.0])
    tick = recorder.ticker(refs, jid, "第 1 集(1/5)", now=lambda: next(t))
    got = []
    for cur in (0, 1, 4, 5, 12):
        tick(cur, 76)
        got.append(jobs.get(refs, jid)["progress"])
    assert got == ["第 1 集(1/5):0:00 / 1:16"] * 3 + ["第 1 集(1/5):0:05 / 1:16", "第 1 集(1/5):0:12 / 1:16"]
