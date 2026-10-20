# -*- coding: utf-8 -*-
from tools.drama.refs import Ref, allocate_slots, find_mentions

NAMES = ["林小雨", "老宅大门", "玉佩"]


def test_mentions_in_order_and_deduplicated():
    found, unknown = find_mentions("@林小雨推开@老宅大门,看着@玉佩,@林小雨笑了", NAMES)
    assert found == ["林小雨", "老宅大门", "玉佩"]
    assert unknown == []


def test_longest_name_wins():
    assert find_mentions("@林小雨来了", ["林小", "林小雨"])[0] == ["林小雨"]


def test_unknown_mention_reported():
    assert find_mentions("@王五走来,@林小雨回头", NAMES) == (["林小雨"], ["王五走来"])


def _c(name):
    return Ref("character", name, f"{name}.png")


def test_slots_priority_top_speaker_then_scene_then_second_speaker():
    scene, prop = Ref("scene", "庭院", "yard.png"), Ref("prop", "玉佩", "jade.png")
    kept, dropped = allocate_slots([(_c("甲"), 3), (_c("乙"), 10)], scene, [prop], slots=3)
    assert [r.name for r in kept] == ["乙", "庭院", "甲"]
    assert dropped == [prop]


def test_slots_ties_keep_binding_order():
    kept, _ = allocate_slots([(_c("甲"), 0), (_c("乙"), 0)], None, [], slots=3)
    assert [r.name for r in kept] == ["甲", "乙"]


def test_single_character_fits_everything():
    scene, prop = Ref("scene", "庭院", "y"), Ref("prop", "玉佩", "j")
    kept, dropped = allocate_slots([(_c("甲"), 5)], scene, [prop], slots=3)
    assert [r.name for r in kept] == ["甲", "庭院", "玉佩"] and dropped == []


def test_two_passes_take_the_overflow_into_a_second_pass():
    from tools.drama.refs import allocate_passes
    chars = [(_c("甲"), 9), (_c("乙"), 5), (_c("丙"), 1)]
    scene = Ref("scene", "正堂", "s.png")
    first, second, dropped = allocate_passes(chars, scene, [], 3)
    assert [r.name for r in first] == ["甲", "正堂", "乙"]
    assert [r.name for r in second] == ["丙"] and dropped == []


def test_two_passes_hold_at_most_five_and_keep_three_unchanged():
    from tools.drama.refs import allocate_passes
    chars = [(_c(n), 0) for n in "甲乙丙丁戊"]
    first, second, dropped = allocate_passes(chars, Ref("scene", "正堂", "s.png"), [], 3)
    assert [r.name for r in second] == ["丙", "丁"] and [r.name for r in dropped] == ["戊"]
    first, second, dropped = allocate_passes(chars[:2], Ref("scene", "正堂", "s.png"), [], 3)
    assert len(first) == 3 and second == [] and dropped == []


# ── 第二遍「留位加人」的分句规则(spike-results/2026-10-20-多人同框留位加人实测.md)─────
QING, XIAO, YU = Ref("character", "沈青禾", "q.png"), Ref("character", "萧珩", "x.png"), Ref("character", "沈玉瑶", "y.png")
JADE = Ref("prop", "玉佩", "j.png")
ADD_NAMES = ["萧珩", "沈玉瑶", "沈青禾", "靖王府正堂", "玉佩"]
SEG7 = ("夜晚的靖王府正堂一侧，@沈玉瑶 瘫软跪在地上惊恐求饶，@萧珩 端坐床沿目光冷峻地俯视她，"
        "@沈青禾 身着大红嫁衣站在床边冷眼旁观，背后能看到彩绘天花，@靖王府正堂")


def test_split_moves_clauses_that_only_mention_later_refs():
    from tools.drama.refs import split_for_add
    first, moved, addable = split_for_add(SEG7, [QING], ADD_NAMES)
    # 云机实测 3/3 的那一种:分句原位换成留位句
    assert first == ("夜晚的靖王府正堂一侧，@沈玉瑶 瘫软跪在地上惊恐求饶，@萧珩 端坐床沿目光冷峻地俯视她，"
                     "画面中给另一个人留出位置，背后能看到彩绘天花，@靖王府正堂")
    assert moved == ["@沈青禾 身着大红嫁衣站在床边冷眼旁观"] and addable == [QING]


def test_split_keeps_a_ref_that_shares_a_clause_with_someone_else():
    from tools.drama.refs import split_for_add
    text = "@萧珩 俯视 @沈青禾，@沈青禾 冷眼旁观，@玉佩 放在桌上"
    first, moved, addable = split_for_add(text, [QING, JADE], ADD_NAMES)
    # 沈青禾 和萧珩同句:拿不出来(第一遍仍写成名字);玉佩单独一句:可加,道具不需要留位句
    assert addable == [JADE] and moved == ["@玉佩 放在桌上"]
    assert first == "@萧珩 俯视 @沈青禾，@沈青禾 冷眼旁观"


def test_split_two_later_people_get_one_slot_sentence():
    from tools.drama.refs import split_for_add
    text = "@萧珩 端坐床沿，@沈青禾 站在床边，@沈玉瑶 跪在地上，@靖王府正堂"
    first, moved, addable = split_for_add(text, [QING, YU], ADD_NAMES)
    assert first == "@萧珩 端坐床沿，画面中给另外两个人留出位置，@靖王府正堂"
    assert moved == ["@沈青禾 站在床边", "@沈玉瑶 跪在地上"] and addable == [QING, YU]


def test_split_with_nothing_movable_changes_nothing():
    from tools.drama.refs import split_for_add
    assert split_for_add("@萧珩 和 @沈青禾 对视", [QING], ADD_NAMES) == ("@萧珩 和 @沈青禾 对视", [], [])
