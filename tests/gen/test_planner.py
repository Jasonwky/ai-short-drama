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
    # 进槽的是林小雨(台词多)、场景、沈墨;玉佩出槽,退化成纯文字。编号按提示词里出场的先后
    assert row["prompt"] == "Picture 1(林小雨) 把 玉佩 递给 Picture 2(沈墨),Picture 3(老宅庭院) 黄昏"
    assert p["image_refs"] == ["assets/lin.png", "assets/shen.png", "assets/yard.png"]


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


def test_video_task_records_the_script_snapshot(store, sample):
    """用户 2026-10-23:视频对应的台词、声音等配置都要记下来(任务参数不可改,就是记录)。"""
    row, params = _task(store, plan_video(store, sample["seg"], seed=1))
    snap = params["script"]
    assert snap["description"] == repo.get(store, "segment", sample["seg"])["description"]
    assert snap["lines"] == [["林小雨", "这块玉佩，是我母亲留给我的。"]]
    assert snap["voices"] == {"林小雨": "清亮女声"} and snap["duration_sec"] == 8.0
    assert snap["video_prompt"] == repo.get(store, "segment", sample["seg"])["video_prompt"]


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


# ── 参考图超过 3 张:第一遍留位、第二遍加入 ─────────────────

def _crowd(store, sample, prompt):
    """林小雨(有台词)+ 沈墨 + 王五 + 老宅庭院 + 玉佩 = 5 张参考图:第一遍 林小雨 / 庭院 / 沈墨,第二遍 玉佩 / 王五。"""
    wang = repo.add_character(store, sample["drama"], "王五", turnaround_path="assets/wang.png")
    repo.bind_characters(store, sample["seg"], [sample["lin"], sample["shen"], wang])
    repo.update(store, "segment", sample["seg"], image_prompt=prompt)


def test_more_than_three_references_leave_a_spot_then_add(store, sample):
    from tools.gen.planner import frame_passes
    _crowd(store, sample, "@林小雨 把 @玉佩 递给 @沈墨,@王五 站在一旁,@老宅庭院 黄昏")
    row, p = _task(store, plan_first_frame(store, sample["seg"], seed=1))
    assert p["image_refs"] == ["assets/lin.png", "assets/shen.png", "assets/yard.png"]
    # 王五单独一句:第一遍拿掉、留位,第二遍加入;玉佩和别人同句:拿不出来,照旧写成名字
    assert row["prompt"] == "Picture 1(林小雨) 把 玉佩 递给 Picture 2(沈墨),画面中给另一个人留出位置,Picture 3(老宅庭院) 黄昏"
    sp = p["second_pass"]
    assert (sp["image_refs"], sp["names"], p["dropped"]) == (["assets/wang.png"], ["王五"], ["玉佩"])
    assert sp["prompt"] == "保持 Picture 1 的画面、构图、光线和已有人物完全不变,在画面中加入:Picture 2(王五) 站在一旁。"
    assert json.loads(row["refs_json"]) == [*p["image_refs"], "assets/wang.png"]
    assert frame_passes(store, sample["seg"]) == 2


def test_nothing_to_add_means_one_pass_as_before(store, sample):
    from tools.gen.planner import frame_passes
    _crowd(store, sample, "@林小雨 把 @玉佩 递给 @沈墨,@王五 和 @沈墨 对视,@老宅庭院 黄昏")
    _, p = _task(store, plan_first_frame(store, sample["seg"], seed=1))
    assert "second_pass" not in p and p["dropped"] == ["玉佩", "王五"] and frame_passes(store, sample["seg"]) == 1


def test_pictures_are_numbered_in_the_order_the_prompt_mentions_them(store, sample):
    """云机实测:先写的人不是 Picture 1 时 3/3 把两人的动作对调(萧珩坐主位 ↔ 李福跪地),按出场先后编号后 3/3 对
    (spike-results/2026-10-20-多人同框留位加人实测.md)。沈墨先写,即使林小雨台词多、先进槽,也是沈墨当 Picture 1。"""
    repo.bind_characters(store, sample["seg"], [sample["lin"], sample["shen"]])
    repo.bind_props(store, sample["seg"], [])
    repo.update(store, "segment", sample["seg"], image_prompt="@沈墨 端坐主位,@林小雨 跪在地上,@老宅庭院 夜")
    row, p = _task(store, plan_first_frame(store, sample["seg"], seed=1))
    assert row["prompt"] == "Picture 1(沈墨) 端坐主位,Picture 2(林小雨) 跪在地上,Picture 3(老宅庭院) 夜"
    assert p["image_refs"] == ["assets/shen.png", "assets/lin.png", "assets/yard.png"] and p["dropped"] == []


# ── 框选局部替换 ─────────────────────────────────────

def test_region_replace_plans_a_new_version_of_the_same_frame(store, sample):
    from tools.gen.planner import plan_region
    tid = plan_region(store, sample["seg"], "first_frame_path", source="frames/s1.png", size=(928, 1664),
                      box=[100, 200, 200, 300], asset=("character", sample["lin"]), note="神情冷峻", seed=3)
    row, p = _task(store, tid)
    assert (row["kind"], row["model"], row["seed"]) == ("image", QWEN_MODEL, 3)
    assert p["workflow"] == "qwen_region" and p["writes"] == "first_frame_path"
    assert (p["source"], p["ref"], p["box"], p["size"]) == ("frames/s1.png", "assets/lin.png", [64, 160, 272, 384],
                                                            [928, 1664])
    assert row["prompt"].endswith("换成 Picture 2(林小雨) 的长相、发型发饰和服装。神情冷峻")
    assert json.loads(row["refs_json"]) == ["frames/s1.png", "assets/lin.png"]


def test_region_replace_on_a_keyframe_writes_back_to_that_keyframe(store, sample):
    from tools.drama import repo as r, targets
    from tools.gen.planner import plan_region
    kid = r.add_keyframe(store, sample["seg"], 4.0, "@林小雨 回头", "frames/k.png")
    tid = plan_region(store, sample["seg"], f"key:{kid}", source="frames/k.png", size=(928, 1664),
                      box=[0, 0, 400, 400], asset=("scene", sample["yard"]), note="", seed=1)
    assert targets.slot(store, r.get(store, "task", tid))[1] == kid


def test_region_replace_refuses_stale_frames_and_unbound_assets(store, sample):
    from tools.gen.planner import plan_region
    with pytest.raises(ValueError, match="框是在旧图上画的"):
        plan_region(store, sample["seg"], "first_frame_path", source="frames/old.png", size=(928, 1664),
                    box=[0, 0, 400, 400], asset=("character", sample["lin"]), note="", seed=1)
    with pytest.raises(ValueError, match="不是本段绑定"):
        plan_region(store, sample["seg"], "first_frame_path", source="frames/s1.png", size=(928, 1664),
                    box=[0, 0, 400, 400], asset=("character", sample["shen"]), note="", seed=1)
    repo.update(store, "segment", sample["seg"], last_frame_path=None)
    with pytest.raises(ValueError, match="还没有图"):
        plan_region(store, sample["seg"], "last_frame_path", source="", size=(928, 1664),
                    box=[0, 0, 400, 400], asset=("character", sample["lin"]), note="", seed=1)


def test_region_replace_uses_the_character_styling_and_checks_tags_when_planning(store, sample):
    from tools.gen.planner import plan_region
    repo.update(store, "character", sample["lin"], styling="月白襦裙,发间一支玉簪")
    tid = plan_region(store, sample["seg"], "first_frame_path", source="frames/s1.png", size=(928, 1664),
                      box=[0, 0, 400, 400], asset=("character", sample["lin"]), note="", seed=1)
    assert repo.get(store, "task", tid)["prompt"].endswith("的长相、发型发饰和服装(月白襦裙,发间一支玉簪)。")
    with pytest.raises(ValueError, match="第 3 张"):      # 补充说明里引了不存在的图:规划时就拒,不等到云机
        plan_region(store, sample["seg"], "first_frame_path", source="frames/s1.png", size=(928, 1664),
                    box=[0, 0, 400, 400], asset=("character", sample["lin"]), note="参照 Picture 3", seed=1)


# ── 场记表(v13)────────────────────────────────────────

def _blocking(**kw):
    return json.dumps([{"character": "林小雨", "pose": "跪", "facing": "镜头", "side": "左", "holding": "玉佩", **kw}],
                      ensure_ascii=False)


def test_first_frame_appends_the_blocking_written_by_code(store, sample):
    repo.update(store, "segment", sample["seg"], blocking_json=_blocking())
    row, _ = _task(store, plan_first_frame(store, sample["seg"], seed=1))
    assert row["prompt"] == ("Picture 1(林小雨) 站在 Picture 2(老宅庭院) 里,手里拿着 Picture 3(玉佩)。"
                             "Picture 1(林小雨) 跪着,面朝镜头,在画面左侧,手里拿着Picture 3(玉佩)")


def test_last_frame_does_not_use_the_blocking(store, sample):
    repo.update(store, "segment", sample["seg"], blocking_json=_blocking(), reference_mode="first_last",
                last_image_prompt="@林小雨 在 @老宅庭院 收起 @玉佩")
    row, _ = _task(store, plan_last_frame(store, sample["seg"], seed=1))
    assert "跪着" not in row["prompt"]


def test_blocking_that_disagrees_with_bindings_refuses_the_frame(store, sample):
    repo.update(store, "segment", sample["seg"], blocking_json=_blocking(character="沈墨"))
    with pytest.raises(ValueError, match="场记表与本段绑定对不上"):
        plan_first_frame(store, sample["seg"], seed=1)


def _lying_pair(store, sample, shot_type):
    """林小雨仰躺、沈墨站着,同框两人(竖屏样例剧)。"""
    repo.bind_characters(store, sample["seg"], [sample["lin"], sample["shen"]])
    rows = [{"character": "林小雨", "pose": "仰躺", "facing": "镜头", "side": "左", "holding": "无"},
            {"character": "沈墨", "pose": "站", "facing": "林小雨", "side": "右", "holding": "无"}]
    repo.update(store, "segment", sample["seg"], shot_type=shot_type,
                image_prompt="@林小雨 在 @老宅庭院 里,@沈墨 守在一旁,@玉佩 放在枕边",
                blocking_json=json.dumps(rows, ensure_ascii=False))


def test_lying_beside_someone_in_a_wide_vertical_shot_refuses_the_first_frame(store, sample):
    _lying_pair(store, sample, "全景")
    with pytest.raises(ValueError, match="景别只能选近景或特写"):
        plan_first_frame(store, sample["seg"], seed=1)


def test_close_shot_phrase_is_appended_after_the_blocking(store, sample):
    _lying_pair(store, sample, "近景")
    row, _ = _task(store, plan_first_frame(store, sample["seg"], seed=1))
    assert row["prompt"].endswith("。近景,画面只拍到躺着的人胸口以上,站着的人在画面上方")
