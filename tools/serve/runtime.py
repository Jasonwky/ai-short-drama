# -*- coding: utf-8 -*-
"""控制台运行时:库 + 素材根目录 + 执行面后端 + 后台 worker 线程 + 主机判活。

- 每台登记的云机一个 `Worker`,由一个后台线程轮流 `tick`(与命令行 worker 同一套代码);
  每轮的动作经 `journal.append` 留痕。
- **判活分两层,界面上分开说**(设计 §四):隧道没通 / 隧道通了但 ComfyUI 没响应。
  P2 真链路实测:云机开机后 ComfyUI **不会自启**,只看隧道会把「ComfyUI 没起」误报成正常。
- `Backend` 决定怎么连执行面。正式入口只用真实后端;假后端只给测试与 Playwright 自测。
- LLM 任务(一次 30–80 秒)放进**单线程**池在后台跑(M-21 落盘单线程),页面立刻返回、自动刷新看结果。
  真实后端点按钮时才读 key:没有 key 就明确报「LLM 不可用」,**不静默切假 LLM**。
- 成片导出(一集 1–3 分钟 ffmpeg)同样放进单线程池后台跑;控制台启动时把上次没跑完的导出标失败。
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from tools.cut import export
from tools.drama.db import Store
from tools.gen import nodecache, preflight
from tools.gen.comfy_client import ComfyClient, ComfyUnreachable
from tools.llm import flow, llm_queue, runs, service
from tools.llm.claude_cli import ClaudeCLIClient
from tools.llm.client import LLMUnavailable
from tools.llm.service import LLMBackend
from tools.serve import journal
from tools.serve.hosts import Host, get_host
from tools.serve.tunnel import TunnelManager
from tools.serve.worker import POLL_SEC, Worker, _ssh_align, _ssh_run, _ssh_idx, _ssh_tts


@dataclass
class Backend:
    label: str
    client_for: Callable[[Host], object]
    tunnel: object
    run_for: Callable[[Host], preflight.Runner]
    fake: bool = False
    before_tick: Callable[[], None] | None = None   # 假 GPU 在这里「跑完」队列
    llm: Callable[[], LLMBackend] | None = None     # 点「LLM 生成」时才调用
    align_for: Callable[[Host], preflight.Runner] = _ssh_align   # 字幕对齐:云机上跑 whisper 的 ssh
    tts_for: Callable[[Host], preflight.Runner] = _ssh_tts       # 配音:云机上跑 qwen-tts 的 ssh
    idx_for: Callable[[Host], preflight.Runner] = _ssh_idx       # 台词音频(IndexTTS2 港片念法):云机上跑 indextts 的 ssh


def _playwright_driver(profile: Path):
    from tools.refs import hgdriver
    return hgdriver.PlaywrightDriver(profile)


def _fetcher(root: Path, src: str, wid: int | None):
    from tools.refs.net import Fetcher
    return Fetcher(root, src, work_id=wid)


def real_llm() -> LLMBackend:
    return LLMBackend(ClaudeCLIClient(), service.DEFAULT_MODEL, "real")


def real_backend() -> Backend:
    return Backend("云机(真实)", lambda h: ComfyClient(h.local_endpoint), TunnelManager(), _ssh_run, llm=real_llm)


@dataclass
class HostStatus:
    tunnel_ok: bool = False
    comfy_ok: bool = False
    message: str = "还没检查过"
    running: int = 0
    pending: int = 0
    last_tick: str = ""
    last_lines: list[str] = field(default_factory=list)


def probe(host: Host, backend: Backend) -> HostStatus:
    if not backend.tunnel.ensure(host):
        return HostStatus(message=f"隧道未通(本机端口 {host.local_port})—— 检查云机是否开机、SSH 端口是否变了")
    try:
        running, pending = backend.client_for(host).queue_ids()
    except ComfyUnreachable as e:
        return HostStatus(tunnel_ok=True, message=f"隧道已通,但 ComfyUI 没响应 —— 云机上 ComfyUI 可能没启动({e})")
    return HostStatus(tunnel_ok=True, comfy_ok=True, running=len(running), pending=len(pending),
                      message=f"正常:ComfyUI 队列 运行 {len(running)} / 排队 {len(pending)}")


class Runtime:
    def __init__(self, store: Store, data_root: Path, backend: Backend, poll_sec: float = POLL_SEC,
                 refs=None, refs_root: Path | None = None, refs_poll_sec: float = 5.0, record_driver=None,
                 refs_fetcher=None, login_fn=None):
        self.store = store
        self.data_root = Path(data_root)
        self.backend = backend
        self.poll_sec = poll_sec
        self.status: dict[int, HostStatus] = {}
        self._workers: dict[int, Worker] = {}
        self.last_error = ""          # 后台线程最近一次异常,主机面板上显示
        self._llm_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="drama-llm")
        self._llm_jobs: list[Future] = []
        self._queue_lock = threading.Lock()   # 出队:判「没有在跑的」与「置 started」在同一把锁里(两个线程会同时来)
        self._queue_fails = 0                 # 连续几条队列项因 LLM 本身失败(断路器)
        self._flow_lock = threading.Lock()    # 全剧流程推进:网页线程(批准后)和后台线程(每轮)都会来,判「排没排过」要串行
        self._cut_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="drama-cut")
        self._cut_jobs: list[Future] = []
        self.previews: dict[int, Future] = {}      # 整集预览:集 → 后台任务(面板看在拼 / 失败原因)
        self.frontend_job: dict | None = None    # 「下载前端」的后台任务状态(工作流面板上显示)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.refs = refs                       # 参考库(spec 2026-11-07-参考库-design.md);None = 没配,参考库面板显示「未配置」
        self.refs_root = Path(refs_root) if refs_root else None
        self.refs_poll_sec = refs_poll_sec
        self.refs_errors: list[str] = []       # refs-fetch 线程最近 REFS_ERRORS_KEEP 条异常 / 清理错误(带时间),来源设置页显示,人点「清掉」才清
        self._refs_thread: threading.Thread | None = None
        # 红果录屏(有头浏览器,refs-record 线程)与扫码登录共用一个配置目录 `<refs_root>/profile`:同时只许一个用
        self.record_driver = record_driver or (lambda: _playwright_driver(self.refs_root / "profile"))
        # 录屏 / 扫码前问 robots 用的取页器(来源, 作品) -> Fetcher;测试与自测注入假网络
        self.refs_fetcher = refs_fetcher or (lambda src, wid: _fetcher(self.refs_root, src, wid))
        # 扫码登录 (配置目录, 剧号) -> 是否登录成功;默认真的有头浏览器,自测注入假的(不开浏览器)
        if login_fn is None:
            from tools.refs import hgdriver
            login_fn = hgdriver.login
        self.login_fn = login_fn
        self.refs_browser: str | None = None   # 谁占着配置目录:None / "record" / "login"
        self._browser_lock = threading.Lock()
        self.refs_login: dict | None = None    # 最近一次扫码登录:{"state": running / ok / failed, "at", "message"}
        self._record_thread: threading.Thread | None = None

    REFS_ERRORS_KEEP = 5

    def refs_fail(self, msg: str) -> None:
        self.refs_errors = (self.refs_errors + [f"{time.strftime('%m-%d %H:%M:%S')} {msg}"])[-self.REFS_ERRORS_KEEP:]

    def refs_tick(self) -> None:
        """参考库一轮:排任务 → 跑一条 → 到期清理(一天一次)。独立线程,不碰 GPU worker(spec 1.1 第 2 条)。"""
        if self.refs is None:
            return
        from tools.refs import monitor, retention, runner
        monitor.plan(self.refs)
        runner.run_one(self.refs, self.refs_root)
        res = retention.daily(self.refs, self.refs_root)
        for e in (res[1] if res else []):                # 删除失败要有人看见(来源设置页 + 待办)
            self.refs_fail(f"清理:{e}")

    def _claim_browser(self, who: str) -> str | None:
        """占配置目录;已被占就返回占着的那个,不占。"""
        with self._browser_lock:
            if self.refs_browser is not None:
                return self.refs_browser
            self.refs_browser = who
            return None

    def _release_browser(self) -> None:
        with self._browser_lock:
            self.refs_browser = None

    def refs_record_tick(self) -> None:
        """录屏一轮:取一条 record 任务录完(实时播放,一部剧几分钟)。正在扫码登录就先不录,下一轮再来。"""
        if self.refs is None:
            return
        from tools.refs import clock, jobs as ref_jobs, recorder
        job = ref_jobs.next_waiting(self.refs, clock.today(), kinds=("record",))
        if job is None or self._claim_browser("record") is not None:
            return
        try:
            recorder.run_job(self.refs, self.refs_root, job, self.record_driver, fetcher=self.refs_fetcher,
                             should_stop=self._stop.is_set)
        finally:
            self._release_browser()

    def start_login(self) -> None:
        """「打开浏览器扫码」:另起线程有头打开一部红果剧的播放页等人扫码(最多 5 分钟)。
        正在录屏 / 已经开着扫码浏览器 → ValueError(400)。登录框只在播放页弹,所以要库里已有一部红果作品。"""
        if self.refs is None:
            raise ValueError("没配参考库")
        row = self.refs.conn.execute("SELECT external_id FROM ref_work WHERE source = 'hongguo' AND blocked = 0"
                                     " ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            raise ValueError("参考库里还没有红果作品 —— 先拉一次红果榜单或贴一个作品链接,再扫码(登录框在播放页上才弹)")
        busy = self._claim_browser("login")
        if busy is not None:
            raise ValueError("正在录屏,录完再扫码" if busy == "record" else "扫码登录的浏览器已经开着,扫完再点")
        from tools.refs import clock
        self.refs_login = {"state": "running", "at": clock.now_iso(), "message": "等扫码(最多 5 分钟)"}

        def job():
            try:
                from tools.refs import hgdriver, loginstate, settings as ref_settings
                from tools.refs.net import FetchError
                url = hgdriver.player_url(row[0])
                try:                                        # 打开前问 robots(与录屏同一口径;读不到 robots 也当不让)
                    if not self.refs_fetcher("hongguo", None).allowed(url):
                        raise FetchError("disallowed", f"robots 禁止打开播放页 {url}")
                except FetchError as e:
                    if e.kind == "disallowed":
                        ref_settings.pause(self.refs, self.refs_root, "hongguo", str(e))
                    self.refs_login = {"state": "failed", "at": clock.now_iso(), "message": f"没打开浏览器:{e}"}
                    return
                ok = self.login_fn(self.refs_root / "profile", row[0])
                loginstate.record(self.refs, self.refs_root, ok, "扫码登录")
                self.refs_login = {"state": "ok" if ok else "failed", "at": clock.now_iso(),
                                   "message": "已登录" if ok else "5 分钟内没扫码登录,再点一次"}
            except Exception as e:     # 不吞:挂到参考库错误上、进 journal
                self.refs_login = {"state": "failed", "at": clock.now_iso(), "message": f"{type(e).__name__}: {e}"}
                self.refs_fail(f"扫码登录:{type(e).__name__}: {e}")
                journal.append(self.data_root, [f"参考库扫码登录异常:{type(e).__name__}: {e}"], prefix="runtime ")
            finally:
                self._release_browser()
        threading.Thread(target=job, name="refs-login", daemon=True).start()

    def _refs_idle(self, host, client) -> list[str]:
        """GPU worker 的空闲钩子:机器空着就跑一条参考库转写(spec 1.1 第 4 条,生产任务优先)。"""
        from tools.refs import transcribe
        try:
            line = transcribe.run_one(self.refs, self.refs_root, client, self.backend.align_for(host))
        except Exception as e:          # 不吞(任务已标失败、进参考库错误与 journal),也不让它冒出 worker.tick 拖垮这一轮
            msg = f"{type(e).__name__}: {e}"
            self.refs_fail(f"转写:{msg}")
            journal.append(self.data_root, [f"参考库转写异常:{msg}"], prefix="runtime ")
            return [f"参考库转写异常:{msg}"]
        return [line] if line else []

    def host_ids(self) -> list[int]:
        return [r[0] for r in self.store.conn.execute("SELECT id FROM host ORDER BY id")]

    def tick_all(self) -> None:
        if self.backend.before_tick:
            self.backend.before_tick()
        for hid in self.host_ids():
            w = self._workers.get(hid)
            if w is None:
                b = self.backend
                w = self._workers[hid] = Worker(self.store, hid, data_root=self.data_root,
                                                client_for=b.client_for, tunnel=b.tunnel, run_for=b.run_for,
                                                align_for=b.align_for, tts_for=b.tts_for, idx_for=b.idx_for,
                                                idle_hook=self._refs_idle if self.refs is not None else None)
            lines = journal.append(self.data_root, w.tick(), prefix=f"host={hid} ")
            host = get_host(self.store, hid)
            st = probe(host, self.backend)
            if st.comfy_ok and nodecache.stale(nodecache.meta(self.data_root)):
                lines += self._refresh_nodes(host)        # 云机连通、节点清单超过一天:顺手刷新离线编辑用的缓存
            st.last_tick = time.strftime("%H:%M:%S")
            st.last_lines = lines
            self.status[hid] = st
        moved = [self.advance_flow(did) for (did,) in
                 self.store.conn.execute("SELECT id FROM drama WHERE flow_auto = 1").fetchall()]
        if any(moved):                        # 出图完成后接四视图、资产定稿后拆分镜等,靠这里每轮推一次
            self.pump_queue()

    def advance_flow(self, drama_id: int) -> list[str]:
        """全剧流程排下一步(spec 2026-10-25-全剧审与资产表-design.md 第 4 节);没开自动推进的剧什么都不做。"""
        with self._flow_lock:
            did = flow.advance(self.store, drama_id)
        if did:
            journal.append(self.data_root, did, prefix="flow ")
        return did

    def _refresh_nodes(self, host: Host) -> list[str]:
        try:
            m = nodecache.refresh(self.data_root, self.backend.client_for(host), host.label)
        except ComfyUnreachable as e:
            return journal.append(self.data_root, [f"节点清单刷新失败:{e}"], prefix=f"host={host.id} ")
        return journal.append(self.data_root, [f"节点清单已刷新:{m['node_types']} 个节点类型,"
                                               f"云机要求前端 {m['required_frontend_version']}"], prefix=f"host={host.id} ")

    def refresh_object_info(self, host_id: int) -> dict:
        """主机面板「刷新节点清单」。隧道没通 / 连不上 ComfyUI 当场报错(400),不写半份缓存。"""
        host = get_host(self.store, host_id)
        if not self.backend.tunnel.ensure(host):
            raise ValueError(f"隧道未通(本机端口 {host.local_port})—— 先开云机、确认 SSH 端口")
        try:
            m = nodecache.refresh(self.data_root, self.backend.client_for(host), host.label)
        except ComfyUnreachable as e:
            raise ValueError(f"连不上云机的 ComfyUI:{e}") from e
        journal.append(self.data_root, [f"节点清单已刷新:{m['node_types']} 个节点类型"], prefix=f"host={host_id} ")
        return m

    def start_frontend_install(self, root) -> None:
        """后台下载 ComfyUI 前端(约 22 MB,核 sha256)。同时只跑一个。"""
        from tools.gen import frontend
        if self.frontend_job and self.frontend_job["state"] == "running":
            raise ValueError("前端正在下载,别重复点")
        self.frontend_job = {"state": "running", "message": f"正在下载前端 {frontend.VERSION}"}

        def job():
            try:
                d = frontend.install(root)
                self.frontend_job = {"state": "done", "message": f"已安装到 {d}"}
            except Exception as e:         # 不吞:失败原因显示在面板上、进 journal
                self.frontend_job = {"state": "failed", "message": f"{type(e).__name__}: {e}"}
            journal.append(self.data_root, [f"下载前端:{self.frontend_job['message']}"], prefix="runtime ")
        threading.Thread(target=job, name="drama-frontend", daemon=True).start()

    def any_host_ready(self) -> tuple[bool, str]:
        if not self.status:
            return False, "还没有登记云机(在「主机」面板添加)" if not self.host_ids() else "worker 还没跑完第一轮"
        ok = [s for s in self.status.values() if s.comfy_ok]
        return (True, ok[0].message) if ok else (False, next(iter(self.status.values())).message)

    def start_llm(self, task: str, target_id: int, params: dict) -> tuple[int, bool]:
        """开一条 LLM 任务。前提不满足 / LLM 不可用当场报错;有锁定版直接复用,不调 LLM。"""
        if self.backend.llm is None:
            raise LLMUnavailable("这个执行面没有配置 LLM")
        llm = self.backend.llm()
        rid, reused = service.begin(self.store, task, target_id, params, llm, self.data_root)
        if not reused:
            self._llm_jobs.append(self._llm_pool.submit(self._finish_llm, rid, llm))
        return rid, reused

    def _finish_llm(self, rid: int, llm: LLMBackend) -> None:
        kind: str | None = "error"
        try:
            kind = service.finish(self.store, rid, llm, self.data_root / "journal",   # → journal/llm/*.json
                                  data_root=self.data_root)
        except Exception as e:     # 不吞:落成 failed 带原因、进 journal(service 自己处理不了的意外)
            runs.fail(self.store, rid, f"{type(e).__name__}: {e}")
            journal.append(self.data_root, [f"llm_run {rid} 意外失败:{type(e).__name__}: {e}"], prefix="runtime ")
        self._after_llm(rid, kind)

    def _after_llm(self, rid: int, kind: str | None) -> None:
        """一条 LLM 运行结束:是队列项就给断路器计数,然后取下一条。手动发起的运行不计入、也不清零(spec 8.7)。"""
        with self._queue_lock:
            if llm_queue.item_for_run(self.store, rid) is not None:
                self._queue_fails = self._queue_fails + 1 if kind == "llm" else 0
                if self._queue_fails >= llm_queue.BREAK_AFTER:
                    err = runs.get(self.store, rid)["error"] or ""
                    reason = (f"连续 {self._queue_fails} 条 LLM 调用失败,队列已暂停 —— 最后一条:{err[:200]};"
                              "修好后点「继续」")
                    llm_queue.pause(self.store, reason)
                    journal.append(self.data_root, [reason], prefix="runtime ")
                    self._queue_fails = 0
        self.pump_queue()

    def pump_queue(self) -> None:
        """取下一条:没暂停、没有在跑的队列项,就开最早的 waiting。开运行当场失败(前提不满足)记下原因、接着取下一条;
        命中锁定版(不调 LLM)也接着取;真开了一条就停,等它结束时再来(spec 2026-10-24-逐集批量出剧本分镜 第 4 节)。"""
        with self._queue_lock:
            while not llm_queue.paused(self.store) and llm_queue.running_item(self.store) is None:
                item = llm_queue.next_waiting(self.store)
                if item is None:
                    return
                try:
                    rid, reused = self.start_llm(item["task"], item["target_id"], llm_queue.params(item))
                except LLMUnavailable as e:
                    llm_queue.pause(self.store, f"LLM 不可用,队列已暂停 —— {e};修好后点「继续」")
                    return
                except (ValueError, KeyError) as e:        # 单集问题(前提不满足 / 集被删了):不卡队列
                    llm_queue.mark_started(self.store, item["id"], None, str(e))
                    continue
                llm_queue.mark_started(self.store, item["id"], rid)
                if not reused:
                    return

    def wait_llm(self, timeout: float = 60) -> None:
        """测试用:等后台 LLM 任务全部结束。"""
        for f in self._llm_jobs:
            f.result(timeout=timeout)

    def start_export(self, episode_id: int, producer: str) -> int:
        """开一次成片导出。前提不满足当场报错(ValueError → 400);满足就后台跑。"""
        eid = export.begin(self.store, self.data_root, episode_id, producer)
        journal.append(self.data_root, [f"export {eid} 开始:episode {episode_id}"], prefix="runtime ")
        self._cut_jobs.append(self._cut_pool.submit(self._finish_export, eid))
        return eid

    def _finish_export(self, eid: int) -> None:
        try:
            export.run(self.store, self.data_root, eid)
            row = export.get(self.store, eid)
            journal.append(self.data_root, [f"export {eid} {row['state']}:{row['error'] or row['labeled_path']}"],
                           prefix="runtime ")
        except Exception as e:     # 不吞:落成 failed 带原因、进 journal
            export.fail(self.store, eid, f"{type(e).__name__}: {e}")
            journal.append(self.data_root, [f"export {eid} 失败:{type(e).__name__}: {e}"], prefix="runtime ")

    def start_preview(self, episode_id: int) -> None:
        """后台拼整集预览;同一集正在拼就不重复开。结果 / 失败原因留在 previews 里给面板看。"""
        cur = self.previews.get(episode_id)
        if cur is not None and not cur.done():
            return
        self.previews[episode_id] = f = self._cut_pool.submit(export.preview, self.store, self.data_root, episode_id)
        self._cut_jobs.append(f)

    def wait_exports(self, timeout: float = 300) -> None:
        """测试用:等后台导出全部结束。"""
        for f in self._cut_jobs:
            f.result(timeout=timeout)

    def start(self) -> None:
        n = export.fail_stale(self.store)
        if n:
            journal.append(self.data_root, [f"上次有 {n} 次导出没跑完,已标失败"], prefix="runtime ")
        n = runs.fail_stale(self.store)
        if n:
            journal.append(self.data_root, [f"上次有 {n} 次 LLM 运行没跑完,已标失败"], prefix="runtime ")
        self.pump_queue()          # 上次没跑完的队列:waiting 的接着跑(重启时在跑的那条已标失败,不重试)

        def loop():
            while not self._stop.is_set():
                try:
                    self.tick_all()
                    self.last_error = ""
                except Exception as e:   # 不吞:进 journal、挂到主机面板上;线程本身不能悄悄死掉
                    self.last_error = f"{time.strftime('%H:%M:%S')} {type(e).__name__}: {e}"
                    journal.append(self.data_root, [f"worker 本轮异常:{self.last_error}"], prefix="runtime ")
                self._stop.wait(self.poll_sec)
        self._thread = threading.Thread(target=loop, name="drama-worker", daemon=True)
        self._thread.start()

        if self.refs is not None:
            from tools.refs import jobs as ref_jobs, settings as ref_settings
            ref_settings.ensure(self.refs)
            n = ref_jobs.fail_stale(self.refs)
            if n:
                journal.append(self.data_root, [f"参考库:上次有 {n} 条任务(抓取 / 录屏 / 转写)没跑完,已标失败"], prefix="runtime ")

            def refs_loop():
                while not self._stop.is_set():
                    try:
                        self.refs_tick()
                    except Exception as e:     # 不吞:进 journal、挂到来源设置面板上(成功一轮也不清);线程不能悄悄死掉
                        self.refs_fail(f"{type(e).__name__}: {e}")
                        journal.append(self.data_root, [f"参考库本轮异常:{type(e).__name__}: {e}"], prefix="runtime ")
                    self._stop.wait(self.refs_poll_sec)
            self._refs_thread = threading.Thread(target=refs_loop, name="refs-fetch", daemon=True)
            self._refs_thread.start()

            def record_loop():
                while not self._stop.is_set():
                    try:
                        self.refs_record_tick()
                    except Exception as e:     # 不吞:recorder 已把任务标失败;这里挂到参考库错误上、进 journal
                        self.refs_fail(f"录屏:{type(e).__name__}: {e}")
                        journal.append(self.data_root, [f"参考库录屏异常:{type(e).__name__}: {e}"], prefix="runtime ")
                    self._stop.wait(self.refs_poll_sec)
            self._record_thread = threading.Thread(target=record_loop, name="refs-record", daemon=True)
            self._record_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=30)
        if self._refs_thread is not None:
            self._refs_thread.join(timeout=30)
        if self._record_thread is not None:      # 正在录的一集要播完才停;等不到就不等(守护线程)
            self._record_thread.join(timeout=30)
        self._llm_pool.shutdown(wait=False, cancel_futures=True)
        self._cut_pool.shutdown(wait=False, cancel_futures=True)
