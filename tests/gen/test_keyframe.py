# -*- coding: utf-8 -*-
"""中间关键帧的规划:出关键帧图(与首帧同一条路)、视频按时刻先后逐张钉锚点(设计 specs/2026-10-15-多关键帧与草稿档-design.md §2.3–2.4)。"""
import json

import pytest

from tools.drama import repo
from tools.gen.planner import key_frame_problems, plan_first_frame, plan_key_frame, plan_video
from tools.gen.workflows import QWEN_MODEL

TURN = "@林小雨 在 @老宅庭院 转身,背对镜头,握着 @玉佩"


def _task(store, t):
    row = repo.get(store, "task", t)
    return row, json.loads(row["params_json"])


def _key(store, seg, time_sec=4.0, prompt=TURN, path="frames/k.png"):
    return repo.add_keyframe(store, seg, time_sec, prompt, path)


def test_keyframe_image_goes_the_same_way_as_the_first_frame(store, sample):
    k = _key(store, sample["seg"], path=None)
    row, p = _task(store, plan_key_frame(store, k, seed=7))
    first, fp = _task(store, plan_first_frame(store, sample["seg"], seed=7))
    assert (row["kind"], row["model"], row["seed"], row["target_id"]) == ("image", QWEN_MODEL, 7, sample["seg"])
    assert row["prompt"] == "Picture 1(林小雨) 在 Picture 2(老宅庭院) 转身,背对镜头,握着 Picture 3(玉佩)"
    assert (p["writes"], p["keyframe_id"]) == ("key_frame_path", k)              # 任务挂在段落上,参数里记写回哪一张
    assert p["frame"]["text"] == TURN                                            # 快照记的是这张关键帧自己的句子
    skip = ("writes", "keyframe_id", "frame")
    assert {a: v for a, v in p.items() if a not in skip} == {a: v for a, v in fp.items() if a not in skip}


def test_keyframe_image_needs_its_prompt(store, sample):
    k = _key(store, sample["seg"], prompt=" ")
    assert "缺少关键帧图提示词" in key_frame_problems(store, k)
    with pytest.raises(ValueError, match="缺少关键帧图提示词"):
        plan_key_frame(store, k, seed=1)


def test_keyframe_works_with_every_reference_mode(store, sample):
    """关键帧是叠加项,不看参考模式。"""
    k = _key(store, sample["seg"])
    for mode in ("first", "last", "first_last"):
        repo.update(store, "segment", sample["seg"], reference_mode=mode)
        assert key_frame_problems(store, k) == []


def test_video_pins_the_keyframe_after_the_mode_anchors(store, sample):
    _key(store, sample["seg"])
    row, p = _task(store, plan_video(store, sample["seg"], seed=1))
    assert p["anchors"] == [["frames/s1.png", 0], ["frames/k.png", 96]]
    assert p["image_refs"] == ["frames/s1.png"]                                  # 只当锚点,不占参考图槽
    assert json.loads(row["refs_json"]) == ["frames/s1.png", "frames/k.png"]     # 溯源链里有它


def test_several_keyframes_are_pinned_in_time_order(store, sample):
    """加的先后不算,按时刻先后钉(调研:第 72、144 帧两个锚点都钉中)。"""
    _key(store, sample["seg"], 6.0, path="frames/k6.png")
    _key(store, sample["seg"], 3.0, path="frames/k3.png")
    _, p = _task(store, plan_video(store, sample["seg"], seed=1))
    assert p["anchors"] == [["frames/s1.png", 0], ["frames/k3.png", 72], ["frames/k6.png", 144]]


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


def test_video_waits_for_every_keyframe_image(store, sample):
    _key(store, sample["seg"], 3.0)
    _key(store, sample["seg"], 6.0, path=None)
    with pytest.raises(ValueError, match="第 6 秒的关键帧还没有图"):
        plan_video(store, sample["seg"], seed=1)
