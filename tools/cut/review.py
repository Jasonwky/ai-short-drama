# -*- coding: utf-8 -*-
"""看片确认:人在视频卡片上看过这段(切镜帧、与上一段的接缝、场记表),确认没有穿帮 —— 没确认的段落拦导出。

为什么要人:生成是随机的,检测只抓得住一部分(白屏、烧字幕);跪的朝向、床摆在正堂、帽子丢了,
每次看片都发现检测之外的新问题(spec 2026-10-22-连续性保障-design.md 第三道防线)。
只认那条视频的 sha256(库表 segment_review,v13,写下不可改):重出 / 挑别的版本自动失效,要重新看。
导出 begin 时快照进 inputs_json,溯源报告逐段列出。
"""
from __future__ import annotations

from pathlib import Path

from tools.drama import repo
from tools.drama.db import Store
from tools.serve.dispatch import sha256_file


def review_for(store: Store, root: Path, seg) -> dict | None:
    """这段当前视频的看片确认(按内容哈希对);没有视频 / 文件不在 / 没确认过 = None。"""
    if not seg["video_path"] or not (Path(root) / seg["video_path"]).is_file():
        return None
    row = repo.review(store, seg["id"], sha256_file(Path(root) / seg["video_path"]))
    return None if row is None else dict(row)


def confirm(store: Store, root: Path, segment_id: int, note: str = "") -> int:
    seg = repo.get(store, "segment", segment_id)
    if not seg["video_path"] or not (Path(root) / seg["video_path"]).is_file():
        raise ValueError(f"段落 {seg['number']} 还没有视频,没什么可看")
    return repo.add_review(store, segment_id, video_path=seg["video_path"],
                           sha256=sha256_file(Path(root) / seg["video_path"]), note=note)


def problem(number: int) -> str:
    return f"段落 {number} 的当前视频还没看片确认(在「视频」面板看切镜帧与接缝,点「看过,没有穿帮」)"


def snapshot(review: dict | None) -> dict | None:
    if review is None:
        return None
    return {"id": review["id"], "sha256": review["sha256"], "note": review["note"], "created_at": review["created_at"]}


def note(number: int, snap: dict) -> str:
    """溯源报告里的一行。"""
    extra = f",备注:{snap['note']}" if snap["note"] else ""
    return f"看片确认:段落 {number},{snap['created_at']}{extra}"
