# -*- coding: utf-8 -*-
"""视频面板的看片区:切镜计数、接缝两帧、看片确认(spec 2026-10-22-连续性保障-design.md 第 1、2 项)。"""
from tests.cut.conftest import done_task
from tools.drama import repo
from tools.serve.fakes import sample_mp4


def _two(store, sample, root):
    seg2 = repo.add_segment(store, sample["episode"], 2, scene_id=sample["yard"], duration_sec=8.0,
                            description="【镜头1】林小雨把玉佩收进怀里。")
    for n, seg in ((1, sample["seg"]), (2, seg2)):
        done_task(store, root, seg, "video", f"outputs/tasks/v{n}/clip.mp4", sample_mp4())
    return seg2


def test_review_section_shows_cuts_and_the_seam(client, runtime, store, sample):
    seg2 = _two(store, sample, runtime.data_root)
    page = client.get(f"/episode/{sample['episode']}/videos").text
    assert page.count("data-review-section") == 2 and page.count("data-cut-count") == 2
    assert page.count("data-seam>") == 1                     # 只有第 2 段有「上一段末帧」
    assert f'id="seg-{seg2}"' in page and "data-review-confirm" in page


def test_confirm_review_is_tied_to_the_current_video(client, runtime, store, sample):
    seg2 = _two(store, sample, runtime.data_root)
    r = client.post(f"/segment/{seg2}/review", data={"note": "接缝看过"}, follow_redirects=False)
    assert r.status_code == 303
    page = client.get(f"/episode/{sample['episode']}/videos").text
    assert page.count("data-reviewed") == 1 and "接缝看过" in page
    done_task(store, runtime.data_root, seg2, "video", "outputs/tasks/v3/clip.mp4", sample_mp4() + b"\0")
    assert "data-reviewed" not in client.get(f"/episode/{sample['episode']}/videos").text    # 换了视频要重看
