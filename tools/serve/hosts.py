# -*- coding: utf-8 -*-
"""执行面机器(云机)的登记。

- **私钥不入库**:只存 `key_ref`(密钥文件路径)。拿着私钥能在云机上做任何事,
  它不能出现在数据库、日志和 git 里。
- **SSH 端口可改**:AutoDL 每次重启都会换端口,改端口是日常操作。
- `models_dir` 是云机上 ComfyUI 模型根目录,LoRA 预检要按它定位文件。
"""
from __future__ import annotations

from dataclasses import dataclass

from tools.drama.db import Store


@dataclass(frozen=True)
class Host:
    id: int
    label: str
    ssh_host: str
    ssh_port: int
    ssh_user: str
    key_ref: str
    local_port: int
    remote_comfy_port: int
    vram_gb: float
    models_dir: str

    @property
    def local_endpoint(self) -> str:
        """ComfyClient 该连的地址。客户端只认本地端口,不知道隧道存在。"""
        return f"127.0.0.1:{self.local_port}"


def _check_key_ref(key_ref: str) -> None:
    if "PRIVATE KEY" in key_ref or "\n" in key_ref.strip():
        raise ValueError("key_ref 看起来是密钥内容而不是文件路径 —— 私钥不许入库")


def add_host(store: Store, *, label: str, ssh_host: str, ssh_port: int, ssh_user: str, key_ref: str,
             local_port: int, vram_gb: float, models_dir: str, remote_comfy_port: int = 8188) -> int:
    _check_key_ref(key_ref)
    if not models_dir.startswith("/"):
        raise ValueError(f"models_dir 必须是云机上的绝对路径,给的是 {models_dir!r}")
    with store.write() as c:
        return c.execute(
            "INSERT INTO host (label, ssh_host, ssh_port, ssh_user, key_ref, local_port,"
            " remote_comfy_port, vram_gb, models_dir) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (label, ssh_host, ssh_port, ssh_user, key_ref, local_port, remote_comfy_port,
             vram_gb, models_dir)).lastrowid


def get_host(store: Store, host_id: int) -> Host:
    row = store.conn.execute("SELECT * FROM host WHERE id = ?", (host_id,)).fetchone()
    if row is None:
        raise KeyError(f"host {host_id} 不存在")
    return Host(**{k: row[k] for k in row.keys()})


def set_ssh_port(store: Store, host_id: int, ssh_port: int) -> None:
    with store.write() as c:
        if c.execute("UPDATE host SET ssh_port = ? WHERE id = ?", (ssh_port, host_id)).rowcount == 0:
            raise KeyError(f"host {host_id} 不存在")
