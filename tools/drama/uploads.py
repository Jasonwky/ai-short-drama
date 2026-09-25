# -*- coding: utf-8 -*-
"""资产上传参考图:人手里已有的图直接当角色 / 场景 / 道具的参考图(设计 §七「资产」面板)。

- 只收 PNG / JPEG(看文件头,不看扩展名),上限 20 MB;按内容寻址存到 `assets/uploads/<sha256 前 16 位>.<png|jpg>`;
- 上传记成一条**创建即完成**的 task(`model = manual:upload`):进历史、能用「用这一版」换回,溯源里照实写「人工上传」;
- 替换已有参考图会让绑定了这个资产的段落的参考帧与视频作废 → plan / confirm,先报代价(与「用这一版」同一套失效传播)。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from tools.drama import invalidation, repo
from tools.drama.db import Store
from tools.drama.invalidation import Impact
from tools.drama.tasks import record_local
from tools.gen.assets import SPEC
from tools.gen.capability import MINIMAX_H3, QWEN_IMAGE_EDIT_2511

MODEL = "manual:upload"
MAX_BYTES = 20 * 1024 * 1024
_MAGIC = {b"\x89PNG\r\n\x1a\n": ".png", b"\xff\xd8\xff": ".jpg"}
_STAGED = re.compile(r"^assets/uploads/[0-9a-f]{16}\.(png|jpg)$")


def stage(data_root: Path, data: bytes) -> str:
    """校验并落盘,返回相对 data_root 的路径。同一张图传几次都是同一个文件。"""
    ext = next((e for magic, e in _MAGIC.items() if data.startswith(magic)), None)
    if ext is None:
        raise ValueError("只收 PNG 或 JPEG 图片(按文件内容判断,不看扩展名)")
    if len(data) > MAX_BYTES:
        raise ValueError(f"图片太大({len(data) / 1024 / 1024:.1f} MB,上限 {MAX_BYTES // 1024 // 1024} MB)")
    rel = f"assets/uploads/{hashlib.sha256(data).hexdigest()[:16]}{ext}"
    path = Path(data_root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return rel


def plan_use(store: Store, table: str, row_id: int) -> Impact:
    """换成上传的图会让哪些产物作废(只算不改)。还没有参考图时不影响任何产物。"""
    if table not in SPEC:
        raise ValueError(f"不是资产表:{table}")
    if not repo.get(store, table, row_id)[SPEC[table][0]]:
        return Impact((), 0, 0, 0.0)
    return invalidation.impact(store, table, row_id, video_cap=MINIMAX_H3, image_cap=QWEN_IMAGE_EDIT_2511)


def confirm_use(store: Store, data_root: Path, table: str, row_id: int, rel: str, filename: str, imp: Impact) -> int:
    if not _STAGED.match(rel) or not (Path(data_root) / rel).is_file():
        raise ValueError(f"不是本机暂存的上传图:{rel}")
    field = SPEC[table][0]
    sha = hashlib.sha256((Path(data_root) / rel).read_bytes()).hexdigest()
    tid = record_local(store, kind="image", target_type=table, target_id=row_id, model=MODEL,
                       params={"workflow": "upload", "writes": field, "filename": filename},
                       prompt=f"人工上传:{filename}", refs=[], result_path=rel, result_sha256=sha)
    invalidation.apply(store, imp)
    repo.update(store, table, row_id, **{field: rel})
    return tid
