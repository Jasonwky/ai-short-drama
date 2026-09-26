# -*- coding: utf-8 -*-
"""成片面板。导出是真 ffmpeg(两段 1 秒样片)。"""
from tests.cut.conftest import SRT, done_task
from tools.cut import export
from tools.drama import repo
from tools.serve.fakes import sample_mp4

PRODUCER = "91310000MA1234567X"


def two_videos(store, sample, root):
    seg2 = repo.add_segment(store, sample["episode"], 2, scene_id=sample["yard"], duration_sec=8.0,
                            description="【镜头1】林小雨把玉佩收进怀里。")
    repo.update(store, "segment", sample["seg"], first_frame_path=None, video_path=None)
    mp4 = sample_mp4()
    for n, seg in ((1, sample["seg"]), (2, seg2)):
        done_task(store, root, seg, "video", f"outputs/tasks/v{n}/clip.mp4", mp4)
    done_task(store, root, sample["seg"], "align", "outputs/tasks/a1/subtitles.srt", SRT.encode("utf-8"),
              refs=["outputs/tasks/v1/clip.mp4"])
    return seg2


def test_page_lists_what_is_missing_and_disables_export(client, sample):
    page = client.get(f"/episode/{sample['episode']}/final").text
    assert "段落 1 的视频文件不在本机" in page and "data-export-form" in page
    assert 'data-export disabled' in page and "烧录字幕" in page
    assert "段落 1 有台词但还没有字幕" in page


def test_export_needs_the_producer_code(client, runtime, store, sample):
    two_videos(store, sample, runtime.data_root)
    r = client.post(f"/episode/{sample['episode']}/export", data={"producer": ""})
    assert r.status_code == 400 and "★必答" in r.text
    assert export.list_for(store, sample["episode"]) == []


def test_export_runs_in_background_and_shows_checks(client, runtime, store, sample):
    two_videos(store, sample, runtime.data_root)
    r = client.post(f"/episode/{sample['episode']}/export", data={"producer": PRODUCER}, follow_redirects=False)
    assert r.status_code == 303
    runtime.wait_exports()
    page = client.get(f"/episode/{sample['episode']}/final").text
    row = export.list_for(store, sample["episode"])[0]
    assert row["state"] == "done", row["error"]
    assert 'data-export-state="done"' in page and "自检通过,可发布" in page and page.count(">PASS<") == 8
    assert f'value="{PRODUCER}"' in page                                       # 记住了,下次预填
    assert f'src="/media/{row["labeled_path"]}"' in page
    assert client.get(f"/media/{row['report_path'][:-5]}.md").status_code == 200
    assert "export 1 done" in (runtime.data_root / "journal/worker.log").read_text(encoding="utf-8")
