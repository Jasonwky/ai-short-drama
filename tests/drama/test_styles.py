# -*- coding: utf-8 -*-
import pytest

from tools.drama.styles import STYLES, get_style


def test_realistic_is_the_default_first_choice():
    assert STYLES[0].key == "realistic" and "photorealistic" in STYLES[0].prefix


def test_keys_are_unique():
    assert len({s.key for s in STYLES}) == len(STYLES)


def test_unknown_style_fails_loudly():
    with pytest.raises(KeyError, match="未知风格预设"):
        get_style("watercolor")
