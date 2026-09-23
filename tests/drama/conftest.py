# -*- coding: utf-8 -*-
import pytest

from tools.drama import repo
from tools.drama.db import Store


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def sample(store):
    """一部剧、一集、两个角色、一个场景、一个道具、一个已出过片的段落。

    段落只绑了林小雨和玉佩;沈墨存在但没绑 —— 用来测「只影响绑定了的段落」。
    """
    d = repo.create_drama(store, "玉佩", "9:16")
    ep = repo.add_episode(store, d, 1, "script", script="字" * 1000)
    lin = repo.add_character(store, d, "林小雨", turnaround_path="assets/lin.png", voice_desc="清亮女声")
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
    return {"drama": d, "episode": ep, "lin": lin, "shen": shen, "yard": yard, "jade": jade, "seg": seg}
