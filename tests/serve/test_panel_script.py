# -*- coding: utf-8 -*-
from tools.drama import repo


def test_add_episodes_numbered_in_order(client, store):
    did = repo.create_drama(store, "新剧", "9:16", "realistic")
    client.post(f"/drama/{did}/episodes")
    client.post(f"/drama/{did}/episodes")
    numbers = [r[0] for r in store.conn.execute("SELECT number FROM episode WHERE drama_id = ? ORDER BY number",
                                                (did,))]
    assert numbers == [1, 2]


def test_script_card_shows_the_rewrite_sync_result_once(client, store, sample):
    from tools.llm import runs
    rid = runs.start(store, task="rewrite", target_type="episode", target_id=sample["episode"], model="m",
                     input_sha="x", llm_mode="real", params={"span": "x", "intent": "加强冲突", "note": ""})
    runs.propose(store, rid, {"candidates": [{"text": "甲", "why": "a"}, {"text": "乙", "why": "b"}]}, "j")
    runs.approve(store, rid, {"pick": 0})
    base = f"/drama/{sample['drama']}/script"
    assert "data-rewrite-notice" not in client.get(base).text                  # 平时没有提示
    page = client.get(f"{base}?rewrite_run={rid}&synced=2").text
    assert page.count("data-rewrite-notice") == 1 and "第 2 段的台词已同步" in page and "重出后在历史里" in page
    assert "data-rewrite-notice" not in client.get(f"{base}?rewrite_run=99999&synced=2").text   # 不是真的运行:不显示
    page = client.get(f"{base}?rewrite_run={rid}&synced=0").text
    assert "没对上任何段落" in page and "分镜没跟着改" in page


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


def test_script_card_has_critique_rewrite_and_adoption(client, store, sample):
    from tools.llm import critique, runs
    page = client.get(f"/drama/{sample['drama']}/script").text
    assert "data-llm-critique" in page and all(f"<option>{r}</option>" in page for r in critique.ROLES)
    assert "data-llm-rewrite" in page and "data-adoption" not in page          # 还没评审过,不显示采纳率
    repo.update(store, "episode", sample["episode"], script="林小雨说：「这块玉佩，是我母亲留给我的。」\n")
    issue = {"quote": "这块玉佩，是我母亲留给我的", "category": "动机不足", "severity": "中", "suggestion": "加一句"}
    rid = runs.start(store, task="critique", target_type="episode", target_id=sample["episode"], model="m",
                     input_sha="x", llm_mode="real", params={"role": "编剧"})
    runs.propose(store, rid, {"issues": [issue]}, "j")
    runs.approve(store, rid, {"adopt": [0], "reject": []})
    page = client.get(f"/drama/{sample['drama']}/script").text
    assert "编剧 1/1" in page and "data-adopted-issue" in page and "按这条改" in page
