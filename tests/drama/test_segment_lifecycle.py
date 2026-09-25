# -*- coding: utf-8 -*-
"""段落删除与编号、只作废视频的变更(音色)。"""
from tools.drama import invalidation, repo
from tools.drama.tasks import create_task, tasks_for
from tools.gen.capability import MINIMAX_H3, QWEN_IMAGE_EDIT_2511


def test_deleted_segment_ids_are_never_reused(store, sample):
    seg = sample["seg"]
    create_task(store, kind="video", target_type="segment", target_id=seg, model="m", params={}, seed=1, prompt="p",
                refs=[])
    assert repo.delete_segments(store, sample["episode"]) == [seg]
    new = repo.add_segment(store, sample["episode"], 1)
    assert new > seg                                           # 不会拿到旧编号
    assert tasks_for(store, "segment", new) == []              # 旧历史不会被新段落「认领」
    assert len(tasks_for(store, "segment", seg)) == 1          # 旧历史还在


def test_ids_skip_past_orphaned_task_targets(store, sample):
    create_task(store, kind="image", target_type="segment", target_id=500, model="m", params={}, seed=1, prompt="p",
                refs=[])
    assert repo.add_segment(store, sample["episode"], 2) == 501


def test_delete_segments_drops_bindings_but_not_assets(store, sample):
    repo.delete_segments(store, sample["episode"])
    assert store.conn.execute("SELECT COUNT(*) FROM segment_character").fetchone()[0] == 0
    assert repo.get(store, "character", sample["lin"])["name"] == "林小雨"


def test_voice_change_only_invalidates_videos(store, sample):
    imp = invalidation.impact(store, "voice", sample["lin"], video_cap=MINIMAX_H3, image_cap=QWEN_IMAGE_EDIT_2511)
    assert (imp.segment_ids, imp.videos, imp.frames, imp.videos_only) == ((sample["seg"],), 1, 0, True)
    assert "重跑 1 条视频、0 张参考帧" in imp.summary()
    invalidation.apply(store, imp)
    seg = repo.get(store, "segment", sample["seg"])
    assert seg["video_path"] is None and seg["first_frame_path"] == "frames/s1.png"
    none = invalidation.impact(store, "voice", sample["shen"], video_cap=MINIMAX_H3, image_cap=QWEN_IMAGE_EDIT_2511)
    assert none.segment_ids == ()                              # 沈墨没绑任何段落


def test_ids_of_deleted_segments_without_history_are_not_reused_either(store, sample):
    repo.delete_segments(store, sample["episode"])                 # 段落 1 没有 task
    assert repo.add_segment(store, sample["episode"], 1) == sample["seg"] + 1
