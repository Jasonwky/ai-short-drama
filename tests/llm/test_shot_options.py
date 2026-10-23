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


def _stand():
    return json.dumps([{"character": "林小雨", "pose": "站", "facing": "镜头", "side": "中", "holding": "玉佩"}],
                      ensure_ascii=False)


def _neighbour(store, sample, number, description, scene=None):
    seg = repo.add_segment(store, sample["episode"], number, scene_id=scene or sample["yard"], duration_sec=8.0,
                           description=description, blocking_json=_stand())
    repo.bind_characters(store, seg, [sample["lin"]])
    repo.bind_props(store, seg, [sample["jade"]])
    return seg


def test_context_carries_same_scene_neighbours(store, sample):
    ctx = so.context(store, sample["seg"], n=2)
    assert ctx["prev"] is None and ctx["next"] is None
    nxt = _neighbour(store, sample, 2, "林小雨转身离开。")
    ctx = so.context(store, sample["seg"], n=2)
    assert ctx["prev"] is None and ctx["next"]["description"] == "林小雨转身离开。"
    assert ctx["next"]["blocking"][0]["pose"] == "站"
    assert "林小雨转身离开。" in so.user_message(ctx)
    other = repo.add_scene(store, sample["drama"], "书房")
    repo.set_scene(store, nxt, other)                       # 下一段换了场景 -> 不算邻居
    assert so.context(store, sample["seg"], n=2)["next"] is None


def test_judge_rejects_pose_jump_into_the_next_segment(store, sample):
    _neighbour(store, sample, 2, "林小雨转身离开。")
    kneel = _opt(pose="跪", shot="近景", angle="俯拍")
    # 本段跪着开头、下一段站着开头,而本段描述里没有起身 -> 打回
    assert any("这一段开头站" in p for p in so.judge_factory(so.context(store, sample["seg"], n=2))(
        {"options": [_opt(), kneel]}))
    repo.update(store, "segment", sample["seg"], description="林小雨跪着,随后起身。")
    assert so.judge_factory(so.context(store, sample["seg"], n=2))({"options": [_opt(), kneel]}) == []


def test_judge_rejects_pose_jump_from_the_previous_segment(store, sample):
    repo.update(store, "segment", sample["seg"], blocking_json=_stand())
    cur = _neighbour(store, sample, 2, "林小雨回到原地。")
    ctx = so.context(store, cur, n=2)
    assert ctx["prev"]["description"] == repo.get(store, "segment", sample["seg"])["description"]
    kneel = _opt(pose="跪", shot="近景", angle="俯拍")
    assert any("上一段开头站" in p for p in so.judge_factory(ctx)({"options": [kneel, _opt()]}))
    repo.update(store, "segment", sample["seg"], description="林小雨站着,然后跪下。")
    assert so.judge_factory(so.context(store, cur, n=2))({"options": [kneel, _opt()]}) == []


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
