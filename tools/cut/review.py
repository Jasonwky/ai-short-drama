# -*- coding: utf-8 -*-
"""看片确认:人在视频卡片上看过这段(切镜帧、与上一段的接缝、场记表),确认没有穿帮 —— 没确认的段落拦导出。

为什么要人:生成是随机的,检测只抓得住一部分(白屏、烧字幕);跪的朝向、床摆在正堂、帽子丢了,
每次看片都发现检测之外的新问题(spec 2026-10-22-连续性保障-design.md 第三道防线)。
只认那条视频的 sha256(库表 segment_review,v13,写下不可改):重出 / 挑别的版本自动失效,要重新看。
导出 begin 时快照进 inputs_json,溯源报告逐段列出。
"""
from __future__ import annotations

import json
from pathlib import Path

from tools.cut import checklist
from tools.drama import repo
from tools.drama.db import Store
from tools.serve.dispatch import sha256_file


def review_for(store: Store, root: Path, seg) -> dict | None:
    """这段当前视频的看片确认(按内容哈希对);没有视频 / 文件不在 / 没确认过 = None。"""
    if not seg["video_path"] or not (Path(root) / seg["video_path"]).is_file():
        return None
    row = repo.review(store, seg["id"], sha256_file(Path(root) / seg["video_path"]))
    return None if row is None else dict(row)


def confirm(store: Store, root: Path, segment_id: int, checks: list[str], note: str = "") -> int:
    """人逐项勾过验收清单才算确认(spec 2026-10-23-不穿帮验收-design.md 第 1 节):人勾项缺一项就拒 ——
    确认的意思就是「这些我都看过了」。"""
    seg = repo.get(store, "segment", segment_id)
    if not seg["video_path"] or not (Path(root) / seg["video_path"]).is_file():
        raise ValueError(f"段落 {seg['number']} 还没有视频,没什么可看")
    gone = checklist.missing(checks)
    if gone:
        raise ValueError(f"段落 {seg['number']} 还有没勾的检查项:{'、'.join(gone)}")
    return repo.add_review(store, segment_id, video_path=seg["video_path"],
                           sha256=sha256_file(Path(root) / seg["video_path"]), checks=checks, note=note)


def complete(r: dict) -> bool:
    """按现行清单勾全了。v13 时代的确认(checks_json 为空)不算 —— 清单变了,要按新清单重看。"""
    return bool(r["checks_json"]) and not checklist.missing(json.loads(r["checks_json"]))


def problem(number: int, r: dict | None = None) -> str:
    if r is not None:
        return f"段落 {number} 是按旧清单确认的,需要按新清单重看(在「视频」面板逐项勾选)"
    return f"段落 {number} 的当前视频还没看片确认(在「视频」面板看切镜帧与接缝,逐项勾选后确认)"


def snapshot(review: dict | None) -> dict | None:
    if review is None:
        return None
    return {"id": review["id"], "sha256": review["sha256"], "note": review["note"], "created_at": review["created_at"],
            "checks": json.loads(review["checks_json"]) if review["checks_json"] else []}


def note(number: int, snap: dict) -> str:
    """溯源报告里的一行。"""
    extra = f",备注:{snap['note']}" if snap["note"] else ""
    return f"看片确认:段落 {number},{snap['created_at']},勾过 {len(snap.get('checks', []))} 项{extra}"
