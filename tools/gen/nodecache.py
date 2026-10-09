# -*- coding: utf-8 -*-
"""云机节点清单(`/object_info`)的本机缓存 —— 离线编辑工作流的命门(设计 §2.3)。

它决定画布里每个节点长什么样、有哪些参数、模型下拉里有哪些文件。云机开着时抓一份存在
`<data_root>/comfy_cache/`,关机后编辑页用它。缓存会过期(云机装 / 卸节点或模型后),所以:
- 记下抓取时间、来源云机、云机要求的前端版本,编辑页上常驻显示;
- worker 发现云机连通、缓存超过一天就自动刷一次;
- 从没抓过 → 明确报错,不拿空清单糊弄(空清单下我们的节点全会显示成「未知节点」)。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

DIR = "comfy_cache"
MAX_AGE = timedelta(days=1)


def _dir(data_root: Path) -> Path:
    return Path(data_root) / DIR


def meta(data_root: Path) -> dict | None:
    f = _dir(data_root) / "meta.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None


def load(data_root: Path) -> dict:
    f = _dir(data_root) / "object_info.json"
    if not f.is_file():
        raise ValueError("还没有节点清单缓存 —— 先开云机,在「主机」面板点「刷新节点清单」")
    return json.loads(f.read_text(encoding="utf-8"))


def refresh(data_root: Path, client, host_label: str) -> dict:
    """从云机抓节点清单与版本信息,写缓存,返回元数据。抓不到由 client 抛(连不上就是连不上,不写半份)。"""
    info = client.object_info()
    stats = client.system_stats()["system"]
    body = json.dumps(info, ensure_ascii=False, sort_keys=True).encode("utf-8")
    m = {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "host": host_label,
         "comfyui_version": stats.get("comfyui_version"), "required_frontend_version": stats.get("required_frontend_version"),
         "node_types": len(info), "sha256": hashlib.sha256(body).hexdigest()}
    d = _dir(data_root)
    d.mkdir(parents=True, exist_ok=True)
    (d / "object_info.json.tmp").write_bytes(body)
    (d / "object_info.json.tmp").replace(d / "object_info.json")
    (d / "meta.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    return m


def stale(m: dict | None, now: datetime | None = None) -> bool:
    if m is None:
        return True
    now = now or datetime.now(timezone.utc)
    return now - datetime.fromisoformat(m["fetched_at"]) > MAX_AGE


def default_of(object_info: dict):
    """给 graphs.diff 用:某个节点某个输入在清单里的默认值(没有就 None)。"""
    def get(cls: str, name: str):
        spec = object_info.get(cls, {}).get("input", {})
        spec = {**spec.get("required", {}), **spec.get("optional", {})}.get(name)
        return spec[1].get("default") if spec and len(spec) > 1 and isinstance(spec[1], dict) else None
    return get
