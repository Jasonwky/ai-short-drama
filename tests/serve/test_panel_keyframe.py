# -*- coding: utf-8 -*-
"""中间关键帧的面板:编辑段落、参考帧面板的关键帧卡、视频面板的对比(测试客户端;有头的那一遍在自测里)。"""
import json
import subprocess

from tests.cut.conftest import mp4_bytes  # noqa: F401
from tools.drama import repo
from tools.drama.tasks import create_task, set_task_state, tasks_for

KEY_PROMPT = "@林小雨 在 @老宅庭院 转身,握着 @玉佩"


def _form(seg, **over):
    f = {k: seg[k] or "" for k in ("description", "image_prompt", "last_image_prompt", "video_prompt",
                                   "reference_mode", "key_image_prompt")}
    f |= {"duration_sec": str(seg["duration_sec"]), "scene_id": str(seg["scene_id"]),
          "key_time_sec": "" if seg["key_time_sec"] is None else str(seg["key_time_sec"])}
    return {**f, **over}


def _save(client, store, sample, **over):
    seg = repo.get(store, "segment", sample["seg"])
    data = _form(seg, **over) | {"character_ids": [str(sample["lin"])], "prop_ids": [str(sample["jade"])]}
    return client.post(f"/segment/{sample['seg']}/edit", data=data)


def test_first_keyframe_on_a_finished_segment_spares_the_first_frame(client, store, sample):
    """第一次给已有首帧、已有视频的段落设关键帧:先报代价,确认后只作废视频,首帧还在。"""
    r = _save(client, store, sample, key_time_sec="4", key_image_prompt=KEY_PROMPT)
    assert "1 条视频、0 张参考帧" in r.text and repo.get(store, "segment", sample["seg"])["key_time_sec"] is None
    _save(client, store, sample, key_time_sec="4", key_image_prompt=KEY_PROMPT, confirm="1")
    seg = repo.get(store, "segment", sample["seg"])
    assert (seg["first_frame_path"], seg["video_path"], seg["key_time_sec"]) == ("frames/s1.png", None, 4.0)


def test_moving_the_keyframe_keeps_its_image_but_new_prompt_drops_it(client, store, sample):
    repo.update(store, "segment", sample["seg"], video_path=None, key_time_sec=4.0, key_image_prompt=KEY_PROMPT,
                key_frame_path="frames/k.png")
    _save(client, store, sample, key_time_sec="5")
    assert repo.get(store, "segment", sample["seg"])["key_frame_path"] == "frames/k.png"
    _save(client, store, sample, key_image_prompt=KEY_PROMPT + ",黄昏", confirm="1")
    seg = repo.get(store, "segment", sample["seg"])
    assert (seg["key_frame_path"], seg["first_frame_path"]) == (None, "frames/s1.png")


def test_other_edits_still_void_every_frame(client, store, sample):
    repo.update(store, "segment", sample["seg"], key_time_sec=4.0, key_image_prompt=KEY_PROMPT,
                key_frame_path="frames/k.png")
    _save(client, store, sample, video_prompt="@林小雨 在 @老宅庭院 抬头看 @玉佩", confirm="1")
    seg = repo.get(store, "segment", sample["seg"])
    assert (seg["first_frame_path"], seg["key_frame_path"], seg["video_path"]) == (None, None, None)


def test_bad_keyframe_time_is_refused_with_a_reason(client, store, sample):
    r = _save(client, store, sample, key_time_sec="四秒")
    assert r.status_code == 400 and "关键帧时刻要填数字" in r.text


def test_edit_page_shows_the_anchor_timeline_and_limits(client, store, sample):
    repo.update(store, "segment", sample["seg"], key_time_sec=4.0)
    page = client.get(f"/segment/{sample['seg']}/edit").text
    assert "可填 0.5–7.5 秒,本段实际 192 帧" in page and "data-key-timeline" in page
    assert "第 96 帧" in page and "第 0 帧" in page and "说话特写在哪儿切由台词决定" in page


def test_frames_panel_has_a_card_per_keyframe(client, store, sample):
    page = client.get(f"/episode/{sample['episode']}/frames").text
    assert "data-no-keyframe" in page and 'data-fill="keyframes"' in page
    k3 = repo.add_keyframe(store, sample["seg"], 3.0, KEY_PROMPT)
    k6 = repo.add_keyframe(store, sample["seg"], 6.0, KEY_PROMPT + ",回头")
    page = client.get(f"/episode/{sample['episode']}/frames").text
    assert page.count("data-generate-key-frame") == 2 and f'data-keyframe="{k3}"' in page and "第 6.0 秒" in page
    client.post(f"/keyframe/{k6}/frame", data={"seed": "5"})
    [t] = [t for t in tasks_for(store, "segment", sample["seg"]) if t["kind"] == "image"]
    assert json.loads(t["params_json"])["keyframe_id"] == k6


def test_videos_panel_compares_the_anchored_image_with_the_frame(client, runtime, store, sample, mp4_bytes):  # noqa: F811
    root = runtime.data_root
    (root / "videos").mkdir(parents=True)
    (root / "videos/v.mp4").write_bytes(mp4_bytes)
    (root / "frames").mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(root / "videos/v.mp4"), "-vf", "select=eq(n\\,12)",
                    "-vsync", "0", "-frames:v", "1", str(root / "frames/k.png")], check=True)
    t = create_task(store, kind="video", target_type="segment", target_id=sample["seg"], model="m", seed=1, prompt="p",
                    params={"writes": "video_path", "anchors": [["frames/s1.png", 0], ["frames/k.png", 12]]},
                    refs=["frames/s1.png", "frames/k.png"])
    set_task_state(store, t, "queued", external_id="p")
    set_task_state(store, t, "done", result_path="videos/v.mp4", result_sha256="x")
    repo.update(store, "segment", sample["seg"], video_path="videos/v.mp4", key_time_sec=0.5, key_image_prompt="p",
                key_frame_path="frames/k.png")
    page = client.get(f"/episode/{sample['episode']}/videos").text
    assert "data-keycheck" in page and "视频第 12 帧的实际画面" in page and "data-key-diff" in page
    assert "data-key-stale" not in page
    repo.update(store, "segment", sample["seg"], key_time_sec=1.0)
    assert "data-key-stale" in client.get(f"/episode/{sample['episode']}/videos").text
