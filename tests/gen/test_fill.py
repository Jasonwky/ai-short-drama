# -*- coding: utf-8 -*-
import json

import pytest

from tools.drama import repo
from tools.drama.tasks import create_task, set_task_state, tasks_for
from tools.gen.fill import confirm_fill, plan_fill


def _seg(store, sample, number, **fields):
    sid = repo.add_segment(store, sample["episode"], number, scene_id=sample["yard"], duration_sec=8.0,
                           reference_mode="first",
                           description="【镜头1】林小雨低头看着玉佩。【镜头2】林小雨说：「这块玉佩，是我母亲留给我的。」",
                           image_prompt="@林小雨 站在 @老宅庭院 里,手里拿着 @玉佩",
                           video_prompt="@林小雨 在 @老宅庭院 低头看 @玉佩,然后抬眼说话", **fields)
    repo.bind_characters(store, sid, [sample["lin"]])
    repo.bind_props(store, sid, [sample["jade"]])
    return sid


def _status(plan):
    return {i.label: (i.status, i.reason) for i in plan.items}


def test_frames_are_sorted_into_four_kinds(store, sample):
    todo = _seg(store, sample, 2)
    blocked = _seg(store, sample, 3)
    repo.set_scene(store, blocked, None)
    busy = _seg(store, sample, 4)
    create_task(store, kind="image", target_type="segment", target_id=busy, model="m",
                params={"writes": "first_frame_path"}, seed=1, prompt="p", refs=[])

    plan = plan_fill(store, "frames", sample["episode"])
    st = _status(plan)
    assert st["段落 1"] == ("has", "") and st["段落 2"] == ("todo", "") and st["段落 4"] == ("in_flight", "")
    assert st["段落 3"][0] == "blocked" and "未绑定场景" in st["段落 3"][1]
    assert [i.target_id for i in plan.todo] == [todo]
    assert plan.summary().startswith("将提交 1 条首帧,约 0.01 小时") and "已有 1、在途 1、不能做 1" in plan.summary()


def test_confirm_uses_listed_targets_and_seeds_in_order(store, sample):
    a, b = _seg(store, sample, 2), _seg(store, sample, 3)
    targets = [("segment", i.target_id) for i in plan_fill(store, "frames", sample["episode"]).todo]
    assert targets == [("segment", a), ("segment", b)]
    created = confirm_fill(store, "frames", sample["episode"], targets, base_seed=100)
    assert [repo.get(store, "task", t)["seed"] for t in created] == [100, 101]
    assert [repo.get(store, "task", t)["target_id"] for t in created] == [a, b]


def test_confirm_skips_what_changed_since_the_plan(store, sample):
    a, b = _seg(store, sample, 2), _seg(store, sample, 3)
    targets = [("segment", a), ("segment", b)]
    confirm_fill(store, "frames", sample["episode"], [("segment", a)], base_seed=1)   # 别人先点了 a
    created = confirm_fill(store, "frames", sample["episode"], targets, base_seed=100)
    assert len(created) == 1 and repo.get(store, "task", created[0])["seed"] == 101   # 序号仍按确认页
    assert len(tasks_for(store, "segment", a)) == 1                                   # a 没有被重复交


def test_confirm_never_creates_what_the_page_did_not_list(store, sample):
    _seg(store, sample, 2)
    assert confirm_fill(store, "frames", sample["episode"], [], base_seed=1) == []


def test_failed_without_product_is_redone_but_failed_with_product_is_not(store, sample):
    repo.update(store, "segment", sample["seg"], video_path=None)
    t = create_task(store, kind="video", target_type="segment", target_id=sample["seg"], model="m", params={},
                    seed=1, prompt="p", refs=[])
    set_task_state(store, t, "failed", error="ComfyUI 执行失败")
    plan = plan_fill(store, "videos", sample["episode"])
    assert _status(plan)["段落 1"] == ("todo", "") and 0 < plan.est_seconds() < 3600
    repo.update(store, "segment", sample["seg"], video_path="videos/s1.mp4")
    assert _status(plan_fill(store, "videos", sample["episode"]))["段落 1"] == ("has", "")


def test_video_blocked_until_its_first_frame_exists(store, sample):
    _seg(store, sample, 2)
    st = _status(plan_fill(store, "videos", sample["episode"]))
    assert st["段落 2"][0] == "blocked" and "缺少首帧" in st["段落 2"][1]


def test_assets_fill_covers_the_whole_drama(store):
    d = repo.create_drama(store, "新剧", "9:16", "realistic")
    lin = repo.add_character(store, d, "林小雨", final_prompt="A young woman.")
    repo.add_character(store, d, "沈墨")
    repo.add_scene(store, d, "老宅庭院", final_prompt="A courtyard.", image_path="assets/yard.png")
    plan = plan_fill(store, "assets", d)
    st = _status(plan)
    assert st["角色「林小雨」"] == ("todo", "") and st["场景「老宅庭院」"] == ("has", "")
    assert st["角色「沈墨」"][0] == "blocked" and "缺少生成描述" in st["角色「沈墨」"][1]
    [t] = confirm_fill(store, "assets", d, [("character", lin)], base_seed=7)
    row = repo.get(store, "task", t)
    assert (row["target_type"], row["seed"], json.loads(row["params_json"])["writes"]) == ("character", 7,
                                                                                         "turnaround_path")


def test_unknown_kind_is_refused(store, sample):
    with pytest.raises(ValueError, match="未知的补全类型"):
        plan_fill(store, "music", sample["episode"])


def test_subtitles_fill_covers_segments_with_video_and_dialogue(store, sample):
    done = _seg(store, sample, 2, video_path="videos/s2.mp4", subtitle_path="subs/s2.srt")
    silent = _seg(store, sample, 3, video_path="videos/s3.mp4")
    repo.update(store, "segment", silent, description="【镜头1】林小雨低头看着玉佩。")
    novideo = _seg(store, sample, 4)
    plan = plan_fill(store, "subtitles", sample["episode"])
    st = _status(plan)
    assert st["段落 1"] == ("todo", "") and st["段落 2"] == ("has", "")
    assert st["段落 3"][0] == st["段落 4"][0] == "blocked"
    assert "没有台词" in st["段落 3"][1] and "还没有视频" in st["段落 4"][1]
    assert "条字幕" in plan.summary()
    [tid] = confirm_fill(store, "subtitles", sample["episode"], [("segment", sample["seg"])], 100)
    assert repo.get(store, "task", tid)["kind"] == "align"
    assert plan_fill(store, "subtitles", sample["episode"]).todo == []      # 在途了,不重复交
    assert done and novideo


def test_in_flight_is_per_field_first_frame_does_not_block_the_last_frame(store, sample):
    """首帧和尾帧都是这个段落的 image 任务:在途要按写回的字段分开算,否则一个在跑、另一个就永远补不上。"""
    repo.update(store, "segment", sample["seg"], reference_mode="first_last", first_frame_path=None,
                last_image_prompt="@林小雨 站在 @老宅庭院 里,手里拿着 @玉佩")
    confirm_fill(store, "frames", sample["episode"], [("segment", sample["seg"])], base_seed=1)
    assert _status(plan_fill(store, "lastframes", sample["episode"]))["段落 1"] == ("todo", "")
