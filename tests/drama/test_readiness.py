# -*- coding: utf-8 -*-
from tools.drama import repo
from tools.drama.readiness import REGION_HINT, is_ready, segment_readiness
from tools.gen.capability import MINIMAX_H3, QWEN_IMAGE_EDIT_2511


def _check(store, seg, host_ok=True):
    return segment_readiness(store, seg, video_cap=MINIMAX_H3, image_cap=QWEN_IMAGE_EDIT_2511,
                             host_ok=host_ok, host_msg="云机隧道已连通" if host_ok else "云机隧道未连通",
                             model_ok=True, model_msg="MiniMax H3 可用")


def _by_key(checks):
    return {c.key: c for c in checks}


def test_complete_segment_is_ready(store, sample):
    checks = _check(store, sample["seg"])
    assert is_ready(checks), [c for c in checks if not c.ok]
    assert "slots" not in _by_key(checks)


def test_missing_turnaround_blocks(store, sample):
    repo.update(store, "character", sample["lin"], turnaround_path=None)
    c = _by_key(_check(store, sample["seg"]))["assets_bound"]
    assert not c.ok and c.msg == "角色「林小雨」缺正面定妆图"


def test_unknown_mention_blocks(store, sample):
    repo.update(store, "segment", sample["seg"], video_prompt="@林小雨 和 @王五 在 @老宅庭院 看 @玉佩")
    c = _by_key(_check(store, sample["seg"]))["mentions"]
    assert not c.ok and "@王五 不是本镜绑定的素材" in c.msg


def test_bound_but_never_mentioned_blocks(store, sample):
    repo.update(store, "segment", sample["seg"], video_prompt="@林小雨 在 @老宅庭院 发呆")
    c = _by_key(_check(store, sample["seg"]))["mentions"]
    assert not c.ok and c.msg == "绑定了「玉佩」但视频提示词从未 @ 它"


def test_missing_last_frame_for_first_last_mode(store, sample):
    repo.update(store, "segment", sample["seg"], reference_mode="first_last")
    c = _by_key(_check(store, sample["seg"]))["refs_ready"]
    assert not c.ok and c.msg == "参考模式 first_last,缺少尾帧"


def test_unimplemented_mode_is_reported_not_faked(store, sample):
    repo.update(store, "segment", sample["seg"], reference_mode="anchors")
    c = _by_key(_check(store, sample["seg"]))["refs_ready"]
    assert not c.ok and "尚未实现" in c.msg


def test_two_characters_push_prop_out_of_slots_as_warning(store, sample):
    repo.bind_characters(store, sample["seg"], [sample["lin"], sample["shen"]])
    repo.update(store, "segment", sample["seg"],
                description="【镜头1】沈墨说：「你还留着它。」【镜头2】林小雨说：「这块玉佩，是我母亲留给我的。」",
                image_prompt="@林小雨 把 @玉佩 递给 @沈墨,@老宅庭院 黄昏",
                video_prompt="@林小雨 把 @玉佩 递给 @沈墨,@老宅庭院 黄昏")
    checks = _check(store, sample["seg"])
    slots = _by_key(checks)["slots"]
    assert slots.ok and slots.warn
    # 玉佩够得上第二遍,但和别人写在同一句、拿不出来 —— 告诉人怎么改
    assert slots.msg == ("道具「玉佩」因槽位不足未作为参考图,仅以文字描述参与(它和别人写在图提示词的同一句里,"
                         "两遍合成加不进来;给它单独写一句就能加)。" + REGION_HINT)
    assert "two_pass" not in _by_key(checks)
    assert is_ready(checks)  # 告警不阻塞,但必须让人看见


def test_a_fourth_reference_in_its_own_clause_is_added_in_a_second_pass(store, sample):
    repo.bind_characters(store, sample["seg"], [sample["lin"], sample["shen"]])
    repo.update(store, "segment", sample["seg"], image_prompt="@林小雨 看着 @沈墨,桌上放着 @玉佩,@老宅庭院 黄昏")
    checks = _by_key(_check(store, sample["seg"]))
    assert checks["two_pass"].warn and checks["two_pass"].msg == "多参考图 4 张:两遍合成,耗时约 2 倍(第二遍加入:玉佩)"
    assert "slots" not in checks


def test_host_down_blocks(store, sample):
    c = _by_key(_check(store, sample["seg"], host_ok=False))["host_ready"]
    assert not c.ok and c.msg == "云机隧道未连通"


def test_text_only_segment_needs_no_images(store, sample):
    repo.update(store, "segment", sample["seg"], reference_mode="text_only", image_prompt="")
    repo.update(store, "character", sample["lin"], turnaround_path=None)
    checks = _by_key(_check(store, sample["seg"]))
    assert "assets_bound" not in checks and checks["prompt_ready"].ok and checks["refs_ready"].ok


def test_character_without_fourview_is_a_visible_warning(store, sample):
    c = _by_key(_check(store, sample["seg"]))["fourview"]
    assert c.ok and c.warn and c.msg == "角色「林小雨」没有四视图,视频里只靠首帧与文字认人"
    repo.update(store, "character", sample["lin"], fourview_path="fv/lin.png")
    assert "fourview" not in _by_key(_check(store, sample["seg"]))


def test_last_frame_mode_needs_its_own_prompt(store, sample):
    repo.update(store, "segment", sample["seg"], reference_mode="first_last", last_frame_path="frames/e.png")
    c = _by_key(_check(store, sample["seg"]))["prompt_ready"]
    assert not c.ok and "尾帧图提示词" in c.msg


def _key(store, seg, time_sec=4.0, prompt="@林小雨 转身背对镜头", path="frames/k.png"):
    return repo.add_keyframe(store, seg, time_sec, prompt, path)


def test_no_keyframe_means_no_keyframe_check(store, sample):
    assert "keyframe" not in _by_key(_check(store, sample["seg"]))


def test_ready_keyframe_names_its_frame(store, sample):
    _key(store, sample["seg"])
    c = _by_key(_check(store, sample["seg"]))["keyframe"]
    assert c.ok and c.msg == "关键帧(第 4 秒 = 第 96 帧)已就绪"


def test_two_keyframes_name_their_frames(store, sample):
    _key(store, sample["seg"], 6.0)
    _key(store, sample["seg"], 3.0)
    c = _by_key(_check(store, sample["seg"]))["keyframe"]
    assert c.ok and c.msg == "关键帧(第 3 秒 = 第 72 帧、第 6 秒 = 第 144 帧)已就绪"
    assert "keyframe_count" not in _by_key(_check(store, sample["seg"]))       # 2 张实测过,不警告


def test_keyframe_index_uses_the_model_fps():
    from tools.drama.readiness import key_frame_index
    assert key_frame_index(4.0, MINIMAX_H3) == 96
    assert key_frame_index(0.52, MINIMAX_H3) == 12


def test_keyframe_problems_block(store, sample):
    """8 秒片实际 192 帧:每张关键帧要在 0.5 秒 ~ 时长 − 0.5 秒,还要有提示词与图;原因写明是第几秒那张。"""
    cases = {
        (0.2, "p", "k.png"): "第 0.2 秒的关键帧不在 0.5–7.5 秒之间",
        (7.8, "p", "k.png"): "第 7.8 秒的关键帧不在 0.5–7.5 秒之间",
        (4.0, " ", "k.png"): "第 4 秒的关键帧缺图提示词",
        (4.0, "p", None): "第 4 秒的关键帧还没有图",
    }
    for (t, prompt, path), msg in cases.items():
        k = _key(store, sample["seg"], t, prompt, path)
        c = _by_key(_check(store, sample["seg"]))["keyframe"]
        assert not c.ok and msg in c.msg, (t, c.msg)
        repo.delete_keyframe(store, k)


def test_keyframes_too_close_block(store, sample):
    _key(store, sample["seg"], 3.0)
    _key(store, sample["seg"], 3.4)
    c = _by_key(_check(store, sample["seg"]))["keyframe"]
    assert not c.ok and "第 3 秒与第 3.4 秒的关键帧相隔不到 0.5 秒" in c.msg


def test_three_keyframes_warn_and_four_block(store, sample):
    """最多 3 张(用户定);第 3 张标「未经真机验证」(实测到 2 张)。"""
    for t in (2.0, 4.0, 6.0):
        _key(store, sample["seg"], t)
    by = _by_key(_check(store, sample["seg"]))
    assert by["keyframe"].ok and by["keyframe_count"].warn and "实测到 2 张" in by["keyframe_count"].msg
    _key(store, sample["seg"], 7.0)
    c = _by_key(_check(store, sample["seg"]))["keyframe"]
    assert not c.ok and "最多 3 张关键帧,现在 4 张" in c.msg


def test_shortened_segment_pushes_the_keyframe_out(store, sample):
    _key(store, sample["seg"], 7.0)
    repo.update(store, "segment", sample["seg"], duration_sec=6.0)
    c = _by_key(_check(store, sample["seg"]))["keyframe"]
    assert not c.ok and "不在 0.5–5.5 秒之间" in c.msg


def test_first_last_plus_keyframe_is_flagged_untested(store, sample):
    _key(store, sample["seg"])
    repo.update(store, "segment", sample["seg"], reference_mode="first_last")
    c = _by_key(_check(store, sample["seg"]))["keyframe_untested"]
    assert c.ok and c.warn and "未经真机验证" in c.msg


def test_furniture_outside_the_scene_list_blocks(store, sample):
    """场景陈设表(v13):场景填了固定陈设,首帧提示词里清单外的陈设拦生成;没填不查。"""
    repo.update(store, "segment", sample["seg"], image_prompt="@林小雨 坐在床边捧着 @玉佩,@老宅庭院")
    assert "furnishings" not in _by_key(_check(store, sample["seg"]))
    repo.update(store, "scene", sample["yard"], purpose="庭院", furnishings="石桌、古井")
    c = _by_key(_check(store, sample["seg"]))["furnishings"]
    assert not c.ok and c.msg.startswith("首帧图提示词:「床」不在场景的固定陈设里")
    repo.update(store, "scene", sample["yard"], furnishings="石桌、古井、竹床")
    assert _by_key(_check(store, sample["seg"]))["furnishings"].ok
