# -*- coding: utf-8 -*-
import json

import pytest

from tools.drama import repo
from tools.gen.planner import plan_first_frame, plan_video
from tools.gen.workflows import H3_MODEL, QWEN_MODEL


def _task(store, t):
    row = repo.get(store, "task", t)
    return row, json.loads(row["params_json"])


# ── 首帧 ──────────────────────────────────────────────

def test_first_frame_task_is_fully_specified(store, sample):
    row, p = _task(store, plan_first_frame(store, sample["seg"], seed=42))
    assert (row["kind"], row["model"], row["seed"], row["state"]) == ("image", QWEN_MODEL, 42, "pending")
    assert row["prompt"] == "Picture 1(林小雨) 站在 Picture 2(老宅庭院) 里,手里拿着 Picture 3(玉佩)"
    assert p["image_refs"] == ["assets/lin.png", "assets/yard.png", "assets/jade.png"]
    assert (p["size"], p["preset"], p["writes"], p["dropped"]) == ([928, 1664], "fast", "first_frame_path", [])


def test_first_frame_records_assets_that_fell_out_of_slots(store, sample):
    repo.bind_characters(store, sample["seg"], [sample["lin"], sample["shen"]])
    repo.update(store, "segment", sample["seg"],
                description="【镜头1】沈墨说：「你还留着它。」【镜头2】林小雨说：「这块玉佩，是我母亲留给我的。」",
                image_prompt="@林小雨 把 @玉佩 递给 @沈墨,@老宅庭院 黄昏")
    row, p = _task(store, plan_first_frame(store, sample["seg"], seed=1))
    assert p["dropped"] == ["玉佩"]
    # 台词多的林小雨占 1 号,场景 2 号,沈墨 3 号;玉佩出槽,退化成纯文字
    assert row["prompt"] == "Picture 1(林小雨) 把 玉佩 递给 Picture 3(沈墨),Picture 2(老宅庭院) 黄昏"
    assert p["image_refs"] == ["assets/lin.png", "assets/yard.png", "assets/shen.png"]


def test_first_frame_refuses_incomplete_content(store, sample):
    repo.update(store, "segment", sample["seg"], image_prompt="@林小雨 @王五")
    with pytest.raises(ValueError, match="@王五 不是本镜绑定的素材"):
        plan_first_frame(store, sample["seg"], seed=1)


def test_first_frame_only_for_first_mode(store, sample):
    repo.update(store, "segment", sample["seg"], reference_mode="text_only")
    with pytest.raises(ValueError, match="尚未实现"):
        plan_first_frame(store, sample["seg"], seed=1)


# ── 视频 ──────────────────────────────────────────────

def test_video_task_carries_voice_description_not_reference_audio(store, sample):
    row, p = _task(store, plan_video(store, sample["seg"], seed=7))
    assert (row["kind"], row["model"]) == ("video", H3_MODEL)
    assert row["prompt"] == ("<Picture 1> 是本段的首帧。林小雨 在 老宅庭院 低头看 玉佩,然后抬眼说话 "
                             "林小雨的声音:清亮女声。")
    assert (p["frames"], p["size"], p["audio_ref"]) == (192, [768, 1344], None)
    assert p["image_refs"] == ["frames/s1.png"] and p["writes"] == "video_path"


def test_video_needs_a_first_frame(store, sample):
    repo.update(store, "segment", sample["seg"], first_frame_path=None)
    with pytest.raises(ValueError, match="缺少首帧"):
        plan_video(store, sample["seg"], seed=1)


def test_video_is_not_blocked_by_runtime_checks(store, sample):
    # 规划阶段不看云机 —— 云机关着也能先把任务排好
    assert plan_video(store, sample["seg"], seed=1) > 0


def test_video_refuses_modes_ref2va_cannot_do(store, sample):
    repo.update(store, "segment", sample["seg"], reference_mode="first_last", last_frame_path="frames/s1_end.png")
    with pytest.raises(ValueError, match="尚未实现"):
        plan_video(store, sample["seg"], seed=1)
