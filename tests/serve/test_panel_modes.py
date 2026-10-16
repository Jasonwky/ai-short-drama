# -*- coding: utf-8 -*-
"""P7 的面板:段落选首尾帧模式并写尾帧提示词 → 参考帧面板出尾帧;资产面板出角色四视图;两者都能增量补全。"""
from tools.drama import repo
from tools.drama.tasks import tasks_for
from tools.serve.hosts import add_host

LAST = "@林小雨 转身走向 @老宅庭院 深处,手里握着 @玉佩"


def _world(store, sample, runtime):
    for rel in ("assets/lin.png", "assets/shen.png", "assets/yard.png", "assets/jade.png", "frames/s1.png"):
        (runtime.data_root / rel).parent.mkdir(parents=True, exist_ok=True)
        (runtime.data_root / rel).write_bytes(b"png")
    add_host(store, label="fake", ssh_host="h", ssh_port=1, ssh_user="root", key_ref="k", local_port=8188,
             vram_gb=24.0, models_dir="/m")


def test_segment_editor_offers_first_last_and_saves_the_last_frame_prompt(client, store, sample):
    seg = repo.get(store, "segment", sample["seg"])
    page = client.get(f"/segment/{seg['id']}/edit").text
    assert 'value="first_last"' in page and 'name="last_image_prompt"' in page
    form = {k: seg[k] for k in ("description", "image_prompt", "video_prompt")} | {
        "duration_sec": "8", "reference_mode": "first_last", "last_image_prompt": LAST,
        "scene_id": str(sample["yard"]), "character_ids": [str(sample["lin"])], "prop_ids": [str(sample["jade"])],
        "confirm": "1"}
    client.post(f"/segment/{seg['id']}/edit", data=form)
    seg = repo.get(store, "segment", sample["seg"])
    assert (seg["reference_mode"], seg["last_image_prompt"]) == ("first_last", LAST)


def test_last_frame_is_generated_on_the_frames_panel(client, runtime, store, sample):
    _world(store, sample, runtime)
    repo.update(store, "segment", sample["seg"], reference_mode="first_last", last_image_prompt=LAST)
    page = client.get(f"/episode/{sample['episode']}/frames").text
    assert "data-generate-last-frame" in page and "还没有尾帧" in page
    client.post(f"/segment/{sample['seg']}/lastframe", data={"seed": "5"})
    runtime.tick_all()
    runtime.tick_all()
    seg = repo.get(store, "segment", sample["seg"])
    assert seg["last_frame_path"].endswith(".png") and seg["first_frame_path"] == "frames/s1.png"
    assert f'src="/media/{seg["last_frame_path"]}" alt="尾帧"' in client.get(f"/episode/{sample['episode']}/frames").text


def test_first_mode_hides_the_last_frame_button(client, sample):
    assert "data-generate-last-frame" not in client.get(f"/episode/{sample['episode']}/frames").text


def test_fourview_is_generated_on_the_assets_panel(client, runtime, store, sample):
    _world(store, sample, runtime)
    repo.update(store, "character", sample["lin"], final_prompt="A young woman in a cream blouse.")
    store.conn.execute("UPDATE drama SET style = 'realistic' WHERE id = ?", (sample["drama"],))
    page = client.get(f"/drama/{sample['drama']}/assets").text
    assert "data-generate-fourview" in page and "还没有四视图" in page
    client.post(f"/asset/character/{sample['lin']}/fourview", data={"seed": "11"})
    runtime.tick_all()
    runtime.tick_all()
    lin = repo.get(store, "character", sample["lin"])
    assert lin["fourview_path"].endswith(".png") and lin["turnaround_path"] == "assets/lin.png"
    page = client.get(f"/drama/{sample['drama']}/assets").text
    assert f'src="/media/{lin["fourview_path"]}"' in page and "data-fourview" in page


def test_fourview_without_front_image_is_a_readable_400(client, store, sample):
    repo.update(store, "character", sample["lin"], turnaround_path=None)
    r = client.post(f"/asset/character/{sample['lin']}/fourview", data={"seed": "1"})
    assert r.status_code == 400 and "正面定妆图" in r.text
    assert tasks_for(store, "character", sample["lin"]) == []


def test_fill_last_frames_and_fourviews(client, store, sample):
    repo.update(store, "segment", sample["seg"], reference_mode="first_last", last_image_prompt=LAST)
    r = client.post(f"/fill/lastframes/{sample['episode']}", data={"base_seed": "7"})
    assert "将提交 1 条尾帧" in r.text and 'data-panel="frames"' in r.text
    repo.update(store, "character", sample["lin"], final_prompt="A young woman.")
    store.conn.execute("UPDATE drama SET style = 'realistic' WHERE id = ?", (sample["drama"],))
    r = client.post(f"/fill/fourviews/{sample['drama']}", data={"base_seed": "7"})
    assert "将提交 1 条四视图" in r.text and 'data-panel="assets"' in r.text
    r = client.post(f"/fill/fourviews/{sample['drama']}", data={"base_seed": "7", "t": [f"character:{sample['lin']}"],
                                                                 "confirm": "1"}, follow_redirects=False)
    assert r.headers["location"] == f"/drama/{sample['drama']}/assets"
    [t] = tasks_for(store, "character", sample["lin"])
    assert t["seed"] == 7 and '"fourview_path"' in t["params_json"]


def test_text_only_video_is_refused_with_the_measured_reason(client, store, sample):
    """纯文字不做成可选的出片方式:实测场景和道具守不住(编辑页的下拉项写同一个原因)。"""
    import pytest

    from tools.gen.planner import plan_video
    repo.update(store, "segment", sample["seg"], reference_mode="text_only")
    with pytest.raises(ValueError, match="纯文字模式不支持出视频:实测场景和道具守不住"):
        plan_video(store, sample["seg"], seed=1)
    assert "纯文字(不支持:实测场景和道具守不住)" in client.get(f"/segment/{sample['seg']}/edit").text
