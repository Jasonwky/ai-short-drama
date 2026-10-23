# -*- coding: utf-8 -*-
"""分镜方案(spec 第 4 节):2–3 个镜头方案,景别 / 机位至少差一项,过场记表、陈设表、竖屏可拍性;人挑一个。"""
import json

import pytest

from tools.drama import repo
from tools.llm import shot_options as so


def _opt(shot="中景", angle="平视", movement="固定", pose="站", img="@林小雨 在 @老宅庭院 低头看 @玉佩"):
    return {"shot_type": shot, "angle": angle, "movement": movement,
            "blocking": [{"character": "林小雨", "pose": pose, "facing": "镜头", "side": "中", "holding": "玉佩"}],
            "image_prompt": img, "why": "理由"}


def test_context_and_closed_sets(store, sample):
    ctx = so.context(store, sample["seg"], n=2)
    assert ctx["aspect"] == "9:16" and ctx["characters"] == ["林小雨"] and ctx["props"] == ["玉佩"]
    item = so.schema(ctx)["properties"]["options"]["items"]["properties"]
    assert item["angle"]["enum"] == list(so.ANGLES) and item["movement"]["enum"] == list(so.MOVES)
    with pytest.raises(ValueError, match="2–3"):
        so.context(store, sample["seg"], n=5)


def test_judge_requires_difference_and_the_existing_checks(store, sample):
    judge = so.judge_factory(so.context(store, sample["seg"], n=2))
    assert judge({"options": [_opt(), _opt(shot="近景", angle="俯拍")]}) == []
    assert any("方案 1 和 2" in p for p in judge({"options": [_opt(), _opt(movement="推")]}))    # 只差运镜不算
    assert any("@玉佩" in p for p in judge({"options": [_opt(img="@林小雨 在 @老宅庭院"), _opt(shot="近景")]}))
    assert any("姿态" in p for p in judge({"options": [_opt(img="@林小雨 跪在 @老宅庭院 看 @玉佩"), _opt(shot="近景")]}))


def test_lying_pair_must_be_close_in_vertical_frames(store, sample):
    repo.bind_characters(store, sample["seg"], [sample["lin"], sample["shen"]])
    judge = so.judge_factory(so.context(store, sample["seg"], n=2))
    two = [{"character": "林小雨", "pose": "仰躺", "facing": "镜头", "side": "左", "holding": "无"},
           {"character": "沈墨", "pose": "站", "facing": "林小雨", "side": "右", "holding": "无"}]
    img = "@林小雨 在 @老宅庭院,@沈墨 守着,@玉佩 在枕边"
    wide = {**_opt(shot="全景", img=img), "blocking": two}
    close = {**_opt(shot="近景", angle="俯拍", img=img), "blocking": two}
    assert any("近景或特写" in p for p in judge({"options": [wide, close]}))


def test_pick_writes_shot_fields_and_keeps_generated_media(store, sample):
    """用户 2026-10-23 原则:已生成的保留 —— 批准新镜头方案不作废首帧 / 视频指针。"""
    ctx = so.context(store, sample["seg"], n=2)
    out = {"options": [_opt(), _opt(shot="近景", angle="俯拍", movement="推")]}
    assert so.decision_problems(out, {"pick": 1}) == [] and so.decision_problems(out, {"pick": 2}) != []
    before = repo.get(store, "segment", sample["seg"])
    so.apply(store, sample["seg"], out, ctx, {"pick": 1})
    s = repo.get(store, "segment", sample["seg"])
    assert (s["shot_type"], s["angle"], s["movement"]) == ("近景", "俯拍", "推")
    assert json.loads(s["blocking_json"])[0]["holding"] == "玉佩" and s["image_prompt"] == _opt()["image_prompt"]
    assert (s["first_frame_path"], s["video_path"]) == (before["first_frame_path"], before["video_path"])
