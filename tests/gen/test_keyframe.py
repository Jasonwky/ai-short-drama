# -*- coding: utf-8 -*-
"""中间关键帧的规划:出关键帧图(与首帧同一条路)、视频多钉一个锚点(设计 specs/2026-10-09-中间关键帧-design.md §一)。"""
import json

import pytest

from tools.drama import repo
from tools.gen.planner import key_frame_problems, plan_first_frame, plan_key_frame, plan_video
from tools.gen.workflows import QWEN_MODEL


def _task(store, t):
    row = repo.get(store, "task", t)
    return row, json.loads(row["params_json"])


def _key(store, seg, **fields):
    repo.update(store, "segment", seg, **{"key_time_sec": 4.0, "key_image_prompt": "@林小雨 在 @老宅庭院 转身,背对镜头,握着 @玉佩",
                                          "key_frame_path": "frames/k.png", **fields})


def test_keyframe_image_goes_the_same_way_as_the_first_frame(store, sample):
    _key(store, sample["seg"], key_frame_path=None)
    row, p = _task(store, plan_key_frame(store, sample["seg"], seed=7))
    first, fp = _task(store, plan_first_frame(store, sample["seg"], seed=7))
    assert (row["kind"], row["model"], row["seed"]) == ("image", QWEN_MODEL, 7)
    assert row["prompt"] == "Picture 1(林小雨) 在 Picture 2(老宅庭院) 转身,背对镜头,握着 Picture 3(玉佩)"
    assert p["writes"] == "key_frame_path"
    assert {k: v for k, v in p.items() if k != "writes"} == {k: v for k, v in fp.items() if k != "writes"}


def test_no_keyframe_set_refuses_to_plan_one(store, sample):
    assert key_frame_problems(store, sample["seg"]) == ["没设关键帧(在「编辑段落」里设第几秒)"]
    with pytest.raises(ValueError, match="没设关键帧"):
        plan_key_frame(store, sample["seg"], seed=1)


def test_keyframe_image_needs_its_prompt(store, sample):
    _key(store, sample["seg"], key_image_prompt=" ")
    assert "缺少关键帧图提示词" in key_frame_problems(store, sample["seg"])


def test_keyframe_works_with_every_reference_mode(store, sample):
    """关键帧是叠加项,不看参考模式。"""
    _key(store, sample["seg"])
    for mode in ("first", "last", "first_last"):
        repo.update(store, "segment", sample["seg"], reference_mode=mode)
        assert key_frame_problems(store, sample["seg"]) == []


def test_video_pins_the_keyframe_after_the_mode_anchors(store, sample):
    _key(store, sample["seg"])
    row, p = _task(store, plan_video(store, sample["seg"], seed=1))
    assert p["anchors"] == [["frames/s1.png", 0], ["frames/k.png", 96]]
    assert p["image_refs"] == ["frames/s1.png"]                                  # 只当锚点,不占参考图槽
    assert json.loads(row["refs_json"]) == ["frames/s1.png", "frames/k.png"]     # 溯源链里有它


def test_first_last_plus_keyframe_has_three_anchors(store, sample):
    _key(store, sample["seg"])
    repo.update(store, "segment", sample["seg"], reference_mode="first_last", last_frame_path="frames/end.png",
                last_image_prompt="@林小雨 走出 @老宅庭院")
    _, p = _task(store, plan_video(store, sample["seg"], seed=1))
    assert p["anchors"] == [["frames/s1.png", 0], ["frames/end.png", -1], ["frames/k.png", 96]]


def test_video_without_a_keyframe_is_unchanged(store, sample):
    """没设关键帧时参数与以前一模一样(防回归)。"""
    _, p = _task(store, plan_video(store, sample["seg"], seed=1))
    assert p["anchors"] == [["frames/s1.png", 0]]


def test_video_waits_for_the_keyframe_image(store, sample):
    _key(store, sample["seg"], key_frame_path=None)
    with pytest.raises(ValueError, match="还没有关键帧图"):
        plan_video(store, sample["seg"], seed=1)
