# -*- coding: utf-8 -*-
import json

import pytest

from tools.drama import repo
from tools.gen.planner import plan_first_frame, plan_last_frame, plan_video
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


def test_first_frame_only_for_modes_that_use_it(store, sample):
    repo.update(store, "segment", sample["seg"], reference_mode="last")
    with pytest.raises(ValueError, match="参考模式 last 不用首帧"):
        plan_first_frame(store, sample["seg"], seed=1)


# ── 视频 ──────────────────────────────────────────────

def test_video_task_carries_voice_description_not_reference_audio(store, sample):
    row, p = _task(store, plan_video(store, sample["seg"], seed=7))
    assert (row["kind"], row["model"]) == ("video", H3_MODEL)
    # 台词必须逐字进提示词:P2 真链路里漏了它,H3 说出的是无意义音节(ASR 相似度 0.05)
    assert row["prompt"] == ("<Picture 1> 是本段的首帧。林小雨 在 老宅庭院 低头看 玉佩,然后抬眼说话 "
                             "林小雨用普通话说:「这块玉佩，是我母亲留给我的。」 林小雨的声音:清亮女声。")
    assert (p["frames"], p["size"], p["audio_ref"]) == (192, [768, 1344], None)
    assert p["image_refs"] == ["frames/s1.png"] and p["writes"] == "video_path"
    assert p["anchors"] == [["frames/s1.png", 0]]           # 首帧既是参考图,又钉在第 0 帧


def test_video_prompt_names_speaker_even_when_glued_to_action(store, sample):
    # 实战第 6 段:「说」前没有标点,整串动作描写曾被当成说话人写进提示词,H3 把它烧成了画面字幕
    repo.update(store, "segment", sample["seg"],
                description="【镜头1】林小雨低头凝视玉佩林小雨说：「这块玉佩，是我母亲留给我的。」【镜头2】她抬眼")
    row, _ = _task(store, plan_video(store, sample["seg"], seed=7))
    assert " 林小雨用普通话说:「这块玉佩，是我母亲留给我的。」 " in row["prompt"]
    assert "凝视玉佩林小雨" not in row["prompt"]


def test_video_needs_a_first_frame(store, sample):
    repo.update(store, "segment", sample["seg"], first_frame_path=None)
    with pytest.raises(ValueError, match="缺少首帧"):
        plan_video(store, sample["seg"], seed=1)


def test_video_is_not_blocked_by_runtime_checks(store, sample):
    # 规划阶段不看云机 —— 云机关着也能先把任务排好
    assert plan_video(store, sample["seg"], seed=1) > 0


def test_video_refuses_modes_not_implemented(store, sample):
    repo.update(store, "segment", sample["seg"], reference_mode="key")
    with pytest.raises(ValueError, match="尚未实现"):
        plan_video(store, sample["seg"], seed=1)


def test_first_last_mode_pins_both_ends(store, sample):
    repo.update(store, "segment", sample["seg"], reference_mode="first_last", last_frame_path="frames/s1_end.png",
                last_image_prompt="@林小雨 走出 @老宅庭院")
    row, p = _task(store, plan_video(store, sample["seg"], seed=1))
    assert p["image_refs"] == ["frames/s1.png"]
    assert p["anchors"] == [["frames/s1.png", 0], ["frames/s1_end.png", -1]]
    assert json.loads(row["refs_json"]) == ["frames/s1.png", "frames/s1_end.png"]     # 溯源里两张都在
    assert row["prompt"].startswith("<Picture 1> 是本段的首帧。")


def test_last_mode_pins_the_last_frame(store, sample):
    repo.update(store, "segment", sample["seg"], reference_mode="last", first_frame_path=None,
                last_frame_path="frames/s1_end.png", last_image_prompt="@林小雨 走出 @老宅庭院")
    row, p = _task(store, plan_video(store, sample["seg"], seed=1))
    assert p["image_refs"] == ["frames/s1_end.png"] and p["anchors"] == [["frames/s1_end.png", -1]]
    assert row["prompt"].startswith("<Picture 1> 是本段的尾帧(最后一个画面)。")


def test_fourviews_join_as_references_most_lines_first(store, sample):
    """有四视图的角色把四视图当参考图(实测:人物与四视图是同一个人、衣着一致);台词多的排前面。"""
    repo.bind_characters(store, sample["seg"], [sample["shen"], sample["lin"]])
    repo.update(store, "character", sample["lin"], fourview_path="fv/lin.png")
    repo.update(store, "character", sample["shen"], fourview_path="fv/shen.png")
    repo.update(store, "segment", sample["seg"],
                video_prompt="@林小雨 把 @玉佩 递给 @沈墨,@老宅庭院 黄昏",
                description="【镜头1】沈墨说：「好。」【镜头2】林小雨说：「这块玉佩，是我母亲留给我的。」")
    row, p = _task(store, plan_video(store, sample["seg"], seed=1))
    assert p["image_refs"] == ["frames/s1.png", "fv/lin.png", "fv/shen.png"]
    assert row["prompt"].startswith("<Picture 1> 是本段的首帧。<Picture 2> 是林小雨的四视图(正面、左侧、背面、右侧)。"
                                    "<Picture 3> 是沈墨的四视图(正面、左侧、背面、右侧)。林小雨 把 玉佩 递给 沈墨")


# ── 尾帧 ──────────────────────────────────────────────

def test_last_frame_is_composed_from_its_own_prompt(store, sample):
    repo.update(store, "segment", sample["seg"], reference_mode="first_last",
                last_image_prompt="@林小雨 转身走向 @老宅庭院 深处,手里握着 @玉佩")
    row, p = _task(store, plan_last_frame(store, sample["seg"], seed=3))
    assert row["prompt"] == "Picture 1(林小雨) 转身走向 Picture 2(老宅庭院) 深处,手里握着 Picture 3(玉佩)"
    assert (p["writes"], p["size"], p["preset"]) == ("last_frame_path", [928, 1664], "fast")


def test_last_frame_needs_a_mode_that_uses_it_and_a_prompt(store, sample):
    with pytest.raises(ValueError, match="参考模式 first 不用尾帧"):
        plan_last_frame(store, sample["seg"], seed=1)
    repo.update(store, "segment", sample["seg"], reference_mode="last")
    with pytest.raises(ValueError, match="缺少尾帧图提示词"):
        plan_last_frame(store, sample["seg"], seed=1)
