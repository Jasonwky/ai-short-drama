# -*- coding: utf-8 -*-
"""切镜计数:H3 在一段 8 秒里会自己切 1–3 次镜头(提示词控制不了,2026-10-14 实测),切镜之后正是背景换屋、
帽子丢失这类穿帮的高发处 —— 把切镜时刻和切后那一帧摆到视频卡片上给人看。只提示,不拦导出。

判据(`spike-results/2026-10-22-内容与镜头不违和的保障方案.md` 标定节,实战 25 条片 4435 对相邻帧):
72×128 灰度相邻帧平均绝对差 > THRESHOLD 算硬切,连续几帧都过线算同一次。> 30 的 45 对几乎都是单帧尖峰;
快动作(转身)是 20–30 的连续多帧,25–30 之间只有 3 对。量法与 `keycheck`、中间关键帧调研同一套。

结果按视频 sha256 缓存在视频旁(`<视频>.cuts.json`,与 `blankscan` 同一做法);首帧、末帧、每次切后那一帧
存成 PNG(`outputs/cuts/`,与 `keycheck` 同一做法),看片时并排看接缝。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from tools.cut import media
from tools.serve.dispatch import sha256_file

W, H = 72, 128
THRESHOLD = 30
PARAMS = {"w": W, "h": H, "threshold": THRESHOLD}
PNG_DIR = "outputs/cuts"


@dataclass(frozen=True)
class Cut:
    frame: int          # 切过去之后的第一帧
    sec: float
    diff: float


@dataclass(frozen=True)
class Report:
    cuts: list[Cut]
    first_png: str      # 相对素材根
    last_png: str
    cut_pngs: list[str] = field(default_factory=list)


def _fps(path: Path) -> float:
    p = media._run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                    "-of", "json", str(path)])
    num, den = (int(x) for x in json.loads(p.stdout)["streams"][0]["r_frame_rate"].split("/"))
    return num / den


def detect(path: Path) -> list[Cut]:
    import numpy as np
    raw = media._run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"scale={W}:{H},format=gray", "-an",
                      "-f", "rawvideo", "-"], binary=True).stdout
    if not raw:
        raise media.CutError(f"切镜计数没从 {path} 读出任何帧")
    f = np.frombuffer(raw, np.uint8).reshape(-1, H, W).astype(np.int16)
    d = np.abs(f[1:] - f[:-1]).mean(axis=(1, 2))
    fps = _fps(path)
    out: list[Cut] = []
    for i, v in enumerate(d, 1):            # i = 后一帧的下标
        if v <= THRESHOLD or (i >= 2 and d[i - 2] > THRESHOLD):     # 前一对也过线:同一次切换
            continue
        out.append(Cut(i, round(i / fps, 2), round(float(v), 1)))
    return out


def _png(path: Path, dest: Path, select: list[str]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    media._run(["ffmpeg", "-v", "error", "-y", *select[:-1], "-i", str(path), "-vf", select[-1],
                "-frames:v", "1", str(dest)])


def _cache_path(root: Path, video_rel: str) -> Path:
    return Path(root) / f"{video_rel}.cuts.json"


def cached(root: Path, video_rel: str) -> Report | None:
    p = _cache_path(root, video_rel)
    if not p.is_file():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    if data["video_sha256"] != sha256_file(Path(root) / video_rel) or data["params"] != PARAMS:
        return None
    r = data["report"]
    if not all((Path(root) / x).is_file() for x in (r["first_png"], r["last_png"], *r["cut_pngs"])):
        return None
    return Report([Cut(**c) for c in r["cuts"]], r["first_png"], r["last_png"], r["cut_pngs"])


def check(root: Path, video_rel: str) -> Report:
    """查一段视频;有缓存读缓存,没有就跑 ffmpeg、出缩略图并写缓存。"""
    got = cached(root, video_rel)
    if got is not None:
        return got
    video = Path(root) / video_rel
    sha = sha256_file(video)
    found = detect(video)
    stem = f"{PNG_DIR}/{sha[:16]}"
    scale = "scale=270:-2"
    _png(video, Path(root) / f"{stem}_first.png", [scale])
    _png(video, Path(root) / f"{stem}_last.png", ["-sseof", "-0.1", scale])
    pngs = []
    for c in found:
        rel = f"{stem}_{c.frame}.png"
        _png(video, Path(root) / rel, [f"select=eq(n\\,{c.frame}),{scale}"])
        pngs.append(rel)
    rep = Report(found, f"{stem}_first.png", f"{stem}_last.png", pngs)
    _cache_path(root, video_rel).write_text(
        json.dumps({"video_sha256": sha, "params": PARAMS, "report": asdict(rep)}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return rep
