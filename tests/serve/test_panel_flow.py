# -*- coding: utf-8 -*-
"""逐集面板的「全剧流程」一栏与两个新审阅页(spec 2026-10-25-全剧审与资产表-design.md 第 5 节)。"""
from tools.drama import repo
from tools.llm import llm_queue, runs, service

EP1 = "【老宅庭院 · 黄昏】\n林小雨说：「这块玉佩，是我母亲留给我的。」\n沈墨说：「我知道。」"


def _proposed(store, task, d, params, out):
    rid = runs.start(store, task=task, target_type="drama", target_id=d, model="m",
                     input_sha=service.prepare(store, task, d, params).sha, llm_mode="real", params=params)
    runs.propose(store, rid, out, "j")
    return rid


def test_flow_strip_and_asset_wall_render(client, store, sample):
    d = sample["drama"]
    page = client.get(f"/drama/{d}/episodes").text
    assert "data-flow" in page and 'data-flow-stage="r1"' in page and 'data-flow-auto="0"' in page
    assert f'data-wall-item="character-{sample["lin"]}"' in page and 'src="/media/assets/lin.png"' in page


def test_toggle_auto_queues_the_first_review(client, runtime, store, sample):
    d = sample["drama"]
    repo.update(store, "episode", sample["episode"], script=EP1)
    llm_queue.pause(store, "测试里不让队列真跑")
    assert client.post(f"/drama/{d}/flow/auto", data={"on": "1"}, follow_redirects=False).status_code == 303
    assert repo.get(store, "drama", d)["flow_auto"] == 1
    item = llm_queue.next_waiting(store)
    assert (item["task"], llm_queue.params(item)) == ("drama_review", {"stage": "script"})
    client.post(f"/drama/{d}/flow/auto", data={"on": "0"})
    assert repo.get(store, "drama", d)["flow_auto"] == 0


def test_manual_run_opens_the_review_page(client, runtime, store, sample):
    d = sample["drama"]
    repo.update(store, "episode", sample["episode"], script=EP1)
    r = client.post(f"/drama/{d}/flow/run/r1", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/llm/run/")
    assert client.post(f"/drama/{d}/flow/run/nope").status_code == 400


def test_drama_review_page_groups_issues_and_approves_into_revisions(client, store, sample):
    d, ep = sample["drama"], sample["episode"]
    repo.update(store, "episode", ep, script=EP1)
    it = {"where": "第1集", "quote": "沈墨说：「我知道。」", "category": "人设冲突", "severity": "高",
          "suggestion": "沈墨此时还不该知道", "also": []}
    rid = _proposed(store, "drama_review", d, {"stage": "script"}, {"issues": [it]})
    page = client.get(f"/llm/run/{rid}").text
    assert 'data-drama-review="script"' in page and 'data-review-where="第1集"' in page and "data-reject-all" in page
    assert client.post(f"/llm/run/{rid}/approve", data={}).status_code == 400              # 没表态
    r = client.post(f"/llm/run/{rid}/approve", data={"issue-0": "adopt"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/drama/{d}/episodes"
    item = store.conn.execute("SELECT task, target_id FROM llm_queue").fetchone()
    assert tuple(item) == ("script_revise", ep)
    assert "data-review-queued" in client.get(f"/llm/run/{rid}").text


def test_cast_review_page_lists_rows_coverage_and_images(client, store, sample):
    d = sample["drama"]
    repo.update(store, "episode", sample["episode"], script=EP1)
    fp = "A young woman in a plain blue dress."
    out = {"characters": [{"name": n, "aliases": [], "appearance": "年轻", "styling": "蓝裙", "final_prompt": fp}
                          for n in ("林小雨", "沈墨")],
           "scenes": [{"name": "老宅庭院", "places": ["老宅庭院"], "location": "老宅", "time": "黄昏", "lighting": "斜阳",
                       "furnishings": "石桌", "final_prompt": "An old courtyard."}],
           "props": [{"name": "玉佩", "description": "白玉", "final_prompt": "A white jade pendant."}]}
    rid = _proposed(store, "cast", d, {"notes": []}, out)
    page = client.get(f"/llm/run/{rid}").text
    assert "data-cast-review" in page and 'data-cast-speaker="沈墨"' in page and 'data-cast-place="老宅庭院"' in page
    assert 'data-cast-row="character-林小雨" data-status="补字段"' in page and 'src="/media/assets/lin.png"' in page
    assert client.post(f"/llm/run/{rid}/approve", data={}, follow_redirects=False).status_code == 303
    assert repo.get(store, "character", sample["lin"])["styling"] == "蓝裙"
    assert "data-cast-approved" in client.get(f"/llm/run/{rid}").text


def test_review_page_lets_the_reviewer_pick_the_episode_to_fix(client, store, sample):
    d, ep = sample["drama"], sample["episode"]
    repo.update(store, "episode", ep, script=EP1)
    e2 = repo.add_episode(store, d, 2, "script", script=EP1)
    it = {"where": "第1集", "quote": "沈墨说：「我知道。」", "category": "台词重复", "severity": "低",
          "suggestion": "后一集换句台词", "also": [{"where": "第2集", "quote": "沈墨说：「我知道。」"}]}
    rid = _proposed(store, "drama_review", d, {"stage": "script"}, {"issues": [it]})
    assert 'data-issue-fix="0"' in client.get(f"/llm/run/{rid}").text
    client.post(f"/llm/run/{rid}/approve", data={"issue-0": "adopt", "fix-0": "第2集"})
    assert tuple(store.conn.execute("SELECT task, target_id FROM llm_queue").fetchone()) == ("script_revise", e2)
    assert "采纳后改的是第2集" in client.get(f"/llm/run/{rid}").text
