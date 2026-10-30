# -*- coding: utf-8 -*-
"""口型检查:每句台词的时间段里,有没有一张脸的嘴随声音动(spec 2026-10-30-台词配音与口型闸门-design.md 第 6.3 节)。

量法(spike-results/2026-10-29-台词与口型匹配方案调研.md 第 4、4.1 节):
- 本机 CPU 逐帧 mediapipe FaceLandmarker(Apache-2.0),每张脸的嘴张开度 = 内唇 13–14 距离 / 脸高 10–152;
- 音轨每帧 RMS 包络;
- 按鼻尖 x 位置把各帧的脸串成轨迹(最近邻,差 TRACK_DX 以内算同一张);
- **每句台词的时间段**(台词音频 `tts.json` 的 spans —— 音频钉在第 0 帧,视频时间轴与它一致)里,
  对每条轨迹算张开度与包络的皮尔逊相关(±LAG_MAX 帧内取最好),取最高的那条。
判定:r ≥ R_MIN 且滞后在 ±LAG_MAX 帧内 = 过;短于 MIN_LINE_SEC、或这段时间里没有正脸轨迹 = 「无法判定」(不算不过,靠看片确认)。
合格线是暂定的(实测:钉音频后 0.66、原片好片 0.39、画外音 −0.77,样本 5 条),前几集出片后重标。
**只判「嘴随声动」,不判「是对的人在动嘴」**(不做人脸 ↔ 角色的身份对应)。

结果按视频 sha256 + spans + 参数缓存在视频旁(`<视频>.lipsync.json`,与 `blankscan` 同一做法);
单测里 `measure` 被 tests/conftest.py 换成假的。
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from tools.serve.dispatch import sha256_file

MODEL = Path(__file__).resolve().parents[2] / "thirdparty" / "models" / "face_landmarker.task"
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
R_MIN = 0.30            # 暂定合格线
LAG_MAX = 3             # 帧(24 fps 下 ±0.125 秒);实测钉音频后滞后 0 帧
LAG_SEARCH = 6          # 相关在 ±这么多帧里找最好
MIN_LINE_SEC = 1.0      # 短于它的句子帧太少,相关不可信
MIN_FRAMES = 12         # 一条轨迹在这句里至少这么多帧有脸才算
TRACK_DX = 0.12
PARAMS = {"r_min": R_MIN, "lag_max": LAG_MAX, "lag_search": LAG_SEARCH, "min_line_sec": MIN_LINE_SEC,
          "min_frames": MIN_FRAMES, "track_dx": TRACK_DX}


@dataclass(frozen=True)
class Line:
    n: int              # 第几句(从 1 起)
    start: float
    end: float
    r: float | None     # 最好那条轨迹的相关;None = 无法判定
    lag: int | None
    verdict: str        # 过 / 不过 / 无法判定
    why: str = ""


def measure(path: Path) -> tuple[float, list[list[tuple[float, float]]], list[float]]:
    """(fps, 每帧的脸 [(鼻尖 x, 张开度)], 每帧音量包络)。"""
    import cv2
    import mediapipe as mp
    import numpy as np
    from mediapipe.tasks.python import BaseOptions, vision

    if not MODEL.is_file():
        raise FileNotFoundError(f"口型检查要 mediapipe 人脸模型(Apache-2.0):{MODEL} 不在 —— 从 {MODEL_URL} 下载放到这里")
    opts = vision.FaceLandmarkerOptions(base_options=BaseOptions(model_asset_path=str(MODEL)),
                                        running_mode=vision.RunningMode.VIDEO, num_faces=4)
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS)
    rows = []
    with vision.FaceLandmarker.create_from_options(opts) as lm:
        i = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            res = lm.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB,
                                               data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)), int(i * 1000 / fps))
            rows.append([(f[1].x, abs(f[14].y - f[13].y) / (abs(f[152].y - f[10].y) + 1e-6)) for f in res.face_landmarks])
            i += 1
    cap.release()
    return fps, rows, envelope(path, fps, len(rows))


def envelope(path: Path, fps: float, n: int) -> list[float]:
    """音轨每帧(1/fps 秒)的 RMS,共 n 帧。"""
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    hop = 16000 / fps
    return [float(np.sqrt(np.mean(a[int(k * hop):int((k + 1) * hop)] ** 2) + 1e-12)) if int(k * hop) < len(a) else 0.0
            for k in range(n)]


def _corr(a: list[float], b: list[float]) -> float | None:
    import numpy as np
    pairs = [(x, y) for x, y in zip(a, b) if x is not None]
    if len(pairs) < MIN_FRAMES:
        return None
    x, y = np.array(pairs).T
    if x.std() == 0 or y.std() == 0:
        return 0.0                  # 嘴一动不动(或这段没声音):不随声动,算 0 而不是「无法判定」
    return float(np.corrcoef(x, y)[0, 1])


def judge(fps: float, rows: list[list[tuple[float, float]]], env: list[float], spans: list[list[float]]) -> list[Line]:
    """纯函数:按句判。"""
    n = len(rows)
    tracks: list[list] = []                       # [x_last, 每帧张开度(None = 这帧没这张脸)]
    for t, faces in enumerate(rows):
        for x, o in faces:
            best = min(tracks, key=lambda k: abs(k[0] - x), default=None)
            if best is None or abs(best[0] - x) > TRACK_DX:
                best = [x, [None] * n]
                tracks.append(best)
            best[0], best[1][t] = x, o
    out = []
    for k, (start, end) in enumerate(spans, 1):
        if end - start < MIN_LINE_SEC:
            out.append(Line(k, start, end, None, None, "无法判定", f"这句只有 {end - start:.1f} 秒,太短"))
            continue
        lo, hi = int(start * fps), min(int(end * fps), n)
        best = None
        for _, series in tracks:
            for lag in sorted(range(-LAG_SEARCH, LAG_SEARCH + 1), key=abs):   # 一样高时取滞后最小的(先到先得)
                a = [series[t] for t in range(lo, hi) if 0 <= t + lag < n]
                b = [env[t + lag] for t in range(lo, hi) if 0 <= t + lag < n]
                r = _corr(a, b)
                if r is not None and (best is None or r > best[0]):
                    best = (r, lag)
        if best is None:
            out.append(Line(k, start, end, None, None, "无法判定", "这句的时间里没有正脸"))
            continue
        r, lag = round(best[0], 3), best[1]
        ok = r >= R_MIN and abs(lag) <= LAG_MAX
        out.append(Line(k, start, end, r, lag, "过" if ok else "不过",
                        "" if ok else (f"嘴与声音的相关只有 {r:.2f}(要 ≥ {R_MIN})" if r < R_MIN
                                       else f"嘴比声音差了 {lag} 帧(要在 ±{LAG_MAX} 帧内)")))
    return out


def _cache_path(root: Path, video_rel: str) -> Path:
    return Path(root) / f"{video_rel}.lipsync.json"


def cached(root: Path, video_rel: str, spans: list[list[float]]) -> list[Line] | None:
    """不量:有对得上这个视频、这组台词时段、这套参数的缓存就返回,否则 None(还没量过)。"""
    p = _cache_path(root, video_rel)
    if not p.is_file():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    if (data["video_sha256"], data["spans"], data["params"]) != (sha256_file(Path(root) / video_rel), spans, PARAMS):
        return None
    return [Line(**x) for x in data["lines"]]


def check(root: Path, video_rel: str, spans: list[list[float]]) -> list[Line]:
    """量一段视频;有缓存读缓存,没有就量(CPU,一条 10 秒片约十几秒)并写缓存。"""
    got = cached(root, video_rel, spans)
    if got is not None:
        return got
    lines = judge(*measure(Path(root) / video_rel), spans)
    _cache_path(root, video_rel).write_text(json.dumps(
        {"video_sha256": sha256_file(Path(root) / video_rel), "spans": spans, "params": PARAMS,
         "lines": [asdict(x) for x in lines]}, ensure_ascii=False, indent=1), encoding="utf-8")
    return lines
