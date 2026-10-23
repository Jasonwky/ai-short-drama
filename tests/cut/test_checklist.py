# -*- coding: utf-8 -*-
"""验收清单(spec 2026-10-23-不穿帮验收-design.md 第 1 节)。"""
from tools.cut import checklist


def test_every_item_has_a_source_and_a_way_to_judge():
    keys = [i.key for i in checklist.ITEMS]
    assert len(keys) == len(set(keys)) == 12
    for i in checklist.ITEMS:
        assert i.name and i.source and i.how in ("人勾", "自动", "自动 + 人勾")


def test_manual_keys_are_the_ones_a_person_must_tick():
    assert set(checklist.MANUAL) == {i.key for i in checklist.ITEMS if "人勾" in i.how}
    assert "text" not in checklist.MANUAL and "physics" in checklist.MANUAL


def test_missing_lists_unticked_manual_items_by_name():
    assert checklist.missing(list(checklist.MANUAL)) == []
    gone = checklist.missing([k for k in checklist.MANUAL if k != "physics"])
    assert gone == ["人在家具里、比例合理、动作不反常识"]
