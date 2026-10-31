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


def test_is_night_reads_the_scene_time():
    assert art.is_night({"time": "黄昏"}) and art.is_night({"time": "夜"}) and art.is_night({"time": "深夜"})
    assert not art.is_night({"time": "白天"}) and not art.is_night({"time": "清晨"}) and not art.is_night(None)


def test_hk90_frame_suffix_picks_light_by_scene_and_never_says_avoid():
    s = get_style("hk90")
    day = art.frame_suffix(s, A, {"time": "白天"})
    night = art.frame_suffix(s, A, {"time": "黄昏"})
    assert s.frame_look in day and s.light_day in day and s.light_night not in day
    assert s.light_night in night and s.light_day not in night
    assert "Avoid" not in day and "Setting: 架空古代,类北宋边军." in day


def test_hk90_video_phrase_is_the_english_opener():
    s = get_style("hk90")
    assert art.video_phrase(s, A, {"time": "夜"}).startswith(s.video_en["night"])
    assert "电影质感" not in art.video_phrase(s, A, {"time": "白天"})


def test_old_styles_ignore_the_scene():
    s = get_style("realistic")
    assert art.frame_suffix(s, A, {"time": "夜"}) == art.frame_suffix(s, A)
    assert art.video_phrase(s, A, {"time": "夜"}) == art.video_phrase(s, A)


def test_asset_prefix_by_table():
    assert art.asset_prefix(get_style("realistic"), "scene") == get_style("realistic").prefix
    hk = get_style("hk90")
    assert art.asset_prefix(hk, "scene") == hk.asset_prefix["scene"]
