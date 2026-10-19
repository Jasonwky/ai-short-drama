# -*- coding: utf-8 -*-
import pytest

from tools.drama import repo
from tools.drama.tasks import create_task, set_task_state
from tools.drama.versions import confirm_pick, plan_pick
from tools.gen.capability import MINIMAX_H3, QWEN_IMAGE_EDIT_2511

CAPS = {"video_cap": MINIMAX_H3, "image_cap": QWEN_IMAGE_EDIT_2511}


def _done(store, target_type, target_id, writes, path):
    t = create_task(store, kind="image", target_type=target_type, target_id=target_id, model="m",
                    params={"writes": writes}, seed=1, prompt="p", refs=[])
    set_task_state(store, t, "queued", external_id="x")
    set_task_state(store, t, "done", result_path=path)
    return t


def test_new_first_frame_costs_this_segments_video_only(store, sample):
    t = _done(store, "segment", sample["seg"], "first_frame_path", "outputs/tasks/9/new.png")
    pick = plan_pick(store, t, **CAPS)
    assert (pick.impact.videos, pick.impact.frames) == (1, 0) and "1 条视频" in pick.summary()
    assert repo.get(store, "segment", sample["seg"])["video_path"] == "videos/s1.mp4"   # plan 不改任何东西
    confirm_pick(store, pick)
    seg = repo.get(store, "segment", sample["seg"])
    assert seg["first_frame_path"] == "outputs/tasks/9/new.png" and seg["video_path"] is None


def test_new_character_image_invalidates_bound_segments(store, sample):
    t = _done(store, "character", sample["lin"], "turnaround_path", "outputs/tasks/3/lin2.png")
    pick = plan_pick(store, t, **CAPS)
    assert pick.impact.segment_ids == (sample["seg"],) and pick.impact.videos == 1
    confirm_pick(store, pick)
    assert repo.get(store, "character", sample["lin"])["turnaround_path"] == "outputs/tasks/3/lin2.png"
    seg = repo.get(store, "segment", sample["seg"])
    assert seg["first_frame_path"] is None and seg["video_path"] is None


def test_unbound_character_costs_nothing(store, sample):
    t = _done(store, "character", sample["shen"], "turnaround_path", "outputs/tasks/4/shen2.png")
    assert plan_pick(store, t, **CAPS).summary() == "不影响任何已生成的产物"


def test_picking_the_current_version_is_refused(store, sample):
    t = _done(store, "segment", sample["seg"], "first_frame_path", "frames/s1.png")
    with pytest.raises(ValueError, match="已经是当前版"):
        plan_pick(store, t, **CAPS)


def test_unfinished_task_cannot_be_picked(store, sample):
    t = create_task(store, kind="image", target_type="segment", target_id=sample["seg"], model="m",
                    params={"writes": "first_frame_path"}, seed=1, prompt="p", refs=[])
    with pytest.raises(ValueError, match="只有完成的任务"):
        plan_pick(store, t, **CAPS)


def test_new_video_drops_the_old_videos_subtitles(store, sample):
    """字幕的时间轴是对着那条视频算的,换了视频就对不上了。"""
    repo.update(store, "segment", sample["seg"], subtitle_path="outputs/tasks/5/subtitles.srt")
    t = _done(store, "segment", sample["seg"], "video_path", "outputs/tasks/9/new.mp4")
    confirm_pick(store, plan_pick(store, t, **CAPS))
    seg = repo.get(store, "segment", sample["seg"])
    assert seg["video_path"] == "outputs/tasks/9/new.mp4" and seg["subtitle_path"] is None


def test_new_subtitles_keep_the_video(store, sample):
    t = _done(store, "segment", sample["seg"], "subtitle_path", "outputs/tasks/9/subtitles.srt")
    confirm_pick(store, plan_pick(store, t, **CAPS))
    seg = repo.get(store, "segment", sample["seg"])
    assert seg["subtitle_path"] == "outputs/tasks/9/subtitles.srt" and seg["video_path"] == "videos/s1.mp4"


def _done_key(store, seg, keyframe_id, path):
    t = create_task(store, kind="image", target_type="segment", target_id=seg, model="m",
                    params={"writes": "key_frame_path", "keyframe_id": keyframe_id}, seed=1, prompt="p", refs=[])
    set_task_state(store, t, "queued", external_id="x")
    set_task_state(store, t, "done", result_path=path)
    return t


def test_new_keyframe_costs_this_segments_video_only(store, sample):
    """换某一张关键帧:写到那一张自己的行上,别的关键帧、首帧不动,只作废本段视频。"""
    repo.add_keyframe(store, sample["seg"], 3.0, "p", "frames/k3.png")
    k6 = repo.add_keyframe(store, sample["seg"], 6.0, "p", "frames/k6.png")
    pick = plan_pick(store, _done_key(store, sample["seg"], k6, "outputs/tasks/9/k6b.png"), **CAPS)
    assert (pick.impact.videos, pick.impact.frames) == (1, 0)
    confirm_pick(store, pick)
    seg = repo.get(store, "segment", sample["seg"])
    assert (seg["video_path"], seg["first_frame_path"]) == (None, "frames/s1.png")
    assert [k["frame_path"] for k in repo.keyframes(store, sample["seg"])] == ["frames/k3.png", "outputs/tasks/9/k6b.png"]


def test_deleted_keyframe_versions_cannot_be_picked(store, sample):
    k = repo.add_keyframe(store, sample["seg"], 3.0, "p", "frames/k.png")
    t = _done_key(store, sample["seg"], k, "outputs/tasks/9/k2.png")
    repo.delete_keyframe(store, k)
    with pytest.raises(ValueError, match="这张关键帧已经删除"):
        plan_pick(store, t, **CAPS)


def test_region_replace_version_is_picked_like_any_frame(store, sample):
    """局部替换出的新版本:与普通首帧同一个写回位置,挑它才替换当前帧,照常让视频作废。"""
    from tools.gen.planner import plan_region
    t = plan_region(store, sample["seg"], "first_frame_path", source="frames/s1.png", size=(928, 1664),
                    box=[0, 0, 400, 400], asset=("character", sample["lin"]), note="", seed=1)
    set_task_state(store, t, "queued", external_id="x")
    set_task_state(store, t, "done", result_path="outputs/tasks/9/region.png")
    assert repo.get(store, "segment", sample["seg"])["first_frame_path"] == "frames/s1.png"     # 进历史,不自动替换
    pick = plan_pick(store, t, **CAPS)
    assert pick.impact.videos == 1
    confirm_pick(store, pick)
    seg = repo.get(store, "segment", sample["seg"])
    assert seg["first_frame_path"] == "outputs/tasks/9/region.png" and seg["video_path"] is None
