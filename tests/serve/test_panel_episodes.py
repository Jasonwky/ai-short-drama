# -*- coding: utf-8 -*-
"""逐集面板(spec 2026-10-24-逐集批量出剧本分镜-design.md 第 1、2、5 节)。"""
from tools.drama import repo
from tools.llm import llm_queue as q, runs, service


def test_panel_lists_each_episode_with_its_states(client, store, sample):
    page = client.get(f"/drama/{sample['drama']}/episodes").text
    assert f'data-episode-row="{sample["episode"]}"' in page and "data-batch-scripts" in page


def test_batch_shows_a_plan_then_enqueues_only_on_confirm(client, runtime, store, sample):
    d = sample["drama"]
    e2 = repo.add_episode(store, d, 2, "script", outline="林小雨回老宅。")
    page = client.post(f"/drama/{d}/episodes/batch", data={}).text
    assert 'data-batch-item="todo"' in page and 'data-batch-item="has"' in page and q.next_waiting(store) is None
    assert f'name="ids" value="{e2}"' in page
    r = client.post(f"/drama/{d}/episodes/batch", data={"confirm": "1", "ids": str(e2), "minutes": "1"},
                    follow_redirects=False)
    assert r.status_code == 303
    runtime.wait_llm()
    assert store.conn.execute("SELECT state FROM llm_queue").fetchone()[0] == "started"


def test_cancel_and_resume(client, runtime, store, sample):
    d = sample["drama"]
    q.pause(store, "LLM 不可用")
    e2 = repo.add_episode(store, d, 2, "script", outline="林小雨回老宅。")
    client.post(f"/drama/{d}/episodes/batch", data={"confirm": "1", "ids": str(e2), "minutes": "1"})
    page = client.get(f"/drama/{d}/episodes").text
    assert "data-queue-paused" in page and "停掉还没开始的(1 条)" in page
    client.post(f"/drama/{d}/episodes/cancel")
    assert q.waiting_count(store, [e2]) == 0
    client.post(f"/drama/{d}/episodes/resume")
    assert q.paused(store) == ""


def test_new_empty_episodes_continue_the_numbering(client, store, sample):
    client.post(f"/drama/{sample['drama']}/episodes/new", data={"n": "3"})
    nums = [r[0] for r in store.conn.execute("SELECT number FROM episode WHERE drama_id = ? ORDER BY number",
                                             (sample["drama"],))]
    assert nums == [1, 2, 3, 4]
    assert client.post(f"/drama/{sample['drama']}/episodes/new", data={"n": "0"}).status_code == 400


def test_approving_a_whole_script_enqueues_the_storyboard(client, store, sample):
    d = sample["drama"]
    e2 = repo.add_episode(store, d, 2, "script", outline="林小雨回老宅。")
    params = {"minutes": 1}
    sha = service.prepare(store, "script_outline", e2, params).sha
    rid = runs.start(store, task="script_outline", target_type="episode", target_id=e2, model="m", input_sha=sha,
                     llm_mode="real", params=params)
    scenes = [{"place": "老宅庭院", "time": "黄昏", "lines": [
        {"kind": "action", "speaker": None, "text": "林小雨推开老宅斑驳的木门在石桌旁慢慢坐下望着那口古井出神良久" * 9},
        {"kind": "dialogue", "speaker": "林小雨", "text": "这块玉佩，是我母亲留给我的。"}]}]
    runs.propose(store, rid, {"scenes": scenes}, "j")
    client.post(f"/llm/run/{rid}/approve", data={})
    item = store.conn.execute("SELECT task, target_id FROM llm_queue").fetchone()
    assert tuple(item) == ("storyboard", e2)
