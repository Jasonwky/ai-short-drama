# -*- coding: utf-8 -*-
import pytest

from tools.drama import repo
from tools.drama.invalidation import apply, impact
from tools.gen.capability import MINIMAX_H3, QWEN_IMAGE_EDIT_2511

CAPS = {"video_cap": MINIMAX_H3, "image_cap": QWEN_IMAGE_EDIT_2511}


def _host(store, gpu):
    from tools.serve.hosts import add_host
    add_host(store, label="a", ssh_host="h", ssh_port=1, ssh_user="root", key_ref="k", local_port=8188,
             vram_gb=24.0, models_dir="/m", gpu=gpu)


def test_character_change_hits_only_bound_segments(store, sample):
    _host(store, "RTX 4090")
    imp = impact(store, "character", sample["lin"], **CAPS)
    assert imp.segment_ids == (sample["seg"],)
    assert (imp.videos, imp.frames) == (1, 1)
    # 8 秒片 = 192 帧 → 20 步 × 44.4 s;外加一张 4 步参考帧 25.3 s
    assert imp.est_seconds == pytest.approx(20 * 44.4 + 25.3)
    assert imp.summary() == "这会重跑 1 条视频、1 张参考帧,约 0.3 小时(按 RTX 4090 估算)"


def test_cost_follows_the_registered_card(store, sample):
    _host(store, "RTX 4080 SUPER")
    assert impact(store, "character", sample["lin"], **CAPS).est_seconds == pytest.approx(20 * 72.3 + 41.0)


def test_no_card_registered_still_lists_the_impact(store, sample):
    imp = impact(store, "character", sample["lin"], **CAPS)
    assert imp.est_seconds is None and (imp.videos, imp.frames) == (1, 1)
    assert imp.summary() == "这会重跑 1 条视频、1 张参考帧,无法估时:还没登记云机"


def test_unbound_character_affects_nothing(store, sample):
    imp = impact(store, "character", sample["shen"], **CAPS)
    assert imp.segment_ids == () and imp.summary() == "不影响任何已生成的产物"


@pytest.mark.parametrize("event,key", [("scene", "yard"), ("prop", "jade"), ("script", "episode"),
                                       ("style", "drama"), ("layout", "seg"), ("segment_prompt", "seg")])
def test_every_event_reaches_the_segment(store, sample, event, key):
    assert impact(store, event, sample[key], **CAPS).segment_ids == (sample["seg"],)


def test_segments_without_outputs_cost_nothing(store, sample):
    s2 = repo.add_segment(store, sample["episode"], 2, scene_id=sample["yard"], duration_sec=8.0)
    imp = impact(store, "scene", sample["yard"], **CAPS)
    assert imp.segment_ids == (sample["seg"], s2) and imp.videos == 1


def test_video_without_duration_is_inconsistent(store, sample):
    repo.update(store, "segment", sample["seg"], duration_sec=None)
    with pytest.raises(ValueError, match="没有时长"):
        impact(store, "scene", sample["yard"], **CAPS)


def test_unknown_event_rejected(store, sample):
    with pytest.raises(ValueError, match="未知的变更事件"):
        impact(store, "weather", 1, **CAPS)


def test_apply_clears_output_pointers_only_after_confirm(store, sample):
    imp = impact(store, "character", sample["lin"], **CAPS)
    assert repo.get(store, "segment", sample["seg"])["video_path"] == "videos/s1.mp4"  # impact 只算不改
    apply(store, imp)
    seg = repo.get(store, "segment", sample["seg"])
    assert seg["video_path"] is None and seg["first_frame_path"] is None
    assert seg["video_prompt"]  # 提示词等创作内容不动


def test_character_change_also_voids_every_keyframe(store, sample):
    """关键帧图是用角色 / 场景 / 道具合成的:换了正面图,旧关键帧图不能再被钉进视频 —— 每一张都是。"""
    k3 = repo.add_keyframe(store, sample["seg"], 3.0, "p", "frames/k3.png")
    k6 = repo.add_keyframe(store, sample["seg"], 6.0, "q", "frames/k6.png")
    imp = impact(store, "character", sample["lin"], **CAPS)
    assert (imp.videos, imp.frames) == (1, 3)                    # 首帧 + 两张关键帧
    apply(store, imp)
    seg = repo.get(store, "segment", sample["seg"])
    assert (seg["first_frame_path"], seg["video_path"]) == (None, None)
    assert [(k["id"], k["time_sec"], k["image_prompt"], k["frame_path"]) for k in repo.keyframes(store, sample["seg"])] \
        == [(k3, 3.0, "p", None), (k6, 6.0, "q", None)]          # 设定还在,只清产物


def test_keyframe_only_edit_spares_the_first_frame(store, sample):
    from tools.drama.invalidation import keyframe_edit
    repo.add_keyframe(store, sample["seg"], 4.0, "p", "frames/k.png")
    seg = repo.get(store, "segment", sample["seg"])
    moved = keyframe_edit(store, seg, images=0, **CAPS)                   # 只改时刻:关键帧图仍有效
    assert (moved.videos, moved.frames, moved.videos_only) == (1, 0, True)
    reprompted = keyframe_edit(store, seg, images=1, **CAPS)              # 改提示词:那张关键帧图也作废
    assert (reprompted.videos, reprompted.frames) == (1, 1) and "1 张参考帧" in reprompted.summary()
    repo.update(store, "segment", sample["seg"], video_path=None)
    no_video = keyframe_edit(store, repo.get(store, "segment", sample["seg"]), images=1, **CAPS)
    assert (no_video.videos, no_video.frames) == (0, 1) and no_video.summary().endswith("无法估时:还没登记云机")
    repo.update(store, "segment", sample["seg"], video_path="videos/s1.mp4")
    apply(store, reprompted)
    assert repo.get(store, "segment", sample["seg"])["first_frame_path"] == "frames/s1.png"


def test_two_pass_frames_cost_twice(store, sample):
    _host(store, "RTX 4090")
    repo.bind_characters(store, sample["seg"], [sample["lin"], sample["shen"]])     # 4 张参考图 → 两遍
    imp = impact(store, "character", sample["lin"], **CAPS)
    assert imp.est_seconds == pytest.approx(20 * 44.4 + 2 * 25.3)
