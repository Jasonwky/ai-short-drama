# -*- coding: utf-8 -*-
"""立项发散的面板(spec 2026-10-23-剧集脑爆系统-design.md 第 1 节)。"""
import json

from tools.drama import repo
from tools.llm import premise, runs, service


def _cand(i):
    dims = list(premise.DIMS)
    return {"title": f"片名{i}", "logline": f"卖点{i}", **{d: premise.DIMS[d][i] for d in dims},
            "protagonist": {"name": f"主角{i}", "tag": "标签", "contrast": "反差"}, "antagonist": "对手",
            "hook": f"钩子{i}", "typicality": [0.2, 0.9, 0.5][i]}


def _run(store, d):
    params = {"idea": "替嫁冲喜的庶女其实是神医", "n": 3, "constraint": ""}
    sha = service.prepare(store, "premise", d, params).sha
    rid = runs.start(store, task="premise", target_type="drama", target_id=d, model="m", input_sha=sha,
                     llm_mode="real", params=params)
    runs.propose(store, rid, {"candidates": [_cand(0), _cand(1), _cand(2)]}, "j")
    return rid


def test_project_list_links_to_the_premise_page(client, store, sample):
    assert f'/drama/{sample["drama"]}/premise' in client.get("/").text
    page = client.get(f'/drama/{sample["drama"]}/premise').text
    assert "data-llm-premise" in page and 'name="idea"' in page
    assert f'action="/llm/start/premise/{sample["drama"]}"' in page      # 表单要带上剧 id


def test_review_page_shows_candidates_routine_tag_and_field_radios(client, store, sample):
    rid = _run(store, sample["drama"])
    page = client.get(f"/llm/run/{rid}").text
    assert page.count("data-premise-candidate=") == 3 and page.count("data-routine") == 1   # 典型度最高的是方案 2
    assert page.count('name="pick-title"') == 3 and 'form="approve-form"' in page
    assert "data-pick-all" in page


def test_approving_assembles_fields_and_the_page_shows_it(client, store, sample):
    rid = _run(store, sample["drama"])
    form = {f"pick-{f}": "0" for f in premise.FIELDS} | {"pick-title": "2"}
    r = client.post(f"/llm/run/{rid}/approve", data=form, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f'/drama/{sample["drama"]}/premise'
    saved = json.loads(repo.get(store, "drama", sample["drama"])["premise_json"])
    assert saved["title"] == "片名2" and saved["logline"] == "卖点0"
    assert "片名2" in client.get(f'/drama/{sample["drama"]}/premise').text
    assert client.post(f"/llm/run/{_run(store, sample['drama'])}/approve", data={"pick-title": "0"}).status_code == 400
