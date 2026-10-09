# -*- coding: utf-8 -*-
from datetime import datetime, timedelta, timezone

import pytest

from tools.gen import nodecache
from tools.serve.fakes import FakeComfy


def test_refresh_writes_cache_and_meta(tmp_path):
    with pytest.raises(ValueError, match="先开云机"):
        nodecache.load(tmp_path)
    assert nodecache.meta(tmp_path) is None and nodecache.stale(None)
    m = nodecache.refresh(tmp_path, FakeComfy(), "4080S")
    assert (m["host"], m["required_frontend_version"], m["node_types"]) == ("4080S", "1.51.9", 35)
    assert "MiniMaxH3AddGuide" in nodecache.load(tmp_path) and nodecache.meta(tmp_path) == m
    assert not nodecache.stale(m)
    assert nodecache.stale(m, datetime.now(timezone.utc) + timedelta(days=2))


def test_unreachable_cloud_writes_nothing(tmp_path):
    comfy = FakeComfy()
    comfy.reachable = False
    with pytest.raises(Exception):
        nodecache.refresh(tmp_path, comfy, "x")
    assert nodecache.meta(tmp_path) is None


def test_default_of_reads_widget_defaults(tmp_path):
    nodecache.refresh(tmp_path, FakeComfy(), "x")
    default = nodecache.default_of(nodecache.load(tmp_path))
    assert default("CreateVideo", "color_space") == "sRGB" and default("NoSuchNode", "x") is None
