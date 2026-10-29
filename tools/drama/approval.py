# -*- coding: utf-8 -*-
"""资产图定稿(spec 2026-10-28-成片质量闸门-第一期-design.md 第 6.1 节)。

资产图以前没有定稿这一步:第一张出来就写进资产,直接被首帧 / 视频拿去当参考(第 1 集孙老栓的近代制服、燕无咎的黑烟就是这样进的成片)。
现在每张图要人定稿才能往下用:
- 定稿**只认那张图**:记路径 + sha256。规划闸门按路径认(规划只看库;每次出图 / 上传都是新路径,换了图路径必变),
  sha256 留作溯源;
- 有 AI / 代码不通过的项时,理由★必填;一经写下不可改(库触发器)。
"""
from __future__ import annotations

import json
from pathlib import Path

from tools.drama import repo
from tools.drama.db import Store, now

SLOTS = {("character", "image"): "turnaround_path", ("character", "fourview"): "fourview_path",
         ("scene", "image"): "image_path", ("prop", "image"): "image_path",
         ("character", "voice"): "voice_ref_path"}      # 角色试听音(spec 2026-10-30-台词配音与口型闸门-design.md 第 3 节)
LABEL = {("character", "image"): "正面图", ("character", "fourview"): "四视图",
         ("scene", "image"): "空镜图", ("prop", "image"): "道具图", ("character", "voice"): "试听音"}


def field(table: str, slot: str) -> str:
    if (table, slot) not in SLOTS:
        raise ValueError(f"没有这种资产图:{table} / {slot}")
    return SLOTS[(table, slot)]


def current(store: Store, table: str, row_id: int, slot: str):
    """这张资产图**当前那张**的定稿记录;没定稿(或定的是换掉之前那张)返回 None。"""
    path = repo.get(store, table, row_id)[field(table, slot)]
    if not path:
        return None
    return store.conn.execute("SELECT * FROM asset_approval WHERE table_name = ? AND row_id = ? AND slot = ? AND path = ?"
                              " ORDER BY id DESC LIMIT 1", (table, row_id, slot, path)).fetchone()


def approve(store: Store, data_root: Path, table: str, row_id: int, slot: str, *, failed: list[str],
            reason: str = "", check_run: int | None = None, checks: dict | None = None) -> int:
    """定稿当前这张图。failed:AI / 代码不通过的项(面板算好传进来,快照进 checks_json);有不通过项时 reason 必填。"""
    row = repo.get(store, table, row_id)
    path = row[field(table, slot)]
    if not path:
        raise ValueError(f"「{row['name']}」还没有{LABEL[(table, slot)]}")
    f = Path(data_root) / path
    if not f.is_file():
        raise ValueError(f"「{row['name']}」的{LABEL[(table, slot)]}不在本机:{path}")
    if failed and not reason.strip():
        raise ValueError(f"「{row['name']}」的{LABEL[(table, slot)]}有不通过的项({'、'.join(failed)}),要写理由才能定稿")
    if current(store, table, row_id, slot) is not None:
        raise ValueError(f"「{row['name']}」的这张{LABEL[(table, slot)]}已经定稿了")
    from tools.serve.dispatch import sha256_file      # serve 依赖 drama,这里用到时再取
    with store.write() as c:
        cur = c.execute("INSERT INTO asset_approval (table_name, row_id, slot, path, sha256, reason, check_run,"
                        " checks_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (table, row_id, slot, path, sha256_file(f), reason.strip(), check_run,
                         json.dumps({"failed": failed, **(checks or {})}, ensure_ascii=False), now()))
        return cur.lastrowid


def unapproved(store: Store, items: list[tuple[str, int, str]]) -> list[str]:
    """[(table, row_id, slot)] 里有图但没定稿的,给就绪清单 / 规划闸门用。没图的不在这里管(缺图另有检查)。"""
    out = []
    for table, row_id, slot in items:
        row = repo.get(store, table, row_id)
        if row[field(table, slot)] and current(store, table, row_id, slot) is None:
            out.append(f"「{row['name']}」的{LABEL[(table, slot)]}还没定稿(到「资产图审」看 AI 检查结果后定稿)")
    return out
