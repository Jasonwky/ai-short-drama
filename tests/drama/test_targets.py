# -*- coding: utf-8 -*-
"""生成任务写到哪(tools/drama/targets.py):关键帧任务写到那一张关键帧;P12 时代的旧任务只认搬来的那张。"""
from tools.drama import repo, targets
from tools.drama.tasks import create_task


def _task(store, seg, **params):
    return repo.get(store, "task", create_task(store, kind="image", target_type="segment", target_id=seg, model="m",
                                               params=params, seed=1, prompt="p", refs=[]))


def _legacy_row(store, seg, path="k.png"):
    """迁移时从 v9 段落列搬来的那张(legacy_v9 = 1)。"""
    with store.write() as c:
        return c.execute("INSERT INTO segment_keyframe (segment_id, time_sec, image_prompt, frame_path, legacy_v9)"
                         " VALUES (?, 4.0, 'p', ?, 1)", (seg, path)).lastrowid


def test_ordinary_tasks_write_to_their_target_field(store, sample):
    t = _task(store, sample["seg"], writes="first_frame_path")
    assert targets.slot(store, t) == ("segment", sample["seg"], "first_frame_path")
    assert targets.current(store, t) == "frames/s1.png"


def test_keyframe_task_writes_to_its_keyframe(store, sample):
    k = repo.add_keyframe(store, sample["seg"], 3.0, "p", "k3.png")
    t = _task(store, sample["seg"], writes="key_frame_path", keyframe_id=k)
    assert targets.slot(store, t) == ("segment_keyframe", k, "frame_path")
    assert targets.current(store, t) == "k3.png"


def test_deleted_keyframe_has_no_slot(store, sample):
    k = repo.add_keyframe(store, sample["seg"], 3.0)
    t = _task(store, sample["seg"], writes="key_frame_path", keyframe_id=k)
    repo.delete_keyframe(store, k)
    assert targets.slot(store, t) == ("segment_keyframe", None, "frame_path") and targets.current(store, t) is None


def test_p12_task_means_the_migrated_keyframe(store, sample):
    """P12 时代的任务没有 keyframe_id:只认迁移时搬来的那张,不认后来加的。"""
    legacy = _legacy_row(store, sample["seg"])
    newer = repo.add_keyframe(store, sample["seg"], 2.0)          # 时刻更早、编号更大
    old = _task(store, sample["seg"], writes="key_frame_path")
    assert targets.slot(store, old) == ("segment_keyframe", legacy, "frame_path")
    repo.delete_keyframe(store, legacy)
    assert targets.slot(store, old)[1] is None and newer                         # 搬来的删了 = 已删除,不顶替


def test_p12_task_without_a_migrated_keyframe_has_no_slot(store, sample):
    repo.add_keyframe(store, sample["seg"], 3.0)
    assert targets.slot(store, _task(store, sample["seg"], writes="key_frame_path"))[1] is None
