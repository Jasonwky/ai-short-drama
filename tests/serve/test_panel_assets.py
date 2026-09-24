# -*- coding: utf-8 -*-
from tools.drama import repo
from tools.drama.tasks import create_task, set_task_state, tasks_for


def test_duplicate_asset_name_is_refused(client, sample):
    r = client.post(f"/drama/{sample['drama']}/assets", data={"kind": "character", "name": "林小雨"})
    assert r.status_code == 400 and "已经有叫「林小雨」" in r.text


def test_edit_then_generate_plans_a_task(client, store, sample, runtime):
    store.conn.execute("UPDATE drama SET style = 'realistic' WHERE id = ?", (sample["drama"],))
    client.post(f"/asset/character/{sample['lin']}", data={"appearance": "", "styling": "", "voice_desc": "清亮女声",
                                                           "final_prompt": "A young woman."})
    assert repo.get(store, "character", sample["lin"])["voice_desc"] == "清亮女声"
    client.post(f"/asset/character/{sample['lin']}/generate", data={"seed": "7"})
    (t,) = tasks_for(store, "character", sample["lin"])
    assert t["seed"] == 7 and (runtime.data_root / "assets/_canvas/gray_1024.png").exists()
    assert "待提交" in client.get(f"/drama/{sample['drama']}/assets").text


def test_generate_without_prompt_explains_why(client, sample):
    r = client.post(f"/asset/prop/{sample['jade']}/generate", data={"seed": "1"})
    assert r.status_code == 400 and "final_prompt" in r.text


def test_pick_older_version_with_cost_confirmation(client, store, sample):
    t = create_task(store, kind="image", target_type="segment", target_id=sample["seg"], model="m",
                    params={"writes": "first_frame_path"}, seed=1, prompt="p", refs=[])
    set_task_state(store, t, "queued", external_id="x")
    set_task_state(store, t, "done", result_path="outputs/tasks/1/a.png")
    nxt = f"/episode/{sample['episode']}/frames"
    r = client.post(f"/task/{t}/pick", data={"next": nxt})
    assert "1 条视频" in r.text
    r = client.post(f"/task/{t}/pick", data={"next": nxt, "confirm": "1"}, follow_redirects=False)
    assert r.headers["location"] == nxt
    assert repo.get(store, "segment", sample["seg"])["first_frame_path"] == "outputs/tasks/1/a.png"
    assert client.post(f"/task/{t}/pick", data={"next": "//evil.example"}).status_code == 400
