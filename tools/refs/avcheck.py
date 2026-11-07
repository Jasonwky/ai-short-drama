# -*- coding: utf-8 -*-
"""录屏的本机检查与加工(spec 1.1 第 3、4 条、3.1):时长、音量、画面;重封装 + 裁到视频区域;关键帧;转 16 kHz PCM。

只调本机 ffmpeg / ffprobe,找不到或失败一律抛 RuntimeError(和 tools/cut 一样不吞)。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

DURATION_TOL = 2.0        # 录出的时长与片长相差不超过 2 秒(spec 3.1)
SILENT_DB = -50.0         # volumedetect mean_volume 低于它 = 静音(调研实测正常片 −17.9 dB)
LUMA_DARK, LUMA_BRIGHT, LUMA_FLAT = 16.0, 235.0, 6.0     # 抽帧平均亮度在 (16, 235) 之间、标准差 > 6 才算有画面
HEAD_SEC, HEAD_STEP, BODY_STEP = 3.0, 0.5, 10.0          # 关键帧:开头 0–3 秒每 0.5 秒,之后每 10 秒(spec 1.1 第 4 条)
TAIL_SEC = 0.05           # 离片尾不到这么近的时刻没有帧可抽(最后一帧在 片长 − 1/fps)


def _bin(name: str) -> str:
    p = shutil.which(name)
    if not p:
        raise RuntimeError(f"找不到 {name} —— 本机要装 ffmpeg(录屏检查与关键帧都要它)")
    return p


def _run(args: list[str]) -> str:
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"{Path(args[0]).name} 失败:{r.stderr.strip()[-400:]}")
    return r.stdout + r.stderr


def duration(path: Path) -> float:
    out = _run([_bin("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)])
    return float(json.loads(out[out.index("{"):])["format"]["duration"])


def frame_size(path: Path) -> tuple[int, int]:
    """第一条视频流的宽高(像素)。录屏裁剪要按它把网页上的视频区域换算成像素。"""
    out = _run([_bin("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", str(path)])
    s = json.loads(out[out.index("{"):])["streams"]
    if not s:
        raise RuntimeError(f"{Path(path).name} 里没有视频流")
    return int(s[0]["width"]), int(s[0]["height"])


def mean_volume(path: Path) -> float | None:
    out = _run([_bin("ffmpeg"), "-hide_banner", "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"])
    m = re.search(r"mean_volume:\s*(-?[\d.]+|-inf) dB", out)
    return None if not m or m.group(1) == "-inf" else float(m.group(1))


def luma_at(path: Path, sec: float) -> tuple[float, float]:
    """第 sec 秒那一帧的平均亮度与标准差(缩到 64×64 灰度像素算)。"""
    raw = subprocess.run([_bin("ffmpeg"), "-v", "error", "-ss", f"{sec:.2f}", "-i", str(path), "-frames:v", "1",
                          "-vf", "scale=64:64,format=gray", "-f", "rawvideo", "-"], capture_output=True)
    if raw.returncode != 0 or len(raw.stdout) < 64 * 64:
        raise RuntimeError(f"第 {sec:.1f} 秒抽不出帧:{raw.stderr.decode('utf-8', 'replace')[-300:]}")
    px = raw.stdout[:64 * 64]
    mean = sum(px) / len(px)
    sd = (sum((p - mean) ** 2 for p in px) / len(px)) ** 0.5
    return mean, sd


def recording_problems(path: Path, expected_sec: float) -> tuple[list[str], dict]:
    """录屏三项检查(spec 3.1)。返回 (问题列表, 量到的数)。空列表 = 通过。"""
    got = duration(path)
    vol = mean_volume(path)
    mid = min(got / 2, max(got - 1, 0))
    mean, sd = luma_at(path, mid)
    meta = {"expected_sec": expected_sec, "duration": round(got, 2), "mean_volume_db": vol, "luma_mean": round(mean, 1), "luma_sd": round(sd, 1)}
    p = []
    if abs(got - expected_sec) > DURATION_TOL:
        p.append(f"时长对不上:录到 {got:.1f} 秒,片长 {expected_sec:.1f} 秒(允许 ±{DURATION_TOL:g} 秒)")
    if vol is None or vol < SILENT_DB:
        p.append(f"没有声音:平均音量 {vol if vol is not None else '-inf'} dB(低于 {SILENT_DB:g} dB 算静音)")
    if not (LUMA_DARK < mean < LUMA_BRIGHT) or sd <= LUMA_FLAT:
        p.append(f"画面不对:第 {mid:.0f} 秒那一帧平均亮度 {mean:.0f}、起伏 {sd:.1f}(黑屏 / 白屏 / 纯色)")
    return p, meta


def remux_crop(src: Path, dst: Path, box: tuple[int, int, int, int] | None) -> None:
    """MediaRecorder 的 webm 没写时长:重封装;给了视频区域 (x, y, w, h)(像素,偶数)就裁到它(要重编码 vp9)。"""
    if box is None:
        _run([_bin("ffmpeg"), "-v", "error", "-y", "-i", str(src), "-c", "copy", str(dst)])
        return
    x, y, w, h = box
    _run([_bin("ffmpeg"), "-v", "error", "-y", "-i", str(src), "-vf", f"crop={w}:{h}:{x}:{y}",
          "-c:v", "libvpx-vp9", "-b:v", "2M", "-c:a", "copy", str(dst)])


def keyframe_times(total: float) -> list[float]:
    head = [round(i * HEAD_STEP, 1) for i in range(int(HEAD_SEC / HEAD_STEP) + 1) if i * HEAD_STEP < total - TAIL_SEC]
    body = [float(t) for t in range(int(BODY_STEP), int(total), int(BODY_STEP))]
    return head + body


def extract_frame(src: Path, sec: float, dst: Path) -> None:
    _run([_bin("ffmpeg"), "-v", "error", "-y", "-ss", f"{sec:.2f}", "-i", str(src), "-frames:v", "1", "-q:v", "3", str(dst)])
    if not Path(dst).is_file():               # -ss 超过最后一帧时 ffmpeg 退出码 0 但不出图
        raise RuntimeError(f"第 {sec:.2f} 秒抽不出关键帧({Path(src).name})")


def pcm16k(src: Path) -> bytes:
    r = subprocess.run([_bin("ffmpeg"), "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                       capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"转音频失败:{r.stderr.decode('utf-8', 'replace')[-300:]}")
    return r.stdout
