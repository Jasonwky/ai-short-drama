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


def _unchanged_form(sample, **changes):
    seg = sample["seg"]
    form = {"description": "【镜头1】林小雨低头看着玉佩。【镜头2】林小雨说：「这块玉佩，是我母亲留给我的。」",
            "duration_sec": "8", "image_prompt": "@林小雨 站在 @老宅庭院 里,手里拿着 @玉佩", "last_image_prompt": "",
            "video_prompt": "@林小雨 在 @老宅庭院 低头看 @玉佩,然后抬眼说话", "reference_mode": "first",
            "scene_id": str(sample["yard"]), "character_ids": [str(sample["lin"])], "prop_ids": [str(sample["jade"])]}
    return seg, {**form, **changes}


def test_video_only_edit_keeps_the_first_frame(client, store, sample):
    # 实战第 6 段:只改视频提示词 / 时长,首帧也被清掉,白白重出一张图(还可能变样)
    for changes in ({"video_prompt": "@林小雨 在 @老宅庭院 低头看 @玉佩,画面中没有任何文字"}, {"duration_sec": "9"}):
        seg, form = _unchanged_form(sample, **changes)
        repo.update(store, "segment", seg, first_frame_path="frames/s1.png", video_path="videos/s1.mp4")
        r = client.post(f"/segment/{seg}/edit", data=form)
        assert "1 条视频、0 张参考帧" in r.text
        client.post(f"/segment/{seg}/edit", data={**form, "confirm": "1"})
        row = repo.get(store, "segment", seg)
        assert (row["first_frame_path"], row["video_path"]) == ("frames/s1.png", None)


def test_frame_prompt_edit_still_clears_the_frame(client, store, sample):
    seg, form = _unchanged_form(sample, image_prompt="@林小雨 站在 @老宅庭院 里")
    client.post(f"/segment/{seg}/edit", data={**form, "confirm": "1"})
    assert repo.get(store, "segment", seg)["first_frame_path"] is None


def test_live_check_uses_the_same_rules(client):
    bad = client.post("/api/segment/check", data={"description": "【镜头1】只有一个镜头", "duration_sec": "3"}).json()
    assert bad["problems"]
    typo = client.post("/api/segment/check", data={"description": "x", "duration_sec": "abc"}).json()
    assert typo["problems"] == ["时长要填数字(秒),填的是 'abc'"]


def test_storyboard_lists_what_is_missing(client, store, sample):
    repo.update(store, "segment", sample["seg"], first_frame_path=None)
    assert "缺少首帧" in client.get(f"/episode/{sample['episode']}/storyboard").text
