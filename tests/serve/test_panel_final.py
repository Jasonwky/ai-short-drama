# -*- coding: utf-8 -*-
"""成片面板。导出是真 ffmpeg(两段 1 秒样片)。"""
import subprocess

from tests.cut.conftest import SRT, done_task
from tools.cut import export, review
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
    for seg in (sample["seg"], seg2):                    # 看片确认(v13):人看过才许导
        review.confirm(store, root, seg)
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
    assert 'data-export-state="done"' in page and "自检通过,可发布" in page and page.count(">PASS<") == 9
    assert f'value="{PRODUCER}"' in page                                       # 记住了,下次预填
    assert f'src="/media/{row["labeled_path"]}"' in page
    assert client.get(f"/media/{row['report_path'][:-5]}.md").status_code == 200
    assert "export 1 done" in (runtime.data_root / "journal/worker.log").read_text(encoding="utf-8")


# ── 配乐 ────────────────────────────────────────────────

def _song_bytes(tmp_path, secs=3):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.3",
                    "-t", str(secs), "-ac", "2", str(tmp_path / "song.wav")], check=True)
    return (tmp_path / "song.wav").read_bytes()


def _upload(client, ep, data, license="自购曲库 #42", start="0.5"):
    return client.post(f"/episode/{ep}/music", files={"file": ("song.wav", data, "audio/wav")},
                       data={"license": license, "start_sec": start}, follow_redirects=False)


def test_upload_music_shows_a_cue_card_with_its_curve(client, runtime, store, sample, tmp_path):
    two_videos(store, sample, runtime.data_root)
    r = _upload(client, sample["episode"], _song_bytes(tmp_path))
    assert r.status_code == 303
    [cue] = repo.music_cues(store, sample["episode"])
    assert cue["path"].startswith("assets/music/") and (runtime.data_root / cue["path"]).is_file()
    assert (cue["start_sec"], cue["end_sec"]) == (0.5, 2.0)                  # 终点默认 = 起点 + 音乐时长,截到成片末尾
    page = client.get(f"/episode/{sample['episode']}/final").text
    assert f'data-music-cue="{cue["id"]}"' in page and "自购曲库 #42" in page
    assert "<polyline" in page and "data-music-speech" in page              # 曲线 + 台词区间底色(段落 1 有字幕)
    assert 'name="gain_db" value="-14' in page


def test_upload_refuses_files_without_audio_or_license(client, runtime, store, sample, tmp_path):
    two_videos(store, sample, runtime.data_root)
    r = _upload(client, sample["episode"], (runtime.data_root / "outputs/tasks/v1/clip.mp4").read_bytes()[:100])
    assert r.status_code == 400 and "读不出音轨" in r.text
    r = _upload(client, sample["episode"], _song_bytes(tmp_path), license="  ")
    assert r.status_code == 400 and "★必填" in r.text
    assert repo.music_cues(store, sample["episode"]) == []


def test_cue_numbers_are_editable_and_checked(client, runtime, store, sample, tmp_path):
    two_videos(store, sample, runtime.data_root)
    _upload(client, sample["episode"], _song_bytes(tmp_path))
    [cue] = repo.music_cues(store, sample["episode"])
    form = {"license": "曲库 B", "start_sec": "1.0", "end_sec": "2.0", "gain_db": "-18", "duck_db": "-6",
            "fade_in": "0.2", "fade_out": "0.3"}
    assert client.post(f"/music/{cue['id']}", data=form, follow_redirects=False).status_code == 303
    row = repo.get(store, "music_cue", cue["id"])
    assert (row["license"], row["start_sec"], row["gain_db"], row["duck_db"], row["fade_out"]) == ("曲库 B", 1.0, -18, -6, 0.3)
    r = client.post(f"/music/{cue['id']}", data={**form, "end_sec": "0.5"})
    assert r.status_code == 400 and "终点要晚于起点" in r.text
    assert repo.get(store, "music_cue", cue["id"])["end_sec"] == 2.0


def test_preview_mixes_audio_only_and_plays_on_the_page(client, runtime, store, sample, tmp_path):
    from tools.cut import media
    two_videos(store, sample, runtime.data_root)
    _upload(client, sample["episode"], _song_bytes(tmp_path))
    [cue] = repo.music_cues(store, sample["episode"])
    assert client.post(f"/music/{cue['id']}/preview", follow_redirects=False).status_code == 303
    [f] = (runtime.data_root / "outputs/preview").glob("*.m4a")
    assert abs(media.audio(f)[0] - 1.5) < 0.1                                 # 只截这条 cue 的那一段
    assert f'src="/media/outputs/preview/{f.name}"' in client.get(f"/episode/{sample['episode']}/final").text


def test_delete_cue_keeps_the_file(client, runtime, store, sample, tmp_path):
    two_videos(store, sample, runtime.data_root)
    _upload(client, sample["episode"], _song_bytes(tmp_path))
    [cue] = repo.music_cues(store, sample["episode"])
    assert client.post(f"/music/{cue['id']}/delete", follow_redirects=False).status_code == 303
    assert repo.music_cues(store, sample["episode"]) == [] and (runtime.data_root / cue["path"]).is_file()
