# -*- coding: utf-8 -*-
"""场景陈设表:首帧提示词里出现的陈设,要在场景的固定陈设清单上(spec 2026-10-22-连续性保障-design.md 第 3 项)。"""
from tools.drama import furnish


def test_furniture_not_in_the_scene_is_reported():
    # 实战第 3 段:正堂里摆了床
    got = furnish.problems("@沈青禾 站在雕花木床边回身,背后是匾额,@靖王府正堂", "太师椅、八仙桌、匾额")
    assert got == ["「床」不在场景的固定陈设里(太师椅、八仙桌、匾额)"]


def test_listed_furniture_passes_and_partial_names_match():
    assert furnish.problems("@萧珩 仰面平躺在床榻上,床帐挽起", "雕花床榻、屏风") == []
    assert furnish.problems("@沈青禾 坐在太师椅上", "太师椅") == []


def test_empty_list_means_not_filled_and_is_not_checked():
    assert furnish.problems("@沈青禾 站在床边", "") == []


def test_each_word_is_reported_once():
    got = furnish.problems("床边、床帐、床沿,还有屏风", "书案")
    assert [g.split("」")[0] for g in got] == ["「床", "「屏风"]
