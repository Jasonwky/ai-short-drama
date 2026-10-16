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
                  "turnaround_path", "fourview_path", "voice_ref_path"},
    "scene": {"episode_id", "location", "time", "lighting", "final_prompt", "image_path"},
    "prop": {"description", "final_prompt", "image_path"},
    # scene_id 不在这里:改场景要走 set_scene(),它会核对归属
    "segment": {"duration_sec", "shot_type", "angle", "movement", "description", "result",
                "atmosphere", "layout", "image_prompt", "last_image_prompt", "video_prompt", "bgm_prompt", "sfx_prompt",
                "reference_mode", "first_frame_path", "last_frame_path", "video_path", "subtitle_path",
                "key_time_sec", "key_image_prompt", "key_frame_path"},
    # segment_id 不在这里:关键帧建在哪段就属于哪段,不挪
    "segment_keyframe": {"time_sec", "image_prompt", "frame_path"},
}
_READABLE = {"drama", "episode", "character", "scene", "prop", "segment", "task", "host", "segment_keyframe"}


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
    row = {"episode_id": episode_id, "number": number, "scene_id": scene_id, **fields}
    with store.write() as c:
        new_id = next_segment_id(c)
        c.execute(f"INSERT INTO segment (id, {', '.join(row)}) VALUES (?, {', '.join('?' * len(row))})",
                  (new_id, *row.values()))
    return new_id


def add_keyframe(store: Store, segment_id: int, time_sec: float, image_prompt: str = "",
                 frame_path: str | None = None) -> int:
    """给段落加一张中间关键帧。张数上限、时刻范围由 readiness 判(超了给人话原因),这里不拦。"""
    get(store, "segment", segment_id)
    return _insert(store, "segment_keyframe", {"segment_id": segment_id, "time_sec": time_sec,
                                               "image_prompt": image_prompt, "frame_path": frame_path})


def keyframes(store: Store, segment_id: int) -> list[sqlite3.Row]:
    """本段的关键帧,按时刻先后。"""
    return store.conn.execute("SELECT * FROM segment_keyframe WHERE segment_id = ? ORDER BY time_sec, id",
                              (segment_id,)).fetchall()


def delete_keyframe(store: Store, keyframe_id: int) -> None:
    """删一张关键帧。它的生成历史(task)与文件保留;编号不复用。"""
    with store.write() as c:
        if c.execute("DELETE FROM segment_keyframe WHERE id = ?", (keyframe_id,)).rowcount == 0:
            raise KeyError(f"segment_keyframe {keyframe_id} 不存在")


_HWM = "segment_id_hwm"


def next_segment_id(c: sqlite3.Connection) -> int:
    """段落编号**永不复用**:段落可以被删(重拆分镜),它的生成历史留在 task 表、成片导出记录里还引用着它;
    新段落若拿到旧编号,旧历史就会被当成它的版本。取「现有段落、task 目标、删除时记下的高水位」中最大的 + 1。
    调用方必须在写事务里调用并紧接着插入(单写锁保证不撞)。"""
    return c.execute(
        "SELECT MAX(m) + 1 FROM (SELECT COALESCE(MAX(id), 0) AS m FROM segment UNION ALL "
        "SELECT COALESCE(MAX(target_id), 0) FROM task WHERE target_type = 'segment' UNION ALL "
        "SELECT COALESCE(MAX(CAST(value AS INTEGER)), 0) FROM meta WHERE key = ?)", (_HWM,)).fetchone()[0]


def delete_segments(store: Store, episode_id: int) -> list[int]:
    """删掉一集的全部段落(重拆分镜 / 重写剧本,人确认后才调用)。绑定随之删除;
    **生成历史(task)与文件都保留** —— 溯源和旧成片仍可查。返回被删的段落 id。"""
    with store.write() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM segment WHERE episode_id = ? ORDER BY number", (episode_id,))]
        c.execute("DELETE FROM segment WHERE episode_id = ?", (episode_id,))
        if ids:
            c.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET "
                      "value = MAX(CAST(value AS INTEGER), CAST(excluded.value AS INTEGER))", (_HWM, str(max(ids))))
    return ids


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
