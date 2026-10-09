# -*- coding: utf-8 -*-
"""估时口径:按已登记云机的卡算,几台卡不同时按最慢的一张(任务不绑机器,谁空谁领)。

没登记云机、或有云机没登记卡 —— **不猜**,返回无法估时与原因;估时只是提示,不挡任何操作。
"""
from __future__ import annotations

from dataclasses import dataclass

from tools.drama.db import Store
from tools.gen.capability import GPU_TIMINGS, MINIMAX_H3, Capability, GpuTiming


@dataclass(frozen=True)
class Basis:
    timing: GpuTiming | None
    note: str          # 「按 X 估算」或「无法估时:原因」—— 每处估时都把它带上

    def video(self, cap: Capability, frames: int, steps: int | None = None) -> float | None:
        return None if self.timing is None else self.timing.video(cap, frames, steps)

    def item(self, backend) -> float | None:
        return None if self.timing is None else self.timing.item(backend)


def basis(store: Store) -> Basis:
    rows = store.conn.execute("SELECT label, gpu FROM host ORDER BY id").fetchall()
    if not rows:
        return Basis(None, "无法估时:还没登记云机")
    missing = [r["label"] for r in rows if r["gpu"] is None]
    if missing:
        return Basis(None, f"无法估时:云机「{'」「'.join(missing)}」没登记 GPU 型号 —— 到「主机」面板登记")
    cards = {GPU_TIMINGS[r["gpu"]].gpu: GPU_TIMINGS[r["gpu"]] for r in rows}
    slowest = max(cards.values(), key=lambda t: t.video(MINIMAX_H3, 192))
    return Basis(slowest, f"按 {slowest.gpu} 估算" + ("(已登记云机里最慢的一张)" if len(cards) > 1 else ""))
