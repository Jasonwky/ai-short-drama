# -*- coding: utf-8 -*-
"""一段多张中间关键帧的存储(设计 specs/2026-10-15-多关键帧与草稿档-design.md §2.1)。"""
import pytest

from tools.drama import repo


def test_keyframes_come_back_in_time_order(store, sample):
    late = repo.add_keyframe(store, sample["seg"], 6.0, "@林小雨 回头")
    early = repo.add_keyframe(store, sample["seg"], 3.0, "@林小雨 转身")
    assert [k["id"] for k in repo.keyframes(store, sample["seg"])] == [early, late]
    assert repo.get(store, "segment_keyframe", early)["legacy_v9"] == 0


def test_keyframe_ids_are_never_reused(store, sample):
    """删了的关键帧,历史任务参数里的 keyframe_id 不能指到新加的那张。"""
    k = repo.add_keyframe(store, sample["seg"], 3.0)
    repo.delete_keyframe(store, k)
    assert repo.add_keyframe(store, sample["seg"], 3.0) > k
    with pytest.raises(KeyError):
        repo.delete_keyframe(store, k)


def test_only_whitelisted_keyframe_fields_are_writable(store, sample):
    k = repo.add_keyframe(store, sample["seg"], 3.0)
    repo.update(store, "segment_keyframe", k, time_sec=3.5, image_prompt="p", frame_path="k.png")
    with pytest.raises(ValueError, match="不允许写入"):
        repo.update(store, "segment_keyframe", k, segment_id=sample["seg"])


def test_keyframes_go_with_their_segment(store, sample):
    repo.add_keyframe(store, sample["seg"], 3.0)
    repo.delete_segments(store, repo.get(store, "segment", sample["seg"])["episode_id"])
    assert store.conn.execute("SELECT COUNT(*) FROM segment_keyframe").fetchone()[0] == 0
