# -*- coding: utf-8 -*-
"""红果网页录屏编排(spec 1.1 第 3、4 条、3.1):一条 `record` 任务 = 一部剧的若干集(默认前 WEB_EPISODES 集)。

浏览器操作收在 `Driver` 后面(真实现 `hgdriver.PlaywrightDriver`,有头;单测用假的),这里只管编排、检查、入库:
逐集「点到这一集 → 带锁就停 → 从头录到播完 → 重封装 + 裁到视频区域 → 时长 / 声音 / 画面三项检查 → 入库」,
通过的抽关键帧、排一条转写(GPU worker 空档跑,`transcribe.run_one`)。

**robots 口径**:红果 robots 允许 `/player/<剧号>`、禁止 `/player/<剧号>/<集号>`(`Disallow: /player/*/*`)。
程序只**打开** `/player/<剧号>`(开跑前用 robots 判定再确认一次);**绝不主动打开**集号地址 ——
切集只像人一样在页面「选集」里点集号,或等网站播完自己跳(人点了才录、一次一部、实时播放,spec 1.1 第 3 条)。

每个分支都落到看得见的地方:任务 error / progress、journal/refs(事件 record / login / job)、意外异常再抛给 Runtime(refs_errors)。
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from tools.refs import avcheck, clock, jobs, loginstate, rjournal, settings, works
from tools.refs.net import FetchError, Fetcher
from tools.refs.sources import ADAPTERS
from tools.refs.sources.base import AdapterError
from tools.refs.sources.hongguo import BASE, WEB_EPISODES


class Driver(Protocol):
    def open(self, series_id: str) -> None: ...                 # 只打开 /player/<剧号>(robots 允许);不打开 /player/<剧>/<集>
    def logged_in(self) -> bool: ...
    def go_episode(self, n: int, vid: str) -> None: ...         # 像人一样点「选集」里的集号 n;之后当前地址应含 vid(n == 1 时不调)
    def locked(self) -> str | None: ...                         # 当前集带锁 → 原因(「扫码进入 App 端」);没锁 → None;
    #                                                             加载不出来 → RuntimeError(不当带锁)
    def record_current(self, out: Path, on_tick: Callable[[float, float], None] | None = None) -> dict: ...
    #                                                             从头播到播完,标签页录制写到 out(webm);播的时候隔一会儿调 on_tick(当前秒, 片长秒);
    #                                                             返回 {"duration": 片长秒, "box": (x,y,w,h) 视频区域 CSS 像素, "viewport": (w,h) CSS 像素}
    def close(self) -> None: ...


class RecordError(Exception):
    """前提不满足 / 某一集操作不下去:任务失败,原因给人看(不是代码 bug,不再抛)。"""


class _Locked(Exception):
    """这一集带锁:记一行 rejected,后面的集不录。"""


def crop_box(box, viewport, frame) -> tuple[int, int, int, int] | None:
    """网页上视频区域(CSS 像素)换算成录出画面里的像素框:按 画面 / 视口 的比例缩放(高分屏的 devicePixelRatio 自然含在里面),
    夹在画面内、往里收成偶数(vp9 / yuv420 要偶数);整幅画面 → None(不裁,省一次重编码);框不在画面里 → RuntimeError。"""
    (x, y, w, h), (vw, vh), (fw, fh) = box, viewport, frame
    sx, sy = fw / vw, fh / vh
    x0, y0 = max(0, math.ceil(x * sx)), max(0, math.ceil(y * sy))
    x0, y0 = x0 + x0 % 2, y0 + y0 % 2
    x1, y1 = min(fw, math.floor((x + w) * sx)), min(fh, math.floor((y + h) * sy))
    cw, ch = x1 - x0, y1 - y0
    cw, ch = cw - cw % 2, ch - ch % 2
    if cw <= 0 or ch <= 0:
        raise RuntimeError(f"视频区域不在录出的画面里:网页上 {tuple(box)}(视口 {tuple(viewport)}),画面 {fw}×{fh}")
    if (x0, y0, cw, ch) == (0, 0, fw - fw % 2, fh - fh % 2):
        return None
    return x0, y0, cw, ch


def _sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _insert(refs, src, wid, kind, unit, title, rel, sha, origin, verdict, reason, meta, *, purged: bool = False) -> int:
    at = clock.now_iso()
    due, hard_due = settings.due_dates(src, at)
    with refs.write() as c:
        return c.execute(
            "INSERT INTO ref_content (work_id, kind, unit, title, path, sha256, origin, verdict, reason, fetched_at, due_at, hard_due_at,"
            " purged_at, meta_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (wid, kind, unit, title, rel, sha, origin, verdict, reason, at, due, hard_due,
             at if purged else "", json.dumps(meta, ensure_ascii=False))).lastrowid


def _mmss(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def ticker(refs, jid: int, label: str, *, every: float = 5.0, now: Callable[[], float] = time.monotonic):
    """集内进度:`<label>:0:42 / 1:16`,每 every 秒最多写一次库。"""
    last: list[float] = []

    def tick(cur: float, total: float) -> None:
        t = now()
        if last and t - last[0] < every:
            return
        last[:] = [t]
        jobs.set_progress(refs, jid, f"{label}:{_mmss(cur)} / {_mmss(total)}")
    return tick


def _episode(refs, root: Path, src, w, n: int, driver: Driver, today: str, on_tick=None) -> bool:
    """录一集并入库;返回收没收。带锁抛 _Locked(这一集记 rejected,后面不录)。
    中途出错:临时文件(ep<n>.part*)一律删;已挪到最终路径、还没入库的录屏也删(原来就有的同名文件不动)。"""
    sid = w["external_id"]
    origin = f"{BASE}/player/{sid}(网页录屏第 {n} 集)"
    lock = driver.locked()
    if lock:
        reason = f"{lock},这一集起网页看不了"
        cid = _insert(refs, src, w["id"], "recording", n, f"第 {n} 集", "", "", origin, "rejected",
                      reason, {}, purged=True)     # 没有文件:直接标已清理,到期清理不碰它
        rjournal.append(root, "record", source="hongguo", work_id=w["id"], episode=n, content=cid, verdict="rejected", reason=reason)
        raise _Locked
    base = Path(root) / "content" / "hongguo" / sid
    base.mkdir(parents=True, exist_ok=True)
    tmp, staged = base / f"ep{n}.part.webm", base / f"ep{n}.part2.webm"
    try:
        got = driver.record_current(tmp, on_tick)
        frame = avcheck.frame_size(tmp)
        box = crop_box(got["box"], got["viewport"], frame)
        avcheck.remux_crop(tmp, staged, box)
        sha = _sha_file(staged)
        rel = f"content/hongguo/{sid}/ep{n}-{sha[:12]}.webm"
        final = Path(root) / rel
        existed = final.exists()                    # 一模一样的录屏以前收过:文件是那一行的,出错也不删
        staged.replace(final)
    finally:
        for part in base.glob(f"ep{n}.part*"):
            part.unlink(missing_ok=True)
    try:
        problems, meta = avcheck.recording_problems(final, float(got["duration"]))
        meta.update(box=list(box) if box else None, frame=list(frame))
        ok = not problems
        cid = _insert(refs, src, w["id"], "recording", n, f"第 {n} 集", rel, sha, origin,
                      "ok" if ok else "rejected", ";".join(problems), meta)
    except BaseException:
        if not existed:
            final.unlink(missing_ok=True)
        raise
    if ok:
        jobs.enqueue(refs, "hongguo", "transcribe", day=today, work_id=w["id"], params={"recording_id": cid})   # 先排转写:抽帧失败不挡它
        kdir = f"content/hongguo/{sid}/ep{n}-{sha[:12]}-kf"         # 带录屏 sha:重录不覆盖上一版还被引用的关键帧
        try:
            (Path(root) / kdir).mkdir(parents=True, exist_ok=True)
            for t in avcheck.keyframe_times(meta["duration"]):
                krel = f"{kdir}/{t:g}.jpg"
                avcheck.extract_frame(final, t, Path(root) / krel)
                _insert(refs, src, w["id"], "keyframe", n, f"第 {n} 集 {t:g} 秒", krel, _sha_file(Path(root) / krel),
                        f"录屏 #{cid}", "ok", "", {"sec": t})
        except (RuntimeError, OSError) as e:                       # 录屏照收;原因记在这一行上,录屏面板显示
            with refs.write() as c:
                c.execute("UPDATE ref_content SET meta_json = json_set(meta_json, '$.keyframe_error', ?) WHERE id = ?", (str(e), cid))
    rjournal.append(root, "record", source="hongguo", work_id=w["id"], episode=n, content=cid,
                    verdict="ok" if ok else "rejected", reason=";".join(problems), **{k: meta[k] for k in ("duration", "expected_sec")})
    return ok


def _episodes(job) -> list[int]:
    eps = jobs.params(job).get("episodes") or list(range(1, WEB_EPISODES + 1))
    bad = [n for n in eps if not (isinstance(n, int) and 1 <= n <= WEB_EPISODES)]
    if bad:
        raise RecordError(f"网页只能看前 {WEB_EPISODES} 集(收到 {bad})")
    return sorted(set(eps))


def _driver_errors() -> tuple[type[BaseException], ...]:
    """某一集操作不下去的异常:RuntimeError(我们自己抛的、ffmpeg)+ Playwright 的 Error(页面关了、超时)。"""
    try:
        from playwright.sync_api import Error as PlaywrightError
    except ImportError:
        return (RuntimeError,)
    return (RuntimeError, PlaywrightError)


def _record(refs, root: Path, job, driver_factory, adapters, fetcher, today: str, should_stop) -> str:
    if job["source"] != "hongguo":
        raise RecordError("只有红果能录屏")
    src = settings.get(refs, "hongguo")
    why = settings.blocked_reason(src, today)
    if why:
        raise RecordError(why)
    w = works.get(refs, job["work_id"])
    if w["blocked"]:
        raise RecordError("这部作品已拉黑,不录")
    p = settings.fetch_problems(src)
    if p:
        raise RecordError(";".join(p))
    eps = _episodes(job)
    sid = w["external_id"]
    f = fetcher("hongguo", w["id"])                         # 一个 Fetcher = 这一轮只读一次 robots(原文进 journal)
    if not f.allowed(f"{BASE}/player/{sid}"):
        raise FetchError("disallowed", f"robots 禁止打开播放页 {BASE}/player/{sid}")     # run_job 照 runner 暂停来源
    m = adapters["hongguo"].fetch_meta(f, sid)              # 各集集号(vids)只在详情里,热度 raw 不存它
    works.apply_meta(refs, w["id"], m)
    vids = m.stats.get("vids") or []
    if len(vids) < max(eps):
        raise RecordError(f"详情里没有集号(只有 {len(vids)} 个,不够录到第 {max(eps)} 集)")
    w = works.get(refs, w["id"])
    driver = driver_factory()
    try:
        driver.open(sid)
        try:
            ok = driver.logged_in()
        except RuntimeError as e:                    # 判不出登录状态(既没弹登录框也没播起来):如实失败,不当已登录
            raise RecordError(f"判不出红果登录状态:{e}") from e
        loginstate.record(refs, root, ok, "录屏前检查", work_id=w["id"], job=job["id"])
        if not ok:
            raise RecordError("红果没登录 —— 在录屏面板点「打开浏览器扫码」")
        got = bad = 0
        errors = _driver_errors()
        for i, n in enumerate(eps, 1):
            if should_stop is not None and should_stop():
                raise RecordError("控制台关闭,录屏中断")
            label = f"第 {n} 集({i}/{len(eps)})"
            jobs.set_progress(refs, job["id"], f"{label}:录制中")
            try:
                if i > 1:
                    # 每集重开一次浏览器(真机 2026-11-07):录当前标签页的流一结束(停轨道、或页面跳到下一集),
                    # Chrome 就把标签页和浏览器一起关掉 —— 所以先跳到这一集再开录,录完这一集就关
                    driver.close()
                    driver.open(sid)
                if n > 1:
                    driver.go_episode(n, vids[n - 1])
                if _episode(refs, root, src, w, n, driver, today, ticker(refs, job["id"], label)):
                    got += 1
                else:
                    bad += 1
            except _Locked:
                bad += 1
                break
            except errors as e:                             # 点不到集号、录制 / ffmpeg / 页面失败:说清是哪一集,前面录好的照收
                raise RecordError(f"第 {n} 集:{e}") from e
        done = f"完成:收 {got} 集 / 不收 {bad} 集"
        jobs.set_progress(refs, job["id"], done)
        return done
    finally:
        driver.close()


def run_job(refs, root: Path, job: dict, driver_factory: Callable[[], Driver], *, adapters: dict | None = None,
            fetcher: Callable[[str, int | None], object] | None = None, today: str | None = None,
            should_stop: Callable[[], bool] | None = None) -> None:
    """录屏线程调用:跑一条 record 任务。前提 / 取页 / 某集操作失败 → 任务失败带原因;意外异常 → 失败、留痕、再抛(与 runner 一致)。
    should_stop:每集开录前问一次(控制台在关),是 → 任务失败「控制台关闭,录屏中断」。失败时 progress 写「失败:<原因前 40 字>」。"""
    root = Path(root)
    adapters = adapters or ADAPTERS
    fetcher = fetcher or (lambda src, wid: Fetcher(root, src, work_id=wid))
    today = today or clock.today()
    log = dict(source=job["source"], work_id=job["work_id"], job=job["id"], kind="record")
    jobs.start(refs, job["id"])

    def fail(err: str) -> None:
        jobs.set_progress(refs, job["id"], f"失败:{err[:40]}")
        jobs.finish(refs, job["id"], error=err)
    try:
        msg = _record(refs, root, job, driver_factory, adapters, fetcher, today, should_stop)
    except FetchError as e:
        fail(str(e))
        if e.kind == "disallowed":
            settings.pause(refs, root, job["source"], str(e))
        elif e.kind == "blocked":
            settings.trip(refs, root, job["source"], str(e), today)
        rjournal.append(root, "job", **log, error=str(e))
        return
    except (RecordError, AdapterError, KeyError) as e:
        err = f"解析失败:{e}" if isinstance(e, AdapterError) else (e.args[0] if isinstance(e, KeyError) else str(e))
        fail(err)
        rjournal.append(root, "job", **log, error=err)
        return
    except Exception as e:
        msg = f"意外异常:{type(e).__name__}: {e}"
        fail(msg)
        rjournal.append(root, "job", **log, error=msg)
        raise
    jobs.finish(refs, job["id"])
    rjournal.append(root, "job", **log, done=msg)
