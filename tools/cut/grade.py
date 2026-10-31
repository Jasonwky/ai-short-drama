# -*- coding: utf-8 -*-
"""导出调色(spec 2026-10-31-港片画风-design.md §2):只在导出编码前逐段套,各类检测仍吃原片。

hk90 = 实验调色链 `tmp/realism_ab/wxgrade.sh` 固化(spike-results/2026-10-31-降低AI感调研.md §6–7):
高光提取模糊后 screen 叠回(泛光)→ 抬黑压白 → 暗部偏青蓝 / 高光偏暖橙 → 降饱和 → 先缩后放降一档清晰度 → 时域颗粒 → 暗角。
踩坑:blend 在 YUV 下对色度平面做 screen 会整片偏洋红,必须先转 gbrp。参数是一次手调(n=1),**未标定**。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

GRADES = {
    "hk90": ("[0:v]format=gbrp,split[base][hl];"
             "[hl]curves=all='0/0 0.6/0 1/1',gblur=sigma=22[glow];"
             "[base][glow]blend=all_mode=screen:all_opacity=0.5,"
             "curves=all='0/0.06 0.5/0.48 1/0.95',"
             "colorbalance=rs=-0.10:gs=-0.02:bs=0.12:rm=0.02:gm=0.0:bm=-0.02:rh=0.10:gh=0.04:bh=-0.10,"
             "eq=saturation=0.9,"
             "scale=iw/2:-2,scale=iw*2:-2,gblur=sigma=0.5,"
             "noise=alls=20:allf=t+u,"
             "vignette=PI/4.5,format=yuv420p[v]"),
}


def filters(key: str) -> str:
    if key not in GRADES:
        raise KeyError(f"未知调色预设 {key!r},可选:{sorted(GRADES)}")
    return GRADES[key]


def apply(src: Path, out: Path, key: str) -> None:
    """只动画面,音轨原样拷贝;ffmpeg 失败抛 CalledProcessError(不吞)。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-filter_complex", filters(key), "-map", "[v]",
                    "-map", "0:a?", "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-c:a", "copy", str(out)],
                   check=True, capture_output=True)


def measure(path: Path, at: float = 1.0) -> dict:
    """一帧的平均饱和度与亮度低位(signalstats 的 SATAVG / YLOW),读回自检用。"""
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(path), "-frames:v", "1",
                        "-vf", "signalstats,metadata=print:file=-", "-f", "null", "-"],
                       check=True, capture_output=True, text=True)
    kv = dict(line.split("=", 1) for line in p.stdout.splitlines() if line.startswith("lavfi.") and "=" in line)
    return {"sat": float(kv["lavfi.signalstats.SATAVG"]), "ylow": float(kv["lavfi.signalstats.YLOW"])}
