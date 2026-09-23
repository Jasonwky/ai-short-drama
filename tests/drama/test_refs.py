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
