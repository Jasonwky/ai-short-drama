# -*- coding: utf-8 -*-
"""白屏检测:找「连续 ≥ MIN_SEC 秒、画面大片近乎纯白」的区间 —— 命中就拦导出(用户 2026-10-20 定)。

为什么要它:实战《替嫁冲喜》第 2 段第三轮视频(task 82)中段约 4 秒人物站在白底前(疑似四视图的白底带进了视频),
末尾再一下整屏白闪;人眼逐段看才发现。

判据(`spike-results/2026-10-20-白屏检测标定.md`,实战 24 条视频):每帧算「亮度 ≥ LUMA 的像素占比」,
占比 ≥ RATIO 的帧连续 ≥ MIN_SEC 秒算一段白屏。命中那条白底段每帧占比 0.47–1.00,其余 23 条视频
(白墙、白衣、烛光高光)每帧最高 0.16。不是只看整屏全白:白底前站着人时白只占六成,blackdetect 式的
「整屏 ≥ 98%」会漏掉它。

量法只用本机 ffmpeg:缩到 160 宽 → lut 把亮度 ≥ LUMA 的像素置 255、其余置 0 → signalstats 的 YAVG / 255 就是占比。
亮度按视频原值比(H3 出的是有限范围 yuv420p,纯白约 235)。
结果按视频 sha256 缓存在视频旁(`<视频>.blankscan.json`,与 `textscan` 同一做法),换了视频或改了阈值自动作废。

误报(雪景等本来就白)由人在视频卡片上填理由放行(`confirm`,库表 blank_override,v12):只认那条视频的 sha256,
换了视频自动失效;导出 begin 时快照进 inputs_json,溯源报告逐条列出。
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from tools.cut import media
from tools.drama import repo
from tools.drama.db import Store
from tools.serve.dispatch import sha256_file

LUMA = 200                  # 有限范围 16–235 里的 200 ≈ 全范围 214(满亮度的 84%)
RATIO = 0.35                # 一帧里近白像素占比过它就算这帧「大片白」
MIN_SEC = 0.5
PARAMS = {"luma": LUMA, "ratio": RATIO, "min_sec": MIN_SEC}


@dataclass(frozen=True)
class Span:
    start: float            # 秒
    end: float


def spans(samples: list[tuple[float, float]]) -> list[Span]:
    """samples = [(帧时刻, 近白占比)],按时刻排好。一帧占到它与下一帧之间(最后一帧按前一个间隔算)。"""
    out, run = [], None
    for i, (t, frac) in enumerate(samples):
        nxt = samples[i + 1][0] if i + 1 < len(samples) else t + (t - samples[i - 1][0] if i else 0.0)
        if frac >= RATIO:
            run = (run[0] if run else t, nxt)
        if run and (frac < RATIO or i + 1 == len(samples)):
            if run[1] - run[0] >= MIN_SEC - 1e-6:
                out.append(Span(round(run[0], 2), round(run[1], 2)))
            run = None
    return out


def measure(path: Path) -> list[tuple[float, float]]:
    vf = f"scale=160:-2,lut=y='gte(val\\,{LUMA})*255':u=128:v=128,signalstats,metadata=print:file=-"
    p = media._run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", vf, "-an", "-f", "null", "-"])
    out, t = [], None
    for line in p.stdout.splitlines():
        m = re.match(r"frame:\d+\s+pts:\S+\s+pts_time:(\S+)", line)
        if m:
            t = float(m.group(1))
            continue
        m = re.match(r"lavfi\.signalstats\.YAVG=(\S+)", line)
        if m and t is not None:
            out.append((t, float(m.group(1)) / 255))
    if not out:
        raise media.CutError(f"白屏检测没从 {path} 读出任何帧的亮度")
    return out


def detect(path: Path) -> list[Span]:
    return spans(measure(path))


def _cache_path(root: Path, video_rel: str) -> Path:
    return Path(root) / f"{video_rel}.blankscan.json"


def cached(root: Path, video_rel: str) -> list[Span] | None:
    """不跑 ffmpeg:有对得上这个视频、这套阈值的缓存就返回结果,否则 None(还没查过)。"""
    p = _cache_path(root, video_rel)
    if not p.is_file():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    if data["video_sha256"] != sha256_file(Path(root) / video_rel) or data["params"] != PARAMS:
        return None
    return [Span(**s) for s in data["spans"]]


def check(root: Path, video_rel: str) -> list[Span]:
    """查一段视频;有缓存读缓存,没有就跑 ffmpeg 并写缓存。"""
    got = cached(root, video_rel)
    if got is not None:
        return got
    found = detect(Path(root) / video_rel)
    _cache_path(root, video_rel).write_text(
        json.dumps({"video_sha256": sha256_file(Path(root) / video_rel), "params": PARAMS,
                    "spans": [asdict(s) for s in found]}, ensure_ascii=False, indent=1), encoding="utf-8")
    return found


def problem(number: int, found: list[Span]) -> str:
    where = "、".join(f"第 {s.start:.1f}–{s.end:.1f} 秒" for s in found)
    return f"段落 {number} {where}画面近乎全白(大片纯白底或白闪),重出这段再导"


# ── 人工确认放行(用户 2026-10-20 定)────────────────────────────
# 雪景之类本来就白的画面:人看过、填理由放行。只认这条视频的 sha256 —— 重出 / 挑别的版本就自动失效、重新拦。

def override_for(store: Store, root: Path, seg) -> dict | None:
    """这段当前视频的放行(按内容哈希对);没有视频 / 文件不在 / 没放行过 = None。"""
    if not seg["video_path"] or not (Path(root) / seg["video_path"]).is_file():
        return None
    row = repo.blank_override(store, seg["id"], sha256_file(Path(root) / seg["video_path"]))
    return None if row is None else dict(row)


def confirm(store: Store, root: Path, segment_id: int, reason: str) -> int:
    """放行这段的当前视频。只放行查过且命中的视频(没命中就没什么可放行的);理由★必填。"""
    seg = repo.get(store, "segment", segment_id)
    found = (cached(root, seg["video_path"])
             if seg["video_path"] and (Path(root) / seg["video_path"]).is_file() else None)
    if not found:
        raise ValueError(f"段落 {seg['number']} 的当前视频没查出白屏(或还没查过),不用放行")
    return repo.add_blank_override(store, segment_id, video_path=seg["video_path"],
                                   sha256=sha256_file(Path(root) / seg["video_path"]),
                                   spans=[[s.start, s.end] for s in found], reason=reason)


def snapshot(override: dict | None) -> dict | None:
    """导出 begin 时记进 inputs_json 的那份(与视频的 sha256 同处一项)。"""
    if override is None:
        return None
    return {"id": override["id"], "sha256": override["sha256"], "spans": json.loads(override["spans_json"]),
            "reason": override["reason"], "created_at": override["created_at"]}


def override_note(number: int, snap: dict) -> str:
    """溯源报告里的一行。"""
    where = "、".join(f"第 {a:.1f}–{b:.1f} 秒" for a, b in snap["spans"])
    return f"人工确认放行:段落 {number},{where},{snap['reason']},{snap['created_at']}"
