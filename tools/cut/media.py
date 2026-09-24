# -*- coding: utf-8 -*-
"""成片用到的 ffmpeg 操作。只调本机 ffmpeg / ffprobe(4.4 实测),不引依赖。

- 找不到 ffmpeg 报 `ValueError`(给人看的原因,控制台渲染成 400),不假装做完;
- ffmpeg 自己失败报 `CutError`,带 stderr 末尾 —— 不吞、不兜空。
- 显式标识的尺寸按**画面最短边**算(GB 45438-2025 5.4 f:字高不低于最短边 5%):
  字号取 7.5%。真 H3 片实测(字号 6.5% 时):量到的字高是字号的 0.82–0.92 倍(亮背景上白字吃掉一部分),
  最差一帧只比下限多 2.6px —— 所以留到 7.5%;描边另算,检查器会扣掉。
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

FONT = Path("C:/Windows/Fonts/NotoSansSC-VF.ttf")    # SIL OFL,可渲染进视频
LABEL_TEXT = "AI 生成"                                 # 同时含「AI」与「生成」(5.4 b)
FONT_RATIO = 0.075
MARGIN_RATIO = 0.04


class CutError(RuntimeError):
    """ffmpeg / ffprobe 执行失败。"""


@dataclass(frozen=True)
class Probe:
    width: int
    height: int
    duration: float
    has_audio: bool


@dataclass(frozen=True)
class Label:
    fontsize: int
    border: int
    margin: int

    @classmethod
    def for_size(cls, width: int, height: int) -> "Label":
        short = min(width, height)
        return cls(math.ceil(short * FONT_RATIO), max(2, round(short * 0.004)), round(short * MARGIN_RATIO))


def require_ffmpeg() -> None:
    missing = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
    if missing:
        raise ValueError(f"本机找不到 {' / '.join(missing)} —— 成片与抽尾帧都要用它,请先安装并加入 PATH")


def _run(args: list[str], *, binary: bool = False) -> subprocess.CompletedProcess:
    p = subprocess.run(args, capture_output=True, **({} if binary else
                                                      {"text": True, "encoding": "utf-8", "errors": "replace"}))
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace") if binary else p.stderr
        raise CutError(f"{args[0]} 失败(退出码 {p.returncode}):{err.strip()[-800:]}")
    return p


def probe(path: Path) -> Probe:
    p = _run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
              "-of", "json", str(path)])
    doc = json.loads(p.stdout)
    video = [s for s in doc["streams"] if s["codec_type"] == "video"]
    if not video:
        raise CutError(f"{path} 里没有视频流")
    return Probe(video[0]["width"], video[0]["height"], float(doc["format"]["duration"]),
                 any(s["codec_type"] == "audio" for s in doc["streams"]))


def frame_count(path: Path) -> int:
    p = _run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
              "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)])
    return int(p.stdout.strip())


def extract_last_frame(video: Path, out_png: Path) -> None:
    """先数帧、再按帧号取最后一帧 —— 取到的**确实**是最后一帧,不靠从末尾倒退的近似。"""
    n = frame_count(video)
    if n < 1:
        raise CutError(f"{video} 一帧都解不出来")
    out_png.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf", f"select=eq(n\\,{n - 1})", "-vsync", "0",
          "-frames:v", "1", str(out_png)])
    if not out_png.is_file():
        raise CutError(f"ffmpeg 没有写出 {out_png}")


def encode_episode(clips: list[Path], out: Path, label: Label | None) -> None:
    """按顺序拼接(重编码),可选烧录显式标识(左上角、全程)。**清空全部原有元数据**:
    ComfyUI 的 mp4 里嵌着完整工作流(提示词、种子、参考图名),不能带进成片。"""
    n = len(clips)
    ins = [a for c in clips for a in ("-i", str(c))]
    graph = "".join(f"[{i}:v:0][{i}:a:0]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]"
    vout = "[v]"
    if label is not None:
        font = FONT.as_posix().replace(":", "\\:")
        graph += (f";[v]drawtext=fontfile='{font}':text='{LABEL_TEXT}':fontsize={label.fontsize}:fontcolor=white"
                  f":borderw={label.border}:bordercolor=black:x={label.margin}:y={label.margin}[vl]")
        vout = "[vl]"
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", graph, "-map", vout, "-map", "[a]",
          "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-map_metadata", "-1", str(out)])


def stream_sha256(path: Path) -> str:
    """视频流的 SHA-256(按压缩后的数据包算)。只换封装、改元数据时不变 —— 写进 ReservedCode1 保护内容完整性。"""
    p = _run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-c", "copy", "-f", "hash",
              "-hash", "sha256", "-"])
    return p.stdout.strip().split("=", 1)[1]


def write_tags(src: Path, dst: Path, tags: dict[str, str]) -> None:
    """只换封装写元数据(不重编码):先清空原有元数据,再只写给定的这几个。"""
    meta = [a for k, v in tags.items() for a in ("-metadata", f"{k}={v}")]
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-map", "0", "-c", "copy", "-map_metadata", "-1",
          "-movflags", "use_metadata_tags", *meta, str(dst)])


def format_tags(path: Path) -> dict[str, str]:
    p = _run(["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(path)])
    return json.loads(p.stdout).get("format", {}).get("tags", {})


def gray_frame(path: Path, t: float, width: int, height: int) -> bytes:
    """t 秒处的一帧,8 位灰度原始像素。"""
    p = _run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
              "-f", "rawvideo", "-pix_fmt", "gray", "-"], binary=True)
    if len(p.stdout) != width * height:
        raise CutError(f"{path} 在 {t:.2f}s 取帧得到 {len(p.stdout)} 字节,应为 {width * height}")
    return p.stdout


def diff_bbox(a: bytes, b: bytes, width: int, threshold: int = 48, min_run: int = 3) -> tuple[int, int, int, int] | None:
    """两帧灰度差超过阈值的区域外接框 (x0, y0, x1, y1),没有差异返回 None。

    一行 / 一列里至少 min_run 个差异像素才算进框 —— 两次独立编码的同一画面会有孤立噪点
    (实测:1 秒测试图样末帧出现过 1 个差 49 的像素);标识是成片的差异,不受影响。
    行首尾若不足 min_run 会让框略小 —— 只会让字高检查更严,不会放过不合格的。
    """
    height = len(a) // width
    rows, cols = [0] * height, [0] * width
    for y in range(height):
        row = y * width
        for x in range(width):
            if abs(a[row + x] - b[row + x]) > threshold:
                rows[y] += 1
                cols[x] += 1
    ys = [y for y, n in enumerate(rows) if n >= min_run]
    xs = [x for x, n in enumerate(cols) if n >= min_run]
    return (xs[0], ys[0], xs[-1], ys[-1]) if xs and ys else None
