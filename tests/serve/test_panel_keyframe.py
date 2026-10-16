# -*- coding: utf-8 -*-
"""中间关键帧的面板:编辑段落的关键帧列表、参考帧面板的关键帧卡、视频面板的对比(测试客户端;有头的那一遍在自测里)。"""
import json
import subprocess

from tests.cut.conftest import mp4_bytes  # noqa: F401
from tools.drama import repo
from tools.drama.tasks import create_task, set_task_state, tasks_for

KEY_PROMPT = "@林小雨 在 @老宅庭院 转身,握着 @玉佩"


def _form(store, seg, keys=None, delete=(), **over):
    """编辑页提交的表单。keys:[(行号, 秒, 提示词)],新加的行号写空串;默认 = 现有关键帧原样。"""
    f = {k: seg[k] or "" for k in ("description", "image_prompt", "last_image_prompt", "video_prompt", "reference_mode")}
    f |= {"duration_sec": str(seg["duration_sec"]), "scene_id": str(seg["scene_id"])}
    if keys is None:
        keys = [(str(k["id"]), str(k["time_sec"]), k["image_prompt"]) for k in repo.keyframes(store, seg["id"])]
    f |= {"key_id": [k[0] for k in keys], "key_time": [k[1] for k in keys], "key_prompt": [k[2] for k in keys],
          "key_delete": [str(d) for d in delete]}
    return {**f, **over}


def _save(client, store, sample, keys=None, delete=(), **over):
    seg = repo.get(store, "segment", sample["seg"])
    data = _form(store, seg, keys, delete, **over) | {"character_ids": [str(sample["lin"])], "prop_ids": [str(sample["jade"])]}
    return client.post(f"/segment/{sample['seg']}/edit", data=data)


def _keys(store, sample):
    return [(k["time_sec"], k["image_prompt"], k["frame_path"]) for k in repo.keyframes(store, sample["seg"])]


def test_first_keyframe_on_a_finished_segment_spares_the_first_frame(client, store, sample):
    """第一次给已有首帧、已有视频的段落加关键帧:先报代价,确认后只作废视频,首帧还在。"""
    r = _save(client, store, sample, keys=[("", "4", KEY_PROMPT)])
    assert "1 条视频、0 张参考帧" in r.text and _keys(store, sample) == []
    _save(client, store, sample, keys=[("", "4", KEY_PROMPT)], confirm="1")
    seg = repo.get(store, "segment", sample["seg"])
    assert (seg["first_frame_path"], seg["video_path"]) == ("frames/s1.png", None)
    assert _keys(store, sample) == [(4.0, KEY_PROMPT, None)]


def test_only_the_reprompted_keyframe_loses_its_image(client, store, sample):
    """靠行号认改了哪张:挪第 1 张的时刻,图还在;改第 2 张的提示词,只有第 2 张的图作废。"""
    repo.update(store, "segment", sample["seg"], video_path=None)
    k1 = repo.add_keyframe(store, sample["seg"], 3.0, KEY_PROMPT, "frames/k1.png")
    k2 = repo.add_keyframe(store, sample["seg"], 6.0, KEY_PROMPT, "frames/k2.png")
    _save(client, store, sample, keys=[(str(k1), "2.5", KEY_PROMPT), (str(k2), "6.0", KEY_PROMPT)])
    assert _keys(store, sample) == [(2.5, KEY_PROMPT, "frames/k1.png"), (6.0, KEY_PROMPT, "frames/k2.png")]
    r = _save(client, store, sample, keys=[(str(k1), "2.5", KEY_PROMPT), (str(k2), "6.0", KEY_PROMPT + ",回头")])
    assert "0 条视频、1 张参考帧" in r.text                                   # 先报代价
    _save(client, store, sample, keys=[(str(k1), "2.5", KEY_PROMPT), (str(k2), "6.0", KEY_PROMPT + ",回头")], confirm="1")
    assert _keys(store, sample) == [(2.5, KEY_PROMPT, "frames/k1.png"), (6.0, KEY_PROMPT + ",回头", None)]
    assert repo.get(store, "segment", sample["seg"])["first_frame_path"] == "frames/s1.png"


def test_delete_one_and_add_one(client, store, sample):
    k1 = repo.add_keyframe(store, sample["seg"], 3.0, KEY_PROMPT, "frames/k1.png")
    k2 = repo.add_keyframe(store, sample["seg"], 6.0, KEY_PROMPT, "frames/k2.png")
    keys = [(str(k1), "3.0", KEY_PROMPT), (str(k2), "6.0", KEY_PROMPT), ("", "5", "@林小雨 回头")]
    r = _save(client, store, sample, keys=keys, delete=[k1])
    assert "1 条视频、0 张参考帧" in r.text
    _save(client, store, sample, keys=keys, delete=[k1], confirm="1")         # 确认页把隐藏字段原样带回来
    assert _keys(store, sample) == [(5.0, "@林小雨 回头", None), (6.0, KEY_PROMPT, "frames/k2.png")]
    assert repo.get(store, "segment", sample["seg"])["video_path"] is None


def test_blank_new_row_is_ignored(client, store, sample):
    repo.update(store, "segment", sample["seg"], video_path=None)
    k = repo.add_keyframe(store, sample["seg"], 3.0, KEY_PROMPT, "frames/k.png")
    r = _save(client, store, sample, keys=[(str(k), "3.0", KEY_PROMPT), ("", "", "")])
    assert r.status_code == 200 and _keys(store, sample) == [(3.0, KEY_PROMPT, "frames/k.png")]


def test_other_edits_still_void_every_frame(client, store, sample):
    repo.add_keyframe(store, sample["seg"], 4.0, KEY_PROMPT, "frames/k.png")
    _save(client, store, sample, video_prompt="@林小雨 在 @老宅庭院 抬头看 @玉佩", confirm="1")
    seg = repo.get(store, "segment", sample["seg"])
    assert (seg["first_frame_path"], seg["video_path"]) == (None, None)
    assert _keys(store, sample) == [(4.0, KEY_PROMPT, None)]


def test_bad_keyframe_rows_are_refused_with_a_reason(client, store, sample):
    r = _save(client, store, sample, keys=[("", "四秒", KEY_PROMPT)])
    assert r.status_code == 400 and "关键帧时刻要填数字" in r.text
    r = _save(client, store, sample, keys=[("", "", KEY_PROMPT)])
    assert r.status_code == 400 and "关键帧要填第几秒" in r.text
    other = repo.add_segment(store, sample["episode"], 9)
    foreign = repo.add_keyframe(store, other, 3.0)
    r = _save(client, store, sample, keys=[(str(foreign), "3.0", "")])
    assert r.status_code == 400 and f"关键帧 {foreign} 不是本段的" in r.text


def test_edit_page_lists_keyframes_on_the_timeline(client, store, sample):
    repo.add_keyframe(store, sample["seg"], 3.0, KEY_PROMPT)
    repo.add_keyframe(store, sample["seg"], 6.0, KEY_PROMPT)
    page = client.get(f"/segment/{sample['seg']}/edit").text
    assert "每张可填 0.5–7.5 秒、彼此至少隔 0.5 秒,本段实际 192 帧" in page and "data-key-timeline" in page
    assert "第 72 帧" in page and "第 144 帧" in page and "关键帧 2" in page and "第 0 帧" in page
    assert "说话特写在哪儿切由台词决定" in page and "自己切 1–3 次镜头" in page          # 限制说明 + 切镜说明
    assert "data-full" not in page
    repo.add_keyframe(store, sample["seg"], 7.0, KEY_PROMPT)
    page = client.get(f"/segment/{sample['seg']}/edit").text
    assert "data-full" in page and "已有 3 张,不能再加" in page


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
