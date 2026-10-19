# -*- coding: utf-8 -*-
"""烧字幕检测:逐帧 OCR,识别出的字与本段台词逐字比对 —— 对得上才算烧进画面的字幕。

为什么要它:H3 偶尔把台词当字幕画进画面(实战《替嫁冲喜》第 6 段两次、多种子调研 1 次),
导出时我们自己还要烧一遍字幕,成片就是两层字。人眼逐段看才发现,所以落成检测,**命中就拦导出**(用户 2026-10-19 定)。

判据(`spike-results/2026-10-19-真实场景参考图调研.md`,27 条人工标注样本:3 条烧字幕全中、24 条没烧的零误报):
- 场景里本来就有字(实拍孔府厅堂的匾额、对联),所以「画面里有字」不算;**与台词对上 ≥ MIN_CHARS 字且占台词 ≥ MIN_RATIO** 才算;
- 烧进去的也可能是提示词里的词(对照实验:「萧珩」「冷声开口」):OCR 出的字 ≥ 2 个、其中 ≥ CONTEXT_RATIO 出现在本段描述 /
  视频提示词里,也算 —— 匾额对联的字不在提示词里;
- 竖排字幕整条识别只出一两个字 → 细高的框交给 `read_column` 逐字切开再认;
- 结果按视频 sha256 缓存在视频旁(`<视频>.textscan.json`),换了视频自动作废;导出前读缓存不重扫。

OCR 用 EasyOCR(Apache-2.0,中文简体模型,本机 CPU 离线跑,数据不外发);没装就 fail-loud,不跳过检查。
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from tools.cut import media
from tools.drama import rules
from tools.serve.dispatch import sha256_file

FPS = 2
MIN_CONF = 0.3
MIN_CHARS = 3
MIN_RATIO = 0.4
CONTEXT_RATIO = 0.8
TALL = 2.5                  # 框高 / 框宽超过它就当竖排


@dataclass(frozen=True)
class Hit:
    t: float                # 第几秒
    text: str               # 这一帧 OCR 出的字(与台词对得上的那一段)
    matched: int            # 台词里有几个字出现在其中
    ratio: float            # matched / 台词字数


def _overlap(text: str, line: str) -> tuple[int, float]:
    want = [ch for ch in line if rules.count_chars(ch)]
    n = sum(1 for ch in want if ch in text)
    return n, (n / len(want) if want else 0.0)


def _texts(frame, reader) -> list[str]:
    out = []
    for box, text, conf in reader.read(frame):
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        if w and h > TALL * w:
            out.append(reader.read_column(frame, box))
        elif conf >= MIN_CONF:
            out.append(text)
    return out


def _from_context(text: str, context: str) -> tuple[int, float]:
    """OCR 出的字有多少出现在描述 / 提示词里(反过来量:提示词很长,只看这串字是不是从里面来的)。"""
    got = [ch for ch in text if rules.count_chars(ch)]
    n = sum(1 for ch in got if ch in context)
    return n, (n / len(got) if got else 0.0)


def hits(frames, lines: list[str], reader, context: str = "") -> list[Hit]:
    """frames = [(秒, 帧)];每帧取对得最多的那段字,过阈值才记。context = 本段描述 + 视频提示词。"""
    found = []
    for t, frame in frames:
        best = None
        for text in _texts(frame, reader):
            cands = [_overlap(text, line) for line in lines]
            cands = [(n, r) for n, r in cands if n >= MIN_CHARS and r >= MIN_RATIO]
            n, r = _from_context(text, context)
            if n >= 2 and r >= CONTEXT_RATIO:
                cands.append((n, r))
            for n, r in cands:
                if best is None or n > best.matched:
                    best = Hit(t, text, n, round(r, 2))
        if best:
            found.append(best)
    return found


class EasyReader:
    """EasyOCR 适配:整帧检测 + 竖条逐字识别。"""
    def __init__(self):
        try:
            import easyocr
        except ImportError as e:
            raise media.CutError("烧字幕检测要用 EasyOCR(pip install easyocr),本机没装 —— 不跳过检查") from e
        self._r = easyocr.Reader(["ch_sim"], gpu=False, verbose=False)

    @staticmethod
    def _image(frame):
        import numpy as np
        from PIL import Image
        return np.array(Image.open(frame).convert("RGB"))

    def read(self, frame):
        return self._r.readtext(self._image(frame))

    def read_column(self, frame, box) -> str:
        im = self._image(frame)
        xs, ys = [int(p[0]) for p in box], [int(p[1]) for p in box]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        n = max(1, round((y1 - y0) / max(1, x1 - x0)))
        cells = [im[y0 + i * (y1 - y0) // n: y0 + (i + 1) * (y1 - y0) // n, x0:x1] for i in range(n)]
        return "".join(t for c in cells for _, t, _ in self._r.recognize(c))


def make_reader():
    return EasyReader()


def _frames(path: Path, out: Path) -> list[tuple[float, str]]:
    media._run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-vf", f"fps={FPS}", str(out / "f%04d.png")])
    return [(i / FPS, str(p)) for i, p in enumerate(sorted(out.glob("f*.png")))]


def _cache_path(root: Path, video_rel: str) -> Path:
    return Path(root) / f"{video_rel}.textscan.json"


def cached(root: Path, video_rel: str, lines: list[str], context: str = "") -> list[Hit] | None:
    """不跑 OCR:有对得上这个视频、这些台词的缓存就返回结果,否则 None(还没查过)。"""
    p = _cache_path(root, video_rel)
    if not p.is_file():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    if (data["video_sha256"] != sha256_file(Path(root) / video_rel) or data["lines"] != lines
            or data.get("context", "") != context):
        return None
    return [Hit(**h) for h in data["hits"]]


def check(root: Path, video_rel: str, lines: list[str], context: str = "", *, reader=None) -> list[Hit]:
    """查一段视频;有缓存读缓存,没有就跑 OCR 并写缓存。"""
    got = cached(root, video_rel, lines, context)
    if got is not None:
        return got
    reader = reader or make_reader()
    with tempfile.TemporaryDirectory() as d:
        found = hits(_frames(Path(root) / video_rel, Path(d)), lines, reader, context)
    _cache_path(root, video_rel).write_text(
        json.dumps({"video_sha256": sha256_file(Path(root) / video_rel), "lines": lines, "context": context,
                    "hits": [asdict(h) for h in found]}, ensure_ascii=False, indent=1), encoding="utf-8")
    return found


def problem(number: int, found: list[Hit]) -> str:
    h = max(found, key=lambda x: x.matched)
    return (f"段落 {number} 的画面里疑似烧进了字幕:第 {h.t:g} 秒识别出「{h.text}」(其中 {h.matched} 字出自台词 / 提示词)"
            f" —— 成片会出现两层字幕,换种子重出视频后再导出")
