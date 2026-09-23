# -*- coding: utf-8 -*-
import sqlite3

import pytest

from tools.drama import repo


def test_segment_assets_in_binding_order(store, sample):
    a = repo.segment_assets(store, sample["seg"])
    assert [c["name"] for c in a["characters"]] == ["林小雨"]
    assert a["scene"]["name"] == "老宅庭院"
    assert [p["name"] for p in a["props"]] == ["玉佩"]


def test_rebinding_replaces_and_keeps_order(store, sample):
    repo.bind_characters(store, sample["seg"], [sample["shen"], sample["lin"]])
    names = [c["name"] for c in repo.segment_assets(store, sample["seg"])["characters"]]
    assert names == ["沈墨", "林小雨"]


def test_update_rejects_fields_outside_whitelist(store, sample):
    with pytest.raises(ValueError, match="不允许写入字段"):
        repo.update(store, "segment", sample["seg"], episode_id=999)
    with pytest.raises(ValueError, match="不允许写入字段"):
        repo.add_character(store, sample["drama"], "王五", library_id=1)  # 跨剧库字段 v1 预留,不开放写入


def test_update_writes_whitelisted_fields(store, sample):
    repo.update(store, "segment", sample["seg"], duration_sec=10.0, layout="林小雨居中偏左")
    seg = repo.get(store, "segment", sample["seg"])
    assert (seg["duration_sec"], seg["layout"]) == (10.0, "林小雨居中偏左")


def test_update_missing_row_raises(store):
    with pytest.raises(KeyError):
        repo.update(store, "segment", 999, layout="x")


def test_duplicate_character_name_in_same_drama_rejected(store, sample):
    with pytest.raises(sqlite3.IntegrityError):
        repo.add_character(store, sample["drama"], "林小雨")


def test_invalid_source_type_rejected(store, sample):
    with pytest.raises(sqlite3.IntegrityError):
        repo.add_episode(store, sample["drama"], 2, "tweet")


def test_cannot_bind_asset_from_another_drama(store, sample):
    other = repo.create_drama(store, "另一部", "9:16")
    stranger = repo.add_character(store, other, "路人")
    with pytest.raises(ValueError, match="另一部剧"):
        repo.bind_characters(store, sample["seg"], [stranger])
    # 失败的绑定不能破坏原有绑定(整体回滚)
    assert [c["name"] for c in repo.segment_assets(store, sample["seg"])["characters"]] == ["林小雨"]


def test_cannot_set_scene_from_another_drama(store, sample):
    other = repo.create_drama(store, "另一部", "9:16")
    far = repo.add_scene(store, other, "别处")
    with pytest.raises(ValueError, match="另一部剧"):
        repo.set_scene(store, sample["seg"], far)
    with pytest.raises(ValueError, match="另一部剧"):
        repo.add_segment(store, sample["episode"], 2, scene_id=far)
