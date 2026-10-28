# -*- coding: utf-8 -*-
"""资产图定稿(spec 2026-10-28-成片质量闸门-第一期-design.md 第 6.1 节)。"""
import sqlite3

import pytest

from tools.drama import approval, repo


@pytest.fixture
def lin(store, sample, tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/lin.png").write_bytes(b"png")
    store.conn.execute("DELETE FROM asset_approval")
    return sample["lin"]


def test_approval_is_tied_to_the_current_image(store, tmp_path, lin):
    assert approval.unapproved(store, [("character", lin, "image")]) == [
        "「林小雨」的正面图还没定稿(到「资产图审」看 AI 检查结果后定稿)"]
    approval.approve(store, tmp_path, "character", lin, "image", failed=[])
    assert approval.current(store, "character", lin, "image")["sha256"]
    assert approval.unapproved(store, [("character", lin, "image")]) == []
    repo.update(store, "character", lin, turnaround_path="outputs/tasks/9/new.png")      # 换了图:定稿失效
    assert approval.current(store, "character", lin, "image") is None


def test_failed_items_need_a_reason_and_approval_is_immutable(store, tmp_path, lin):
    with pytest.raises(ValueError, match="要写理由"):
        approval.approve(store, tmp_path, "character", lin, "image", failed=["瑕疵"])
    approval.approve(store, tmp_path, "character", lin, "image", failed=["瑕疵"], reason="烟雾很淡,首帧会重画")
    with pytest.raises(ValueError, match="已经定稿"):
        approval.approve(store, tmp_path, "character", lin, "image", failed=[])
    with pytest.raises(sqlite3.DatabaseError, match="不可改"):
        store.conn.execute("UPDATE asset_approval SET reason = 'x'")


def test_missing_image_cannot_be_approved(store, tmp_path, lin):
    repo.update(store, "character", lin, fourview_path=None)
    with pytest.raises(ValueError, match="还没有四视图"):
        approval.approve(store, tmp_path, "character", lin, "fourview", failed=[])
    repo.update(store, "character", lin, turnaround_path="assets/gone.png")
    with pytest.raises(ValueError, match="不在本机"):
        approval.approve(store, tmp_path, "character", lin, "image", failed=[])
