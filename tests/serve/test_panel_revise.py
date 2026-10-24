# -*- coding: utf-8 -*-
"""修改回路的控制台:发起、审阅页分组、按意见批准、回滚两态(spec 2026-10-24-剧本分镜修改回路-design.md 第 3 节)。"""
from tools.drama import repo
from tools.llm import runs, script_revise as sr, service

LINE = "林小雨说：「这块玉佩，是我母亲留给我的。」"


def _proposed(store, ep, notes, responses):
    params = {"notes": notes}
    sha = service.prepare(store, sr.TASK, ep, params).sha
    rid = runs.start(store, task=sr.TASK, target_type="episode", target_id=ep, model="m", input_sha=sha,
                     llm_mode="real", params=params)
    runs.propose(store, rid, {"responses": responses}, "j")
    return rid


def _setup(store, sample):
    repo.update(store, "episode", sample["episode"], script=f"林小雨低头。\n{LINE}\n")
    return sample["episode"]


def test_start_form_collects_notes(client, store, sample):
    ep = _setup(store, sample)
    r = client.post(f"/llm/start/script_revise/{ep}", data={"note-0": "台词太平", "must-0": "1", "note-1": ""},
                    follow_redirects=False)
    assert r.status_code == 303
    row = runs.history(store, "episode", ep, "script_revise")[-1]
    assert runs.params(row) == {"notes": [{"text": "台词太平", "target": "", "must": True}]}


def test_review_groups_by_note_and_lists_skipped(client, store, sample):
    ep = _setup(store, sample)
    rid = _proposed(store, ep, [{"text": "台词太平", "target": "", "must": False},
                                {"text": "加个丫鬟", "target": "", "must": False}],
                    [{"note": 1, "verdict": "改", "reason": "更狠", "edits": [
                        {"old": LINE, "new": "林小雨说：「玉佩是娘的。」", "why": "更狠"}]},
                     {"note": 2, "verdict": "不改", "reason": "没有这个角色", "edits": []}])
    page = client.get(f"/llm/run/{rid}").text
    assert page.count("data-revise-group=") == 1 and 'name="decide-1"' in page
    assert "data-revise-skipped" in page and "没有这个角色" in page


def test_approve_by_note_then_rollback_with_confirmation(client, store, sample):
    ep = _setup(store, sample)
    rid = _proposed(store, ep, [{"text": "台词太平", "target": "", "must": False}],
                    [{"note": 1, "verdict": "改", "reason": "更狠", "edits": [
                        {"old": LINE, "new": "林小雨说：「玉佩是娘的。」", "why": "更狠"}]}])
    r = client.post(f"/llm/run/{rid}/approve", data={"decide-1": "accept"}, follow_redirects=False)
    assert r.status_code == 303 and "玉佩是娘的" in repo.get(store, "episode", ep)["script"]
    page = client.post(f"/llm/run/{rid}/rollback", data={}).text                 # 第一态:确认页
    assert "撤销 1 处修改" in page and "玉佩是娘的" in repo.get(store, "episode", ep)["script"]
    client.post(f"/llm/run/{rid}/rollback", data={"confirm": "1"})
    assert repo.get(store, "episode", ep)["script"] == f"林小雨低头。\n{LINE}\n"
    assert client.post(f"/llm/run/{rid}/rollback", data={}).status_code == 400   # 回滚过了,不能再回滚
