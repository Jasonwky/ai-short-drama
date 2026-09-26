# -*- coding: utf-8 -*-
"""视频面板上的字幕:对齐按钮 → 假云机对齐 → 当前字幕逐句显示;失败原因看得见;补全缺的字幕。"""
from tools.drama import repo
from tools.drama.tasks import tasks_for
from tools.serve.fakes import sample_mp4
from tools.serve.hosts import add_host

LINE = "这块玉佩，是我母亲留给我的。"


def _with_video(store, sample, runtime):
    (runtime.data_root / "videos").mkdir()
    (runtime.data_root / "videos/s1.mp4").write_bytes(sample_mp4())
    add_host(store, label="fake", ssh_host="h", ssh_port=1, ssh_user="root", key_ref="k", local_port=8188,
             vram_gb=24.0, models_dir="/m")


def test_align_button_then_subtitles_show_on_the_panel(client, runtime, store, sample):
    _with_video(store, sample, runtime)
    runtime.backend.align_for(None).heard = "这块玉佩是我母亲留给我的"
    page = client.get(f"/episode/{sample['episode']}/videos").text
    assert "data-align" in page and "还没有字幕" in page
    r = client.post(f"/segment/{sample['seg']}/align", follow_redirects=False)
    assert r.status_code == 303
    runtime.tick_all()
    seg = repo.get(store, "segment", sample["seg"])
    assert seg["subtitle_path"].endswith("subtitles.srt")
    page = client.get(f"/episode/{sample['episode']}/videos").text
    assert "data-cue" in page and LINE in page and f'href="/media/{seg["subtitle_path"]}"' in page


def test_failed_alignment_shows_why(client, runtime, store, sample):
    _with_video(store, sample, runtime)                    # 假对齐脚本默认什么也没听见
    client.post(f"/segment/{sample['seg']}/align")
    runtime.tick_all()
    [t] = [t for t in tasks_for(store, "segment", sample["seg"]) if t["kind"] == "align"]
    assert t["state"] == "failed"
    assert "判卷不过" in client.get(f"/episode/{sample['episode']}/videos").text


def test_align_refused_without_dialogue_is_a_400(client, store, sample):
    repo.update(store, "segment", sample["seg"], description="【镜头1】林小雨低头看着玉佩。")
    r = client.post(f"/segment/{sample['seg']}/align")
    assert r.status_code == 400 and "没有台词" in r.text


def test_subtitle_fill_goes_back_to_the_videos_panel(client, store, sample):
    page = client.get(f"/episode/{sample['episode']}/videos").text
    assert 'action="/fill/subtitles/' in page
    r = client.post(f"/fill/subtitles/{sample['episode']}", data={"base_seed": "1"})
    assert "将提交 1 条字幕" in r.text and 'data-panel="videos"' in r.text
    r = client.post(f"/fill/subtitles/{sample['episode']}", data={"base_seed": "1", "t": [f"segment:{sample['seg']}"],
                                                                  "confirm": "1"}, follow_redirects=False)
    assert r.headers["location"] == f"/episode/{sample['episode']}/videos"
