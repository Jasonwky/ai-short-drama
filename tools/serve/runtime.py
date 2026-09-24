# -*- coding: utf-8 -*-
"""控制台运行时:库 + 素材根目录 + 执行面后端 + 后台 worker 线程 + 主机判活。

- 每台登记的云机一个 `Worker`,由一个后台线程轮流 `tick`(与命令行 worker 同一套代码);
  每轮的动作经 `journal.append` 留痕。
- **判活分两层,界面上分开说**(设计 §四):隧道没通 / 隧道通了但 ComfyUI 没响应。
  P2 真链路实测:云机开机后 ComfyUI **不会自启**,只看隧道会把「ComfyUI 没起」误报成正常。
- `Backend` 决定怎么连执行面。正式入口只用真实后端;假后端只给测试与 Playwright 自测。
- LLM 任务(一次 30–80 秒)放进**单线程**池在后台跑(M-21 落盘单线程),页面立刻返回、自动刷新看结果。
  真实后端点按钮时才读 key:没有 key 就明确报「LLM 不可用」,**不静默切假 LLM**。
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from tools.drama.db import Store
from tools.gen import preflight
from tools.gen.comfy_client import ComfyClient, ComfyUnreachable
from tools.llm import runs, service
from tools.llm.client import ChatClient, LLMUnavailable, read_key
from tools.llm.service import LLMBackend
from tools.serve import journal
from tools.serve.hosts import Host, get_host
from tools.serve.tunnel import TunnelManager
from tools.serve.worker import POLL_SEC, Worker, _ssh_run


@dataclass
class Backend:
    label: str
    client_for: Callable[[Host], object]
    tunnel: object
    run_for: Callable[[Host], preflight.Runner]
    fake: bool = False
    before_tick: Callable[[], None] | None = None   # 假 GPU 在这里「跑完」队列
    llm: Callable[[], LLMBackend] | None = None     # 点「LLM 生成」时才调用


def real_llm() -> LLMBackend:
    return LLMBackend(ChatClient(read_key()), service.DEFAULT_MODEL, "real")


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
    def __init__(self, store: Store, data_root: Path, backend: Backend, poll_sec: float = POLL_SEC):
        self.store = store
        self.data_root = Path(data_root)
        self.backend = backend
        self.poll_sec = poll_sec
        self.status: dict[int, HostStatus] = {}
        self._workers: dict[int, Worker] = {}
        self.last_error = ""          # 后台线程最近一次异常,主机面板上显示
        self._llm_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="drama-llm")
        self._llm_jobs: list[Future] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

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
                                                client_for=b.client_for, tunnel=b.tunnel, run_for=b.run_for)
            lines = journal.append(self.data_root, w.tick(), prefix=f"host={hid} ")
            st = probe(get_host(self.store, hid), self.backend)
            st.last_tick = time.strftime("%H:%M:%S")
            st.last_lines = lines
            self.status[hid] = st

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
        rid, reused = service.begin(self.store, task, target_id, params, llm)
        if not reused:
            self._llm_jobs.append(self._llm_pool.submit(self._finish_llm, rid, llm))
        return rid, reused

    def _finish_llm(self, rid: int, llm: LLMBackend) -> None:
        try:
            service.finish(self.store, rid, llm, self.data_root / "journal")   # → journal/llm/*.json
        except Exception as e:     # 不吞:落成 failed 带原因、进 journal(service 自己处理不了的意外)
            runs.fail(self.store, rid, f"{type(e).__name__}: {e}")
            journal.append(self.data_root, [f"llm_run {rid} 意外失败:{type(e).__name__}: {e}"], prefix="runtime ")

    def wait_llm(self, timeout: float = 60) -> None:
        """测试用:等后台 LLM 任务全部结束。"""
        for f in self._llm_jobs:
            f.result(timeout=timeout)

    def start(self) -> None:
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

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=30)
        self._llm_pool.shutdown(wait=False, cancel_futures=True)
