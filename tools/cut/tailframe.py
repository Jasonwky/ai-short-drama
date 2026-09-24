# -*- coding: utf-8 -*-
"""尾帧衔接(设计 §6.6):本段当前视频的最后一帧 → 下一段的首帧。保证**物理连续**,不是「看起来像」。

- 三种前置条件各给可操作的原因:本段没有视频(或文件不在本机)/ 本段是最后一段 / 本机没有 ffmpeg;
- 抽帧是控制面本地的确定性操作,记成一条**创建即完成**的 task(`tasks.record_local`):
  溯源链能从下一段的视频一路追回到上一段的视频;抽出来的帧也进历史,人可以换回原来的首帧;
- 下一段已有视频时,换首帧会让那段视频作废 → plan/confirm 两态,先报代价再落盘(与「用这一版」同一套)。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tools.cut import media
from tools.drama import repo
from tools.drama.db import Store
from tools.drama.invalidation import Impact
from tools.drama.tasks import record_local
from tools.gen.capability import MINIMAX_H3
from tools.serve.dispatch import sha256_file

MODEL = "ffmpeg:last-frame"


@dataclass(frozen=True)
class TailPlan:
    source_id: int
    target_id: int
    video_path: str
    impact: Impact

    def summary(self) -> str:
        return self.impact.summary()


def plan_tail(store: Store, data_root: Path, segment_id: int) -> TailPlan:
    seg = repo.get(store, "segment", segment_id)
    if not seg["video_path"]:
        raise ValueError(f"段落 {seg['number']} 还没有视频 —— 先在「视频」面板生成,再抽尾帧")
    if not (Path(data_root) / seg["video_path"]).is_file():
        raise ValueError(f"段落 {seg['number']} 的视频文件不在本机:{seg['video_path']}")
    nxt = store.conn.execute("SELECT * FROM segment WHERE episode_id = ? AND number > ? ORDER BY number LIMIT 1",
                             (seg["episode_id"], seg["number"])).fetchone()
    if nxt is None:
        raise ValueError(f"段落 {seg['number']} 是本集最后一段,没有下一段可以接")
    media.require_ffmpeg()
    if nxt["video_path"]:
        imp = Impact((nxt["id"],), 0, 1, MINIMAX_H3.est_seconds(MINIMAX_H3.frames_for(nxt["duration_sec"])))
    else:
        imp = Impact((), 0, 0, 0.0)
    return TailPlan(segment_id, nxt["id"], seg["video_path"], imp)


def confirm_tail(store: Store, data_root: Path, plan: TailPlan) -> int:
    root = Path(data_root)
    src = root / plan.video_path
    sha = sha256_file(src)
    rel = f"outputs/tailframes/{sha[:16]}.png"          # 按源视频内容寻址:同一个视频抽几次都是同一张
    if not (root / rel).is_file():
        media.extract_last_frame(src, root / rel)
    number = repo.get(store, "segment", plan.source_id)["number"]
    tid = record_local(store, kind="image", target_type="segment", target_id=plan.target_id, model=MODEL,
                       params={"workflow": "last_frame", "source_segment": plan.source_id,
                               "source_sha256": sha, "writes": "first_frame_path", "expect_ext": ".png"},
                       prompt=f"段落 {number} 视频的最后一帧", refs=[plan.video_path],
                       result_path=rel, result_sha256=sha256_file(root / rel))
    if plan.impact.segment_ids:
        with store.write() as c:
            c.execute("UPDATE segment SET video_path = NULL, subtitle_path = NULL WHERE id = ?", (plan.target_id,))
    repo.update(store, "segment", plan.target_id, first_frame_path=rel)
    return tid
