# -*- coding: utf-8 -*-
"""关键帧对比:这条视频钉的图 vs 视频那一帧的实际画面(设计 specs/2026-10-09-中间关键帧-design.md §二.3)。"""
import subprocess

import pytest

from tests.cut.conftest import mp4_bytes  # noqa: F401
from tools.cut import keycheck, media
from tools.drama import repo
from tools.drama.tasks import create_task, set_task_state


def _video(store, root, seg, data, anchors):
    (root / "videos").mkdir(exist_ok=True)
    (root / "videos/v.mp4").write_bytes(data)
    t = create_task(store, kind="video", target_type="segment", target_id=seg, model="m", seed=1, prompt="p",
                    params={"writes": "video_path", "anchors": anchors}, refs=[a for a, _ in anchors])
    set_task_state(store, t, "queued", external_id="p")
    set_task_state(store, t, "done", result_path="videos/v.mp4", result_sha256="x")
    repo.update(store, "segment", seg, video_path="videos/v.mp4")


def _frame_png(root, idx, rel):
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(root / "videos/v.mp4"), "-vf", f"select=eq(n\\,{idx})",
                    "-vsync", "0", "-frames:v", "1", str(root / rel)], check=True)


@pytest.fixture
def world(store, sample, tmp_path, mp4_bytes):  # noqa: F811
    """1 秒 24 帧的测试图样;关键帧钉在第 12 帧(0.5 秒)。"""
    _video(store, tmp_path, sample["seg"], mp4_bytes, [["frames/s1.png", 0], ["frames/k.png", 12]])
    repo.update(store, "segment", sample["seg"], key_time_sec=0.5, key_image_prompt="p", key_frame_path="frames/k.png")
    return {"root": tmp_path, "seg": sample["seg"]}


def test_the_anchored_frame_matches_its_own_image(store, world):
    _frame_png(world["root"], 12, "frames/k.png")                    # 关键帧图就是视频第 12 帧
    c = keycheck.check(store, world["root"], world["seg"])
    assert (c.anchor_path, c.frame_idx, c.problem, c.stale) == ("frames/k.png", 12, "", False)
    assert c.diff < 1 and (world["root"] / c.frame_png).is_file()


def test_a_different_image_is_far_off(store, world):
    (world["root"] / "frames").mkdir(exist_ok=True)                   # 纯黑图冒充关键帧:与彩色图样差得远
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:size=384x672", "-frames:v", "1",
                    str(world["root"] / "frames/k.png")], check=True)
    assert keycheck.check(store, world["root"], world["seg"]).diff > keycheck.OTHER


def test_image_and_frame_come_from_the_video_task_not_the_current_keyframe(store, world):
    """出片后换过关键帧图 / 改过时刻:对比仍按这条视频当时钉的那张,并提示「换过」。"""
    _frame_png(world["root"], 12, "frames/k.png")
    repo.update(store, "segment", world["seg"], key_frame_path="frames/k2.png", key_time_sec=0.6)
    c = keycheck.check(store, world["root"], world["seg"])
    assert c.anchor_path == "frames/k.png" and c.frame_idx == 12 and c.stale


def test_frame_beyond_the_video_is_reported_not_raised(store, sample, tmp_path, mp4_bytes):  # noqa: F811
    _video(store, tmp_path, sample["seg"], mp4_bytes, [["frames/s1.png", 0], ["frames/k.png", 96]])
    _frame_png(tmp_path, 0, "frames/k.png")
    c = keycheck.check(store, tmp_path, sample["seg"])
    assert c.diff is None and c.problem == "视频只有 24 帧,取不到第 96 帧"


def test_missing_files_are_reported(store, world):
    assert keycheck.check(store, world["root"], world["seg"]).problem == "关键帧图不在本机:frames/k.png"
    _frame_png(world["root"], 12, "frames/k.png")
    (world["root"] / "videos/v.mp4").unlink()
    assert keycheck.check(store, world["root"], world["seg"]).problem == "视频不在本机:videos/v.mp4"


def test_no_keyframe_anchor_no_check(store, sample, tmp_path, mp4_bytes):  # noqa: F811
    _video(store, tmp_path, sample["seg"], mp4_bytes, [["frames/s1.png", 0], ["frames/e.png", -1]])
    assert keycheck.check(store, tmp_path, sample["seg"]) is None
    repo.update(store, "segment", sample["seg"], video_path=None)
    assert keycheck.check(store, tmp_path, sample["seg"]) is None


def test_result_is_cached_by_video_and_image_content(store, world, monkeypatch):
    _frame_png(world["root"], 12, "frames/k.png")
    first = keycheck.check(store, world["root"], world["seg"])
    calls = []
    monkeypatch.setattr(media, "_run", lambda *a, **k: calls.append(a) or pytest.fail("应当走缓存"))
    assert keycheck.check(store, world["root"], world["seg"]).diff == first.diff and calls == []
