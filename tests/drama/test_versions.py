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
