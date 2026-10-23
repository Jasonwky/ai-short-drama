# -*- coding: utf-8 -*-
"""场记表纯函数(spec 2026-10-22-连续性保障-design.md 第 4 项)。例子取自题甲第 1 集。"""
from tools.drama import blocking as bk


def _e(character, pose="站", facing="镜头", side="中", holding="无"):
    return {"character": character, "pose": pose, "facing": facing, "side": side, "holding": holding}


def test_phrase_spells_out_pose_facing_side_and_prop():
    # 实战第 7 段:沈玉瑶背对萧珩求饶 —— 场记表把「面朝萧珩」写死
    got = bk.phrase([_e("沈玉瑶", "跪", "萧珩", "左"), _e("萧珩", "坐", "沈玉瑶", "右", "银针")])
    assert got == "@沈玉瑶 跪着,面朝@萧珩,在画面左侧;@萧珩 坐着,面朝@沈玉瑶,在画面右侧,手里拿着@银针"


def test_side_and_back_facing_read_naturally():
    assert bk.phrase([_e("萧珩", "仰躺", "侧对镜头", "左")]).split(",")[-2:] == ["侧对镜头", "在画面左侧"]
    assert ",背对镜头," in bk.phrase([_e("李福", "跪", "背对镜头", "左")])


def test_close_shot_drops_full_body_and_sides():
    # 10-23 对照 a1 组(2/2 合理)只写了「仰面躺着」、没钉左右;「全身完整地躺平」和「只拍胸口以上」自相矛盾
    got = bk.phrase([_e("萧珩", "仰躺", "侧对镜头", "左"), _e("沈青禾", "俯身", "萧珩", "中", "银针")], close=True)
    assert got == "@萧珩 仰面躺着,侧对镜头;@沈青禾 俯下身子凑近,面朝@萧珩,手里拿着@银针"


def test_bending_over_someone_is_a_pose():
    # 实战第 4 段:施针要俯身凑近;只有「站」时人站在画面另一边,模型凭空多画一只手递针
    assert bk.phrase([_e("沈青禾", "俯身", "萧珩", "中")]).startswith("@沈青禾 俯下身子凑近,面朝@萧珩")
    assert bk.transition_problems([_e("沈青禾", "站")], "沈青禾俯身取出银针", [_e("沈青禾", "俯身")]) == []


def test_lying_pose_is_written_by_code_without_negations():
    got = bk.phrase([_e("萧珩", "仰躺")])
    assert "仰面平躺" in got and "全身" in got
    # 10-23:「身体不横出床沿」写了照样横出(4/4),否定句可能反而提到了它 —— 不再写
    assert "不" not in bk.POSE_PHRASE["仰躺"] and "不" not in bk.POSE_PHRASE["侧躺"]


def test_table_must_cover_each_character_once_with_closed_values():
    probs = bk.problems([_e("沈青禾", facing="沈青禾"), _e("李福", "飘"), _e("李福")], ["沈青禾", "萧珩", "李福"], [])
    assert "场记表缺「萧珩」" in probs and "场记表里「李福」出现了不止一次" in probs
    assert any("面朝「沈青禾」" in p for p in probs) and any("姿态「飘」" in p for p in probs)


def test_one_prop_cannot_be_in_two_hands():
    probs = bk.problems([_e("甲", holding="银针"), _e("乙", holding="银针")], ["甲", "乙"], ["银针"])
    assert probs == ["道具「银针」同时在两个人手里"]


def test_pose_change_needs_an_action_in_the_previous_segment():
    prev, cur = [_e("李福", "站")], [_e("李福", "跪")]
    (msg,) = bk.transition_problems(prev, "【镜头1】李福脸色煞白,浑身发抖", cur)
    assert "李福" in msg and "站" in msg and "跪" in msg
    assert bk.transition_problems(prev, "【镜头1】李福脸色煞白,扑通一声跪倒在地", cur) == []
    assert bk.transition_problems([_e("萧珩", "仰躺")], "萧珩猛地睁眼,坐起身来", [_e("萧珩", "坐")]) == []


def test_left_right_swap_is_only_a_hint():
    prev = [_e("萧珩", side="左"), _e("沈青禾", side="右")]
    cur = [_e("萧珩", side="右"), _e("沈青禾", side="左")]
    assert bk.side_swaps(prev, cur) == ["「萧珩」「沈青禾」左右站位和上一段对调了(可能越轴)"]
    assert bk.side_swaps(prev, prev) == []
