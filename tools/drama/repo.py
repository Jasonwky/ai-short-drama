# -*- coding: utf-8 -*-
"""剧 / 集 / 资产 / 段落的读写。

**写入受控**(CLAUDE.md LLM 纪律「白名单写入范围」):每张表只允许写声明过的字段,
写别的字段直接抛错 —— 将来 LLM 产出的结构化数据经这里落盘,越界要当场失败。

跨剧绑定当场拒绝:外键只能保证「资产存在」,保证不了「资产属于这部剧」。
"""
from __future__ import annotations

import sqlite3

from tools.drama.db import Store, now

# 各表允许调用方写入的字段。主键、外键归属、创建时间由本模块自己管。
_COLUMNS: dict[str, set[str]] = {
    "episode": {"source_text", "outline", "script"},
    "character": {"appearance", "styling", "voice_desc", "final_prompt", "seed",
                  "turnaround_path", "voice_ref_path"},
    "scene": {"episode_id", "location", "time", "lighting", "final_prompt", "image_path"},
    "prop": {"description", "final_prompt", "image_path"},
    # scene_id 不在这里:改场景要走 set_scene(),它会核对归属
    "segment": {"duration_sec", "shot_type", "angle", "movement", "description", "result",
                "atmosphere", "layout", "image_prompt", "video_prompt", "bgm_prompt", "sfx_prompt",
                "reference_mode", "first_frame_path", "last_frame_path", "video_path", "subtitle_path"},
}
_READABLE = {"drama", "episode", "character", "scene", "prop", "segment", "task", "host"}


def _check_cols(table: str, fields: dict) -> None:
    bad = set(fields) - _COLUMNS[table]
    if bad:
        raise ValueError(f"{table} 不允许写入字段:{sorted(bad)}")


def _insert(store: Store, table: str, fields: dict) -> int:
    cols = ", ".join(fields)
    marks = ", ".join("?" * len(fields))
    with store.write() as c:
        return c.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(fields.values())).lastrowid


def get(store: Store, table: str, row_id: int) -> sqlite3.Row:
    if table not in _READABLE:
        raise ValueError(f"未知表 {table}")
    row = store.conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        raise KeyError(f"{table} {row_id} 不存在")
    return row


def update(store: Store, table: str, row_id: int, **fields) -> None:
    if table not in _COLUMNS:
        raise ValueError(f"表 {table} 不走通用更新")
    _check_cols(table, fields)
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    with store.write() as c:
        n = c.execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*fields.values(), row_id)).rowcount
    if n == 0:
        raise KeyError(f"{table} {row_id} 不存在")


# ── 创建 ──────────────────────────────────────────────────────

def create_drama(store: Store, title: str, aspect_ratio: str, style: str = "") -> int:
    return _insert(store, "drama", {"title": title, "aspect_ratio": aspect_ratio,
                                    "style": style, "created_at": now()})


def add_episode(store: Store, drama_id: int, number: int, source_type: str, **fields) -> int:
    _check_cols("episode", fields)
    return _insert(store, "episode", {"drama_id": drama_id, "number": number,
                                      "source_type": source_type, **fields})


def add_character(store: Store, drama_id: int, name: str, **fields) -> int:
    _check_cols("character", fields)
    return _insert(store, "character", {"drama_id": drama_id, "name": name, **fields})


def add_scene(store: Store, drama_id: int, name: str, **fields) -> int:
    _check_cols("scene", fields)
    return _insert(store, "scene", {"drama_id": drama_id, "name": name, **fields})


def add_prop(store: Store, drama_id: int, name: str, **fields) -> int:
    _check_cols("prop", fields)
    return _insert(store, "prop", {"drama_id": drama_id, "name": name, **fields})


def add_segment(store: Store, episode_id: int, number: int, scene_id: int | None = None, **fields) -> int:
    _check_cols("segment", fields)
    if scene_id is not None:
        _check_same_drama(store.conn, _drama_of_episode(store.conn, episode_id), "scene", scene_id)
    return _insert(store, "segment", {"episode_id": episode_id, "number": number,
                                      "scene_id": scene_id, **fields})


# ── 归属与绑定 ────────────────────────────────────────────────

def _drama_of_episode(c: sqlite3.Connection, episode_id: int) -> int:
    row = c.execute("SELECT drama_id FROM episode WHERE id = ?", (episode_id,)).fetchone()
    if row is None:
        raise KeyError(f"episode {episode_id} 不存在")
    return row[0]


def _drama_of_segment(c: sqlite3.Connection, segment_id: int) -> int:
    row = c.execute("SELECT e.drama_id FROM segment s JOIN episode e ON e.id = s.episode_id "
                    "WHERE s.id = ?", (segment_id,)).fetchone()
    if row is None:
        raise KeyError(f"segment {segment_id} 不存在")
    return row[0]


def _check_same_drama(c: sqlite3.Connection, drama_id: int, table: str, row_id: int) -> None:
    row = c.execute(f"SELECT drama_id FROM {table} WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        raise KeyError(f"{table} {row_id} 不存在")
    if row[0] != drama_id:
        raise ValueError(f"{table} {row_id} 属于另一部剧,不能绑到这里")


def set_scene(store: Store, segment_id: int, scene_id: int | None) -> None:
    with store.write() as c:
        if scene_id is not None:
            _check_same_drama(c, _drama_of_segment(c, segment_id), "scene", scene_id)
        c.execute("UPDATE segment SET scene_id = ? WHERE id = ?", (scene_id, segment_id))


def _bind(store: Store, segment_id: int, table: str, link: str, col: str, ids: list[int]) -> None:
    with store.write() as c:
        drama = _drama_of_segment(c, segment_id)
        for i in ids:
            _check_same_drama(c, drama, table, i)
        c.execute(f"DELETE FROM {link} WHERE segment_id = ?", (segment_id,))
        c.executemany(f"INSERT INTO {link} (segment_id, {col}, position) VALUES (?, ?, ?)",
                      [(segment_id, i, pos) for pos, i in enumerate(ids)])


def bind_characters(store: Store, segment_id: int, character_ids: list[int]) -> None:
    """整体替换本段落的角色绑定。列表顺序即绑定顺序。"""
    _bind(store, segment_id, "character", "segment_character", "character_id", character_ids)


def bind_props(store: Store, segment_id: int, prop_ids: list[int]) -> None:
    """整体替换本段落的道具绑定。列表顺序即绑定顺序。"""
    _bind(store, segment_id, "prop", "segment_prop", "prop_id", prop_ids)


def segment_assets(store: Store, segment_id: int) -> dict:
    """本段落绑定的资产:{"characters": [...], "scene": Row|None, "props": [...]},按绑定顺序。"""
    c = store.conn
    seg = get(store, "segment", segment_id)
    chars = c.execute("SELECT ch.* FROM segment_character sc JOIN character ch ON ch.id = sc.character_id "
                      "WHERE sc.segment_id = ? ORDER BY sc.position", (segment_id,)).fetchall()
    props = c.execute("SELECT p.* FROM segment_prop sp JOIN prop p ON p.id = sp.prop_id "
                      "WHERE sp.segment_id = ? ORDER BY sp.position", (segment_id,)).fetchall()
    scene = get(store, "scene", seg["scene_id"]) if seg["scene_id"] is not None else None
    return {"characters": chars, "scene": scene, "props": props}
