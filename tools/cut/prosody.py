# -*- coding: utf-8 -*-
"""韵律量法(spike-results/2026-10-31-降低AI感调研.md §12 同一套):出声占比、音高起伏(p10–p90,半音)。

念法模板入库前代码判(spec 2026-10-31-IndexTTS2港片配音-design.md §9 V2):阈值取自参考句实测(出声 99 %、起伏 18.8 半音)
与用户选定版(86 %、16.8 半音),**暂定**,前几集出片后重标。音高用自相关粗估,只看量级,不当精确音高。
"""
from __future__ import annotations

import wave

import numpy as np

MIN_OCCUPANCY = 0.85
MIN_RANGE_ST = 15.0


def _frames(x: np.ndarray, sr: int, win: float, hop: float):
    n, h = int(win * sr), int(hop * sr)
    for i in range(0, max(len(x) - n, 0), h):
        yield x[i:i + n]


def occupancy(x: np.ndarray, sr: int, thr_db: float = -35.0) -> float:
    """响度超过 thr_db 的 20 ms 帧占比。"""
    e = [20 * np.log10(np.sqrt((f ** 2).mean()) + 1e-9) for f in _frames(x, sr, 0.02, 0.02)]
    return float(np.mean(np.array(e) > thr_db)) if e else 0.0


def pitch_range_st(x: np.ndarray, sr: int) -> float:
    """有声帧的音高(自相关,65–450 Hz)相对中位数的 p10–p90 跨度,单位半音;有声帧不足 5 个 → 0。"""
    lo, hi = int(sr / 450), int(sr / 65)
    f0 = []
    for f in _frames(x, sr, 0.04, 0.01):
        f = f - f.mean()
        if np.sqrt((f ** 2).mean()) < 0.02:
            continue
        ac = np.correlate(f, f, "full")[len(f) - 1:]
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[k] / (ac[0] + 1e-9) > 0.45:
            f0.append(sr / k)
    if len(f0) < 5:
        return 0.0
    st = 12 * np.log2(np.array(f0) / np.median(f0))
    return float(np.percentile(st, 90) - np.percentile(st, 10))


def template_problems(x: np.ndarray, sr: int) -> list[str]:
    occ, rng = occupancy(x, sr), pitch_range_st(x, sr)
    out = []
    if occ < MIN_OCCUPANCY:
        out.append(f"出声占比 {occ:.0%} < {MIN_OCCUPANCY:.0%}(停顿太多,不像一口气连着说)")
    if rng < MIN_RANGE_ST:
        out.append(f"音高起伏 {rng:.1f} 半音 < {MIN_RANGE_ST:g}(抑扬顿挫不够)")
    return out


def load_wav(path) -> tuple[np.ndarray, int]:
    """单声道 float32(多声道取平均)。只读 16 位 PCM wav —— 我们自己的产物都是。"""
    with wave.open(str(path), "rb") as w:
        sr, ch = w.getframerate(), w.getnchannels()
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    return (x.reshape(-1, ch).mean(1) if ch > 1 else x), sr
