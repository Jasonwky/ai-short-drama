# -*- coding: utf-8 -*-
"""常驻 worker:一台云机一个。每轮做三件事 —— 保隧道、推进在途任务、空闲时派下一条。

- **每台机器同时只交一条**:ComfyUI 本来就串行跑,多交只是在它的队列里排队;
  留在我们的库里排,才能按模型分组挑、才能在交出去之前取消。这台 ComfyUI 还是多个项目共用的。
- **每轮重读 host**:SSH 端口在界面上改了,下一轮就按新端口重建隧道。
  接管来的隧道(不是本进程起的)杀不掉,如实提示人去结束它,不假装已处理。
- 没有单独的「恢复」流程:重启后库里的在途任务带着 external_id,照常推进就是接管。

用法:
    python -m tools.serve.worker --db data/drama.db --host-id 1 --data-root data --once
    python -m tools.serve.worker --db data/drama.db --host-id 1 --data-root data --loop
"""
from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from pathlib import Path

from tools.drama.db import Store
from tools.gen import preflight
from tools.gen.comfy_client import ComfyClient
from tools.serve.collect import IN_FLIGHT, Collector
from tools.serve import journal
from tools.serve.dispatch import dispatch, pick_next
from tools.serve.hosts import Host, get_host
from tools.serve.tunnel import TunnelManager

POLL_SEC = 10


def _ssh_run(host: Host) -> preflight.Runner:
    return preflight.ssh_runner(host.ssh_host, host.ssh_port, host.ssh_user, host.key_ref)


class Worker:
    def __init__(self, store: Store, host_id: int, *, data_root: Path,
                 client_for: Callable[[Host], object] = lambda h: ComfyClient(h.local_endpoint),
                 tunnel: TunnelManager | None = None,
                 run_for: Callable[[Host], preflight.Runner] = _ssh_run,
                 collector: Collector | None = None):
        self.store = store
        self.host_id = host_id
        self.data_root = Path(data_root)
        self.client_for = client_for
        self.tunnel = tunnel or TunnelManager()
        self.run_for = run_for
        self.collector = collector or Collector(self.data_root)
        self._last_host: Host | None = None

    def tick(self) -> list[str]:
        log: list[str] = []
        host = get_host(self.store, self.host_id)
        if self._last_host is not None and self._last_host != host:
            owned = self.tunnel.owns(host.id)
            self.tunnel.stop(host.id)
            if not owned:
                log.append("host 配置变了,但当前隧道不是本进程起的,杀不掉 —— 请手工结束旧的 ssh -L 进程")
            else:
                log.append("host 配置变了,已停掉旧隧道,按新配置重建")
        self._last_host = host

        if not self.tunnel.ensure(host):
            log.append(f"隧道未通(本机端口 {host.local_port}),在途任务按连不上处理")
        client = self.client_for(host)

        inflight = self.store.conn.execute(
            f"SELECT * FROM task WHERE host_id = ? AND state IN ({','.join('?' * len(IN_FLIGHT))}) ORDER BY id",
            (host.id, *IN_FLIGHT)).fetchall()
        for task in inflight:
            out = self.collector.advance(self.store, client, task)
            log.append(f"task {task['id']}: {task['state']} → {out.state} {out.message}".rstrip())

        busy = self.store.conn.execute(
            f"SELECT COUNT(*) FROM task WHERE host_id = ? AND state IN ({','.join('?' * len(IN_FLIGHT))})",
            (host.id, *IN_FLIGHT)).fetchone()[0]
        if busy == 0:
            task = pick_next(self.store, host.id)
            if task is not None:
                out = dispatch(self.store, client, host, task, data_root=self.data_root, run=self.run_for(host))
                log.append(f"task {task['id']}: pending → {out.state} {out.message}".rstrip())
                log += [f"task {task['id']} 告警: {w}" for w in out.warnings]
        return log


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AI 短剧生成 worker(一台云机一个)")
    ap.add_argument("--db", required=True)
    ap.add_argument("--host-id", type=int, required=True)
    ap.add_argument("--data-root", required=True, help="素材与产物的根目录;库里的路径都相对它")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="只跑一轮")
    mode.add_argument("--loop", action="store_true", help=f"常驻,每 {POLL_SEC}s 一轮")
    args = ap.parse_args(argv)

    data_root = Path(args.data_root)
    worker = Worker(Store(args.db), args.host_id, data_root=data_root)
    while True:
        lines = journal.append(data_root, worker.tick(), prefix=f"host={args.host_id} ")
        if lines:
            print("\n".join(lines), flush=True)
        if args.once:
            return 0
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    sys.exit(main())
