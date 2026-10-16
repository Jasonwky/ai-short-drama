# -*- coding: utf-8 -*-
"""关键帧对比:这条视频钉的那张图 vs 视频那一帧的实际画面(设计 specs/2026-10-09-中间关键帧-design.md §二.3)。

- 图与帧号**从产出这条视频的任务参数 `anchors` 读**(不可变),不是段落当前的关键帧 —— 出片后换过的标 stale;
- 量法**沿用调研**(spike-results/2026-10-09-中间关键帧锚定实测.md):两边都直接缩到 72×128 灰度、平均绝对差。
  调研里「约 10 = 钉中、对照 50 以上」就是这么量的,所以参照数可以直接用。AddGuide 实际按视频比例中心裁切后缩放,
  两种宽高比差约 2%;为了参照数可用,**不换口径**;
- 结果按「视频内容 + 帧号 + 图内容」缓存在 `outputs/keycheck/`(本机 ffmpeg,确定性)。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from tools.cut import media
from tools.cut.provenance import cached_sha256
from tools.drama import repo
from tools.drama.db import Store
from tools.drama.readiness import key_frame_index
from tools.gen.capability import MINIMAX_H3

W, H = 72, 128
HIT, OTHER = 10, 50          # 调研参照:钉中约 10;不同画面 50 以上
CACHE = "outputs/keycheck"


@dataclass(frozen=True)
class KeyCheck:
    anchor_path: str             # 这条视频钉的那张图
    frame_idx: int
    frame_png: str | None        # 视频那一帧(相对素材根),给页面并排显示
    diff: float | None           # None = 算不出来,原因在 problem
    problem: str
    stale: bool                  # 出片后段落的关键帧换过(图或时刻)


def _gray(src: Path, select: str = "") -> bytes:
    vf = (f"{select}," if select else "") + f"scale={W}:{H},format=gray"
    return media._run(["ffmpeg", "-v", "error", "-i", str(src), "-vf", vf, "-vsync", "0", "-frames:v", "1",
                       "-f", "rawvideo", "-"], binary=True).stdout


def _diff(a: bytes, b: bytes) -> float:
    return round(sum(abs(x - y) for x, y in zip(a, b)) / len(a), 2)


def check(store: Store, root: Path, segment_id: int) -> KeyCheck | None:
    """段落当前视频钉了关键帧就给对比,没钉(或没视频)返回 None。"""
    root = Path(root)
    seg = repo.get(store, "segment", segment_id)
    if not seg["video_path"]:
        return None
    task = store.conn.execute("SELECT params_json FROM task WHERE kind = 'video' AND state = 'done' AND result_path = ?"
                              " ORDER BY id DESC LIMIT 1", (seg["video_path"],)).fetchone()
    keys = [(p, i) for p, i in json.loads(task["params_json"]).get("anchors", []) if i > 0] if task else []
    if not keys:
        return None
    path, idx = keys[0]
    stale = seg["key_frame_path"] != path or seg["key_time_sec"] is None or key_frame_index(seg["key_time_sec"], MINIMAX_H3) != idx

    def done(**kw) -> KeyCheck:
        return KeyCheck(path, idx, **{"frame_png": None, "diff": None, "problem": "", "stale": stale, **kw})

    video, image = root / seg["video_path"], root / path
    if not video.is_file():
        return done(problem=f"视频不在本机:{seg['video_path']}")
    if not image.is_file():
        return done(problem=f"关键帧图不在本机:{path}")
    name = f"{cached_sha256(video)[:16]}_{idx}_{cached_sha256(image)[:16]}"
    meta, png = root / CACHE / f"{name}.json", root / CACHE / f"{name}.png"
    if not meta.is_file():
        n = media.frame_count(video)
        if idx >= n:
            return done(problem=f"视频只有 {n} 帧,取不到第 {idx} 帧")
        png.parent.mkdir(parents=True, exist_ok=True)
        media._run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf", f"select=eq(n\\,{idx})", "-vsync", "0",
                    "-frames:v", "1", str(png)])
        meta.write_text(json.dumps({"diff": _diff(_gray(video, f"select=eq(n\\,{idx})"), _gray(image))}),
                        encoding="utf-8")
    return done(frame_png=png.relative_to(root).as_posix(), diff=json.loads(meta.read_text(encoding="utf-8"))["diff"])
