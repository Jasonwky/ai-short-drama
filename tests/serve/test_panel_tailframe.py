# -*- coding: utf-8 -*-
"""参考帧面板的尾帧入口:下一段已有视频时先报代价再确认。"""
from tests.serve.test_panel_final import two_videos
from tools.drama import repo


def test_tail_frame_button_and_confirm(client, runtime, store, sample):
    seg2 = two_videos(store, sample, runtime.data_root)
    page = client.get(f"/episode/{sample['episode']}/frames").text
    assert f'action="/segment/{sample["seg"]}/tailframe"' in page and "用段落 1 视频的尾帧" in page

    r = client.post(f"/segment/{sample['seg']}/tailframe")                     # 段落 2 已有视频 → 先报代价
    assert r.status_code == 200 and "重跑 1 条视频" in r.text and 'name="confirm"' in r.text
    assert repo.get(store, "segment", seg2)["first_frame_path"] is None

    r = client.post(f"/segment/{sample['seg']}/tailframe", data={"confirm": "1"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith(f"#seg-{seg2}")
    seg = repo.get(store, "segment", seg2)
    assert seg["first_frame_path"].startswith("outputs/tailframes/") and seg["video_path"] is None


def test_tail_frame_refusal_is_a_readable_400(client, sample):
    r = client.post(f"/segment/{sample['seg']}/tailframe")
    assert r.status_code == 400 and "视频文件不在本机" in r.text
