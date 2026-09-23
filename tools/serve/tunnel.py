# -*- coding: utf-8 -*-
"""SSH 隧道管理:把云机上只监听 127.0.0.1 的 ComfyUI 映射到本机端口。

**隧道由控制面自己起**:AutoDL 每次重启都换 SSH 端口,隧道若在应用之外,
界面上「改端口」就没有意义 —— 改完还得有人手工重建。

判活分两层,本模块只管第一层(**本机端口有没有在转发**);
「转发通不通、ComfyUI 活不活」是第二层,由调用方用 ComfyClient 判断。
**ssh 进程活着 ≠ 转发通**:云机关机时 ssh 可能僵着。

几条踩出来的规矩:
- 本机端口已经在转发(比如控制面重启前起的那条 ssh 还活着)就**接管**,不再起新的;
  否则新 ssh 会因 ExitOnForwardFailure 秒死,每轮生一条必死进程。
- 云机不可达时**退避**(5s 起翻倍、封顶 300s),不每轮都拉一条 ssh。
- 闸门 0 实测:**Windows 控制面上没有 `setsid`**。用 `subprocess.Popen` + DETACHED_PROCESS 脱离。
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
from collections.abc import Callable

from tools.serve.hosts import Host

BACKOFF_START = 5
BACKOFF_CAP = 300

# Windows:DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP —— 控制面退出时隧道不跟着被杀
_DETACH = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0


def command_for(host: Host) -> list[str]:
    return [
        "ssh", "-N",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ExitOnForwardFailure=yes",      # 端口占用时立刻失败,别假装成功
        "-o", "ServerAliveInterval=30",
        "-o", "BatchMode=yes",                 # 拿不到 tty 也不卡在交互提示上
        "-i", host.key_ref,
        "-p", str(host.ssh_port),
        "-L", f"{host.local_port}:127.0.0.1:{host.remote_comfy_port}",
        f"{host.ssh_user}@{host.ssh_host}",
    ]


def _port_forwarding(host: Host) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", host.local_port)) == 0


def _spawn(cmd: list[str]):
    return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=_DETACH)


class TunnelManager:
    def __init__(self, spawn: Callable[[list[str]], object] = _spawn,
                 forwarding: Callable[[Host], bool] = _port_forwarding,
                 clock: Callable[[], float] = time.monotonic):
        self._spawn = spawn
        self._forwarding = forwarding
        self._clock = clock
        self._procs: dict[int, object] = {}
        self._next_try: dict[int, float] = {}
        self._backoff: dict[int, int] = {}

    def ensure(self, host: Host) -> bool:
        """确保本机端口在转发。返回**此刻**是否在转发(刚拉起的 ssh 要过一会儿才通)。"""
        if self._forwarding(host):
            self._backoff.pop(host.id, None)
            self._next_try.pop(host.id, None)
            return True
        proc = self._procs.get(host.id)
        if proc is not None and proc.poll() is None:
            return False  # 我们起的 ssh 还活着,只是还没通,等它
        now = self._clock()
        if now < self._next_try.get(host.id, 0.0):
            return False  # 退避中
        self._procs[host.id] = self._spawn(command_for(host))
        wait = BACKOFF_START if host.id not in self._backoff else min(self._backoff[host.id] * 2, BACKOFF_CAP)
        self._backoff[host.id] = wait
        self._next_try[host.id] = now + wait
        return False

    def stop(self, host_id: int) -> None:
        """改端口前必须先停旧的:不停就换地址,旧隧道占着本机端口,新的起不来。"""
        proc = self._procs.pop(host_id, None)
        self._backoff.pop(host_id, None)
        self._next_try.pop(host_id, None)
        if proc is None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    def owns(self, host_id: int) -> bool:
        """这条隧道是不是我们起的。接管来的(控制面重启前起的)我们没有句柄,**杀不掉**,
        改端口时要如实告诉人去系统里结束那条 ssh,而不是静默地什么也没做。"""
        proc = self._procs.get(host_id)
        return proc is not None and proc.poll() is None
