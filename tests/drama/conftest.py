# -*- coding: utf-8 -*-
import pytest

from tools.drama import repo
from tools.drama.db import Store


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


def approve_all(store):
    """样例里已有的资产图都算定稿过(spec 2026-10-28 第 6.1 节;按路径认,sha 记个占位)。测试换了图要重新定稿。"""
    for table, slot, col in (("character", "image", "turnaround_path"), ("character", "fourview", "fourview_path"),
                             ("scene", "image", "image_path"), ("prop", "image", "image_path")):
        for r in store.conn.execute(f"SELECT id, {col} FROM {table} WHERE {col} IS NOT NULL").fetchall():
            if not store.conn.execute("SELECT 1 FROM asset_approval WHERE table_name = ? AND row_id = ? AND slot = ?"
                                      " AND path = ?", (table, r[0], slot, r[1])).fetchone():
                store.conn.execute("INSERT INTO asset_approval (table_name, row_id, slot, path, sha256, created_at)"
                                   " VALUES (?, ?, ?, ?, 'test', 't')", (table, r[0], slot, r[1]))


@pytest.fixture
def sample(store):
    """一部剧、一集、两个角色、一个场景、一个道具、一个已出过片的段落。

    段落只绑了林小雨和玉佩;沈墨存在但没绑 —— 用来测「只影响绑定了的段落」。
    """
    d = repo.create_drama(store, "玉佩", "9:16", "realistic")
    ep = repo.add_episode(store, d, 1, "script", script="字" * 1000)
    lin = repo.add_character(store, d, "林小雨", turnaround_path="assets/lin.png", fourview_path="assets/lin_4v.png",
                             voice_desc="清亮女声")
    shen = repo.add_character(store, d, "沈墨", turnaround_path="assets/shen.png")
    yard = repo.add_scene(store, d, "老宅庭院", image_path="assets/yard.png")
    jade = repo.add_prop(store, d, "玉佩", image_path="assets/jade.png")
    seg = repo.add_segment(
        store, ep, 1, scene_id=yard, duration_sec=8.0, reference_mode="first",
        first_frame_path="frames/s1.png", video_path="videos/s1.mp4",
        description="【镜头1】林小雨低头看着玉佩。【镜头2】林小雨说：「这块玉佩，是我母亲留给我的。」",
        image_prompt="@林小雨 站在 @老宅庭院 里,手里拿着 @玉佩",
        video_prompt="@林小雨 在 @老宅庭院 低头看 @玉佩,然后抬眼说话")
    repo.bind_characters(store, seg, [lin])
    repo.bind_props(store, seg, [jade])
    approve_all(store)
    return {"drama": d, "episode": ep, "lin": lin, "shen": shen, "yard": yard, "jade": jade, "seg": seg}
