# -*- coding: utf-8 -*-
"""角色四视图:以定稿的正面图为输入,Qwen 快档出一张横版四宫格(2026-09-26 云机实测:快档 2/2 合格、标准档 1/1 不合格)。"""
import json

import pytest

from tools.drama import repo
from tools.drama.tasks import create_task, set_task_state
from tools.drama.versions import confirm_pick, plan_pick
from tools.gen.assets import plan_fourview
from tools.gen.capability import MINIMAX_H3, QWEN_IMAGE_EDIT_2511

CAPS = {"video_cap": MINIMAX_H3, "image_cap": QWEN_IMAGE_EDIT_2511}


def _ready(store, sample):
    repo.update(store, "character", sample["lin"], final_prompt="A young woman in a cream blouse and navy skirt.")
    store.conn.execute("UPDATE drama SET style = 'realistic' WHERE id = ?", (sample["drama"],))


def test_fourview_is_the_fast_preset_from_the_front_image(store, sample):
    _ready(store, sample)
    row = repo.get(store, "task", plan_fourview(store, sample["lin"], seed=11))
    p = json.loads(row["params_json"])
    assert (row["target_type"], row["kind"], row["seed"]) == ("character", "image", 11)
    assert (p["preset"], p["size"], p["writes"], p["image_refs"]) == ("fast", [1664, 928], "fourview_path",
                                                                     ["assets/lin.png"])
    assert row["prompt"].startswith("Turn <Picture 1> into a character turnaround sheet of the same person")
    assert "front view, left side view, back view, right side view" in row["prompt"]
    assert "A young woman in a cream blouse" in row["prompt"] and "fictional person" in row["prompt"]
    assert "photorealistic" in row["prompt"] and "No text" in row["prompt"]


def test_fourview_needs_the_front_image_style_and_description(store, sample):
    repo.update(store, "character", sample["lin"], turnaround_path=None)
    with pytest.raises(ValueError) as e:
        plan_fourview(store, sample["lin"], seed=1)
    assert "正面定妆图" in str(e.value) and "风格预设" in str(e.value) and "final_prompt" in str(e.value)


def _done(store, ttype, tid, writes, path):
    t = create_task(store, kind="image", target_type=ttype, target_id=tid, model="m", params={"writes": writes},
                    seed=1, prompt="p", refs=[])
    set_task_state(store, t, "queued", external_id="x")
    set_task_state(store, t, "done", result_path=path)
    return t


def test_new_fourview_only_voids_videos_of_bound_segments(store, sample):
    repo.update(store, "segment", sample["seg"], subtitle_path="subs/s1.srt")
    pick = plan_pick(store, _done(store, "character", sample["lin"], "fourview_path", "fv/new.png"), **CAPS)
    assert (pick.impact.videos, pick.impact.frames, pick.impact.videos_only) == (1, 0, True)
    confirm_pick(store, pick)
    seg = repo.get(store, "segment", sample["seg"])
    assert seg["first_frame_path"] == "frames/s1.png" and seg["video_path"] is None and seg["subtitle_path"] is None
    assert repo.get(store, "character", sample["lin"])["fourview_path"] == "fv/new.png"


def test_new_front_image_drops_the_fourview_made_from_the_old_one(store, sample):
    repo.update(store, "character", sample["lin"], fourview_path="fv/old.png")
    pick = plan_pick(store, _done(store, "character", sample["lin"], "turnaround_path", "assets/lin2.png"), **CAPS)
    assert "四视图" in pick.summary()
    confirm_pick(store, pick)
    lin = repo.get(store, "character", sample["lin"])
    assert lin["turnaround_path"] == "assets/lin2.png" and lin["fourview_path"] is None
