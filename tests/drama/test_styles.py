# -*- coding: utf-8 -*-
import re

import pytest

from tools.drama.styles import STYLES, get_style


def test_realistic_is_the_default_first_choice():
    assert STYLES[0].key == "realistic" and "photorealistic" in STYLES[0].prefix


def test_keys_are_unique():
    assert len({s.key for s in STYLES}) == len(STYLES)


def test_unknown_style_fails_loudly():
    with pytest.raises(KeyError, match="未知风格预设"):
        get_style("watercolor")


def test_hk90_preset_has_every_part():
    s = get_style("hk90")
    assert s.label == "90 年代港产武侠片"
    assert set(s.asset_prefix) == {"character", "scene", "prop"}
    assert "film grain" not in s.asset_prefix["character"]          # G4:定妆图要过浅色底检查,不写颗粒 / 侧光
    assert not re.search(r"light", s.asset_prefix["character"], re.I)   # 整词:slightly 不算
    assert s.frame_look and s.light_day and s.light_night and s.negative and s.grade == "hk90"
    assert s.video_en["day"].startswith("The target video is in realistic photographic style")
    assert s.video_en["night"].startswith("The target video is in realistic photographic style")
    for text in (s.frame_look, s.light_day, s.light_night, *s.video_en.values()):
        assert "cinematic" not in text.lower() and "photorealistic" not in text.lower()


def test_old_presets_leave_new_fields_empty():
    for key in ("realistic", "anime", "semi3d"):
        s = get_style(key)
        assert s.asset_prefix is None and s.frame_look == "" and s.video_en is None and s.negative == "" and s.grade == ""
