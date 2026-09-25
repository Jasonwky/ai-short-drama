# -*- coding: utf-8 -*-
"""资产面板:音色锁定(改声音描述先报代价)与种子锁定。"""
from tools.drama import repo
from tools.drama.tasks import create_task, set_task_state, tasks_for


def _edit(client, sample, **over):
    data = {"appearance": "", "styling": "", "voice_desc": "清亮女声", "final_prompt": "A young woman.", **over}
    return client.post(f"/asset/character/{sample['lin']}", data=data, follow_redirects=False)


def test_changing_the_voice_asks_first_and_only_drops_videos(client, store, sample):
    r = _edit(client, sample, voice_desc="低沉女声")
    assert r.status_code == 200 and "改声音描述:这会重跑 1 条视频、0 张参考帧" in r.text
    assert repo.get(store, "character", sample["lin"])["voice_desc"] == "清亮女声"          # 还没改
    r = _edit(client, sample, voice_desc="低沉女声", confirm="1")
    assert r.status_code == 303
    seg = repo.get(store, "segment", sample["seg"])
    assert repo.get(store, "character", sample["lin"])["voice_desc"] == "低沉女声"
    assert seg["video_path"] is None and seg["first_frame_path"] == "frames/s1.png"        # 参考帧不动


def test_editing_other_fields_does_not_ask(client, store, sample):
    assert _edit(client, sample, appearance="二十出头").status_code == 303
    assert repo.get(store, "segment", sample["seg"])["video_path"] == "videos/s1.mp4"


def _done(store, character_id, seed):
    t = create_task(store, kind="image", target_type="character", target_id=character_id, model="m",
                    params={"writes": "turnaround_path"}, seed=seed, prompt="p", refs=[])
    set_task_state(store, t, "queued", external_id="x")
    set_task_state(store, t, "done", result_path=f"outputs/tasks/{t}/a.png")
    return t


def test_seed_lock_is_enforced_until_unlocked(client, runtime, store, sample):
    repo.update(store, "character", sample["lin"], final_prompt="A young woman.")
    store.conn.execute("UPDATE drama SET style = 'realistic' WHERE id = ?", (sample["drama"],))
    t = _done(store, sample["lin"], 777)
    assert "data-lock-seed" in client.get(f"/drama/{sample['drama']}/assets").text
    client.post(f"/asset/character/{sample['lin']}/seed", data={"task_id": str(t)})
    assert repo.get(store, "character", sample["lin"])["seed"] == 777
    page = client.get(f"/drama/{sample['drama']}/assets").text
    assert 'value="777" readonly data-seed-locked' in page and "data-unlock-seed" in page

    r = client.post(f"/asset/character/{sample['lin']}/generate", data={"seed": "5"})
    assert r.status_code == 400 and "种子已锁定为 777" in r.text
    client.post(f"/asset/character/{sample['lin']}/generate", data={"seed": "777"})
    assert tasks_for(store, "character", sample["lin"])[-1]["seed"] == 777

    client.post(f"/asset/character/{sample['lin']}/seed", data={"unlock": "1"})
    assert repo.get(store, "character", sample["lin"])["seed"] is None


def test_asset_fill_uses_the_locked_seed(client, runtime, store):
    d = repo.create_drama(store, "新剧", "9:16", "realistic")
    lin = repo.add_character(store, d, "林小雨", final_prompt="A young woman.")
    repo.update(store, "character", lin, seed=4242)
    page = client.post(f"/fill/assets/{d}", data={"base_seed": "10"}).text
    assert ">4242<" in page                                                       # 确认页上就是锁定种子
    client.post(f"/fill/assets/{d}", data={"base_seed": "10", "t": [f"character:{lin}"], "confirm": "1"})
    assert tasks_for(store, "character", lin)[-1]["seed"] == 4242


def test_cannot_lock_someone_elses_seed(client, store, sample):
    t = _done(store, sample["shen"], 9)
    r = client.post(f"/asset/character/{sample['lin']}/seed", data={"task_id": str(t)})
    assert r.status_code == 400 and "不是这个角色已完成的参考图" in r.text
