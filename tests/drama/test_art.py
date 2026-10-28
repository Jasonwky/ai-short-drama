# -*- coding: utf-8 -*-
from tools.drama import art
from tools.drama.styles import get_style

A = art.Art("架空古代,类北宋边军", "交领右衽、粗布或皮甲", ("uniform", "pocket", "口袋"))


def test_parse_roundtrip_and_empty():
    assert art.parse("") == art.Art()
    assert art.parse(art.dumps(A)) == A


def test_split_words_accepts_mixed_separators():
    assert art.split_words("uniform, pocket、口袋\n button  uniform") == ("uniform", "pocket", "口袋", "button")


def test_banned_hits_whole_word_english_and_substring_chinese():
    assert art.banned_hits("worn grey cloth Uniform, a pipe", A.banned) == ["uniform"]
    assert art.banned_hits("uniformly lit", A.banned) == []
    assert art.banned_hits("胸前两个口袋", A.banned) == ["口袋"]
    assert art.problems("pockets", A, "x") == []          # 整词:复数不算(人要禁复数就把复数也写上)


def test_suffixes_by_kind():
    assert art.asset_suffix(A, "character") == " Setting: 架空古代,类北宋边军. Costume: 交领右衽、粗布或皮甲."
    assert art.asset_suffix(A, "scene") == " Setting: 架空古代,类北宋边军."
    assert art.asset_suffix(art.Art(), "character") == ""
    s = get_style("realistic")
    assert art.frame_suffix(s, A).startswith(f" {s.prefix}. Setting: ") and art.frame_suffix(s, A).endswith(f"Avoid: {s.avoid}.")
    assert art.frame_suffix(s, art.Art()) == f" {s.prefix}. Avoid: {s.avoid}."
    assert art.video_phrase(s, A) == f"画面风格:{s.zh}。时代:架空古代,类北宋边军。"
    assert art.video_phrase(s, art.Art()) == f"画面风格:{s.zh}。"
