# -*- coding: utf-8 -*-
"""成片测试的夹具:真 ffmpeg(本机 4.4)生成的小样片 + 走 task 表的生成记录(溯源要靠它)。"""
import pytest

from tests.drama.conftest import sample, store  # noqa: F401  —— 复用 P1 的夹具
from tools.drama import repo
from tools.drama.tasks import create_task, set_task_state
from tools.serve.dispatch import sha256_file
from tools.serve.fakes import sample_mp4


SRT = "1\n00:00:00,200 --> 00:00:00,900\n这块玉佩，是我母亲留给我的。\n\n"


@pytest.fixture(scope="session")
def mp4_bytes():
    return sample_mp4()


def done_task(store, root, seg_id, kind, rel, data, refs=()):  # noqa: F811
    """把 data 写到 root/rel,再记一条已完成的生成任务(与 worker 收取时同样的字段)。"""
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(data)
    writes = {"video": "video_path", "image": "first_frame_path", "align": "subtitle_path"}[kind]
    t = create_task(store, kind=kind, target_type="segment", target_id=seg_id, model=f"m-{kind}",
                    params={"writes": writes}, seed=7, prompt=f"{kind} prompt", refs=list(refs))
    set_task_state(store, t, "queued", external_id=f"p{t}")
    set_task_state(store, t, "done", result_path=rel, result_sha256=sha256_file(root / rel))
    repo.update(store, "segment", seg_id, **{writes: rel})
    return t


@pytest.fixture
def two_segments(store, sample, tmp_path, mp4_bytes):  # noqa: F811
    """两段都有带音轨的视频、首帧 → 视频的生成链完整;段落 1 有台词,配了对齐出的字幕(0.2–0.9 s)。"""
    root = tmp_path
    seg1 = sample["seg"]
    seg2 = repo.add_segment(store, sample["episode"], 2, scene_id=sample["yard"], duration_sec=8.0,
                            description="【镜头1】林小雨把玉佩收进怀里。")
    repo.update(store, "segment", seg1, first_frame_path=None, video_path=None)
    for n, seg in ((1, seg1), (2, seg2)):
        done_task(store, root, seg, "image", f"outputs/tasks/f{n}/frame.png", b"png-%d" % n)
        done_task(store, root, seg, "video", f"outputs/tasks/v{n}/clip.mp4", mp4_bytes,
                  refs=[f"outputs/tasks/f{n}/frame.png"])
    done_task(store, root, seg1, "align", "outputs/tasks/a1/subtitles.srt", SRT.encode("utf-8"),
              refs=["outputs/tasks/v1/clip.mp4"])
    return {**sample, "root": root, "seg1": seg1, "seg2": seg2}
