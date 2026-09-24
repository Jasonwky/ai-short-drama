# -*- coding: utf-8 -*-
from tools.drama import repo


def test_add_episodes_numbered_in_order(client, store):
    did = repo.create_drama(store, "新剧", "9:16", "realistic")
    client.post(f"/drama/{did}/episodes")
    client.post(f"/drama/{did}/episodes")
    numbers = [r[0] for r in store.conn.execute("SELECT number FROM episode WHERE drama_id = ? ORDER BY number",
                                                (did,))]
    assert numbers == [1, 2]


def test_missing_drama_is_a_404_page_not_500(client):
    r = client.get("/drama/999/script")
    assert r.status_code == 404 and "找不到" in r.text


def test_script_change_on_generated_episode_asks_first(client, store, sample):
    ep = sample["episode"]
    r = client.post(f"/episode/{ep}/script", data={"script": "新剧本"})
    assert r.status_code == 200 and "data-cost" in r.text and "1 条视频" in r.text
    assert repo.get(store, "episode", ep)["script"] != "新剧本"            # 没确认前什么都不改
    r = client.post(f"/episode/{ep}/script", data={"script": "新剧本", "confirm": "1"}, follow_redirects=False)
    assert r.status_code == 303 and repo.get(store, "episode", ep)["script"] == "新剧本"
    assert repo.get(store, "segment", sample["seg"])["video_path"] is None


def test_script_edit_without_generated_products_saves_directly(client, store):
    did = repo.create_drama(store, "新剧", "9:16", "realistic")
    ep = repo.add_episode(store, did, 1, "script")
    r = client.post(f"/episode/{ep}/script", data={"script": "字" * 600})
    assert repo.get(store, "episode", ep)["script"] == "字" * 600
    assert "应拆" in r.text
