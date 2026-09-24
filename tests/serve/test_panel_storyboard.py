# -*- coding: utf-8 -*-
from tools.drama import repo


def test_new_segment_opens_its_editor(client, sample):
    r = client.post(f"/episode/{sample['episode']}/segments", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/edit")
    assert "还差什么" in client.get(r.headers["location"]).text


def test_segment_edit_rebinds_and_asks_before_invalidating(client, store, sample):
    seg = sample["seg"]
    form = {"description": "【镜头1】林小雨抬头。【镜头2】沈墨走进来。", "duration_sec": "8", "image_prompt": "@林小雨",
            "video_prompt": "", "reference_mode": "first", "scene_id": str(sample["yard"]),
            "character_ids": [str(sample["lin"]), str(sample["shen"])], "prop_ids": []}
    r = client.post(f"/segment/{seg}/edit", data=form)
    assert "data-cost" in r.text and 'name="character_ids"' in r.text      # 确认页把多选字段原样带回
    client.post(f"/segment/{seg}/edit", data={**form, "confirm": "1"})
    assets = repo.segment_assets(store, seg)
    assert [c["name"] for c in assets["characters"]] == ["林小雨", "沈墨"] and assets["props"] == []
    assert repo.get(store, "segment", seg)["first_frame_path"] is None


def test_live_check_uses_the_same_rules(client):
    bad = client.post("/api/segment/check", data={"description": "【镜头1】只有一个镜头", "duration_sec": "3"}).json()
    assert bad["problems"]
    typo = client.post("/api/segment/check", data={"description": "x", "duration_sec": "abc"}).json()
    assert typo["problems"] == ["时长要填数字(秒),填的是 'abc'"]


def test_storyboard_lists_what_is_missing(client, store, sample):
    repo.update(store, "segment", sample["seg"], first_frame_path=None)
    assert "缺少首帧" in client.get(f"/episode/{sample['episode']}/storyboard").text
