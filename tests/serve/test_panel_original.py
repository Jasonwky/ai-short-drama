# -*- coding: utf-8 -*-
"""原创分集的控制台(spec 2026-10-24-原创分集-design.md):大纲页入口与批次表、骨架 / 展开审阅页、按方案批准。
2026-10-24 独立复查指出这一层没有单测守着,补上。"""
import json

from tools.drama import repo
from tools.llm import expand, runs, service, skeleton as sk

PREMISE = {"title": "替嫁王妃", "logline": "冲喜新娘一针救活王爷", "genre": "古装权谋", "payoff": "打脸",
           "conflict": "身份错位", "hook_type": "直接冲突",
           "protagonist": {"name": "沈青禾", "tag": "庶女神医", "contrast": "表面柔弱实则狠"},
           "antagonist": "嫡姐", "hook": "大婚夜她跪在堂中被人羞辱"}


def _proposed(store, task, d, params, out):
    rid = runs.start(store, task=task, target_type="drama", target_id=d, model="m",
                     input_sha=service.prepare(store, task, d, params).sha, llm_mode="real", params=params)
    runs.propose(store, rid, out, "j")
    return rid


def test_outline_page_asks_for_a_premise_first(client, sample):
    page = client.get(f"/drama/{sample['drama']}/outline").text
    assert "data-original" in page and f'href="/drama/{sample["drama"]}/premise"' in page
    assert "data-llm-skeleton" not in page


def test_skeleton_review_then_pick_then_batches_on_the_outline_page(client, store, sample):
    d = sample["drama"]
    repo.update(store, "drama", d, premise_json=json.dumps(PREMISE, ensure_ascii=False))
    assert 'name="total" value="30" min="2"' in client.get(f"/drama/{d}/outline").text
    params = {"total": 60, "n": 3, "constraint": ""}
    out = sk.fake_sample(sk.schema({"n": 3}))
    rid = _proposed(store, "skeleton", d, params, out)
    page = client.get(f"/llm/run/{rid}").text
    assert page.count("data-skeleton-candidate=") == 3 and 'name="pick"' in page and "data-skeleton-paywall" in page
    assert "data-skeleton-kept" in page                           # 夹具第 1 集有剧本:展开时不覆盖,要提示
    assert client.post(f"/llm/run/{rid}/approve", data={}).status_code == 400          # 没挑方案
    assert client.post(f"/llm/run/{rid}/approve", data={"pick": "1"}, follow_redirects=False).status_code == 303
    assert sk.load(store, d)["arc"] == out["candidates"][1]["arc"]
    page = client.get(f"/drama/{d}/outline").text
    first, second = sk.batches(sk.load(store, d))[:2]
    assert f'data-expand-part="{first[1]}"' in page and 'name="start" value="1"' in page
    assert "data-expand-start" in page and "data-expand-waiting" in page             # 第 2 批要等第 1 批


def test_expand_review_page_labels_new_fill_and_keep(client, store, sample):
    d = sample["drama"]
    repo.update(store, "drama", d, premise_json=json.dumps(PREMISE, ensure_ascii=False))
    skel = {"arc": "复仇清算", "rhythm": "层层加码", "ending": "反转", "total": 4,
            "acts": [{"title": "甲", "goal": "g", "climax": "c", "weight": 0.5, "start": 1, "end": 2},
                     {"title": "乙", "goal": "g", "climax": "c", "weight": 0.5, "start": 3, "end": 4}]}
    repo.update(store, "drama", d, skeleton_json=json.dumps(skel, ensure_ascii=False))
    repo.add_episode(store, d, 2, "script")                                          # 空集:填
    params = {"start": 1, "end": 2}
    rid = _proposed(store, "expand", d, params, expand.fake_sample(expand.schema(expand.context(store, d, 1, 2))))
    page = client.get(f"/llm/run/{rid}").text
    assert 'data-expand-episode="1" data-status="keep"' in page                     # 第 1 集已有剧本
    assert 'data-expand-episode="2" data-status="fill"' in page and "已有,不覆盖" in page
    assert client.post(f"/llm/run/{rid}/approve", data={}, follow_redirects=False).status_code == 303
    assert repo.get(store, "episode", sample["episode"])["outline"] == ""            # 有剧本的第 1 集没被覆盖
