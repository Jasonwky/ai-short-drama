# -*- coding: utf-8 -*-
"""控制台里的 LLM 路径(TestClient + 假 / 脚本化 LLM)。不声称覆盖 LLM 契约。"""
import pytest

from tools.drama import repo
from tools.llm import runs
from tools.llm.client import LLMUnavailable
from tools.llm.fake import FakeLLM
from tools.llm import service
from tools.llm.service import LLMBackend

VOICE = {"gender": "female", "age": "young_adult", "timbre": "清亮柔和", "pace": "slow"}


@pytest.fixture(autouse=True)
def _lin_has_a_look(store, sample):
    repo.update(store, "character", sample["lin"], appearance="二十出头,长发")


def _start_voice(client, runtime, sample):
    r = client.post(f"/llm/start/voice/{sample['lin']}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/llm/run/")
    runtime.wait_llm()
    return int(r.headers["location"].rsplit("/", 1)[1])


def test_fake_llm_output_is_shown_but_cannot_be_approved(client, runtime, store, sample):
    rid = _start_voice(client, runtime, sample)
    page = client.get(f"/llm/run/{rid}").text
    assert 'data-llm-state="proposed"' in page and "假 LLM" in page and "disabled data-approve" in page
    assert "青年女声,清亮柔和" in page                                   # 产物预览
    r = client.post(f"/llm/run/{rid}/approve")
    assert r.status_code == 400 and "假 LLM" in r.text
    assert repo.get(store, "character", sample["lin"])["voice_desc"] == "清亮女声"


def test_real_mode_output_is_written_only_after_approval(client, runtime, store, sample):
    runtime.backend.llm = lambda: LLMBackend(FakeLLM({"submit_voice": VOICE}), "gpt-5.5", "real")
    rid = _start_voice(client, runtime, sample)
    assert repo.get(store, "character", sample["lin"])["voice_desc"] == "清亮女声"
    r = client.post(f"/llm/run/{rid}/approve", follow_redirects=False)
    assert r.headers["location"] == f"/drama/{sample['drama']}/assets"
    assert repo.get(store, "character", sample["lin"])["voice_desc"] == "青年女声,清亮柔和,语速偏慢"


def test_llm_failures_are_visible(client, runtime, store):
    d = repo.create_drama(store, "新剧", "9:16", "realistic")
    assert "data-outline-form" in client.get(f"/drama/{d}/outline").text
    novel = "\n".join(["字" * 100] * 3)                                   # 3 段,够拆 3 集
    r = client.post(f"/llm/start/outline/{d}", data={"novel": novel, "n_episodes": "3"}, follow_redirects=False)
    runtime.wait_llm()
    rid = int(r.headers["location"].rsplit("/", 1)[1])
    row = runs.get(store, rid)
    assert row["state"] == "failed" and "3 轮都没过判卷" in row["error"]      # 假 LLM 没有大纲样例
    assert "data-llm-error" in client.get(f"/llm/run/{rid}").text


def test_llm_unavailable_is_a_503_not_a_silent_fake(client, runtime, sample):
    def down():
        raise LLMUnavailable("找不到 LLM key 文件")
    runtime.backend.llm = down
    r = client.post(f"/llm/start/voice/{sample['lin']}")
    assert r.status_code == 503 and "LLM 不可用" in r.text


def test_bad_params_and_unmet_preconditions_are_400(client, store, sample):
    d = repo.create_drama(store, "新剧", "9:16", "realistic")
    r = client.post(f"/llm/start/outline/{d}", data={"novel": "x", "n_episodes": "三"})
    assert r.status_code == 400 and "要填整数" in r.text
    empty = repo.add_episode(store, sample["drama"], 2, "script")
    r = client.post(f"/llm/start/storyboard/{empty}")
    assert r.status_code == 400 and "还没有剧本" in r.text


def _real_run(store, task, target_id, params, output):
    rid = runs.start(store, task=task, target_type="episode", target_id=target_id, model="m",
                     input_sha=service.prepare(store, task, target_id, params).sha, llm_mode="real", params=params)
    runs.propose(store, rid, output, "j")
    return rid


def test_critique_review_page_asks_for_a_stance_on_every_issue(client, store, sample):
    repo.update(store, "episode", sample["episode"], script="林小雨说：「这块玉佩，是我母亲留给我的。」\n")
    issue = {"quote": "这块玉佩，是我母亲留给我的", "category": "动机不足", "severity": "中", "suggestion": "加一句"}
    rid = _real_run(store, "critique", sample["episode"], {"role": "编剧"}, {"issues": [issue, issue]})
    page = client.get(f"/llm/run/{rid}").text
    assert page.count("data-issue=") == 2 and page.count('value="adopt"') == 2 and 'form="approve-form"' in page
    r = client.post(f"/llm/run/{rid}/approve", data={"issue-0": "adopt"})
    assert r.status_code == 400 and "第 2 条还没表态" in r.text
    r = client.post(f"/llm/run/{rid}/approve", data={"issue-0": "adopt", "issue-x": "reject"})
    assert r.status_code == 400
    r = client.post(f"/llm/run/{rid}/approve", data={"issue-0": "adopt", "issue-1": "reject"}, follow_redirects=False)
    assert r.status_code == 303
    assert runs.get(store, rid)["decision_json"] == '{"adopt": [0], "reject": [1]}'


def test_rewrite_review_page_shows_candidates_against_the_span(client, store, sample):
    span = "林小雨说：「这块玉佩，是我母亲留给我的。」"
    repo.update(store, "episode", sample["episode"], script=f"林小雨低头。\n{span}\n")
    out = {"candidates": [{"text": "林小雨说：「玉佩是我母亲的。谁敢碰？」", "why": "冲突"},
                          {"text": "林小雨攥紧玉佩，一言不发。", "why": "留白"}]}
    rid = _real_run(store, "rewrite", sample["episode"], {"span": span, "intent": "加强冲突", "note": ""}, out)
    page = client.get(f"/llm/run/{rid}").text
    assert page.count("data-candidate=") == 2 and span in page
    r = client.post(f"/llm/run/{rid}/approve", data={"pick": "x"})
    assert r.status_code == 400
    r = client.post(f"/llm/run/{rid}/approve", data={"pick": "1"}, follow_redirects=False)
    assert r.status_code == 303 and "一言不发" in repo.get(store, "episode", sample["episode"])["script"]


def test_critique_model_output_is_html_escaped(client, store, sample):
    repo.update(store, "episode", sample["episode"], script="林小雨说：「这块玉佩，是我母亲留给我的。」\n")
    issue = {"quote": "<b>x</b>", "category": "动机不足", "severity": "中", "suggestion": "加一句"}
    rid = _real_run(store, "critique", sample["episode"], {"role": "编剧"}, {"issues": [issue]})
    page = client.get(f"/llm/run/{rid}").text
    assert "&lt;b&gt;" in page and "<b>x</b>" not in page


def test_approved_critique_shows_recorded_stance_without_radios(client, store, sample):
    repo.update(store, "episode", sample["episode"], script="林小雨说：「这块玉佩，是我母亲留给我的。」\n")
    issue = {"quote": "这块玉佩，是我母亲留给我的", "category": "动机不足", "severity": "中", "suggestion": "加一句"}
    rid = _real_run(store, "critique", sample["episode"], {"role": "编剧"}, {"issues": [issue, issue]})
    assert 'value="adopt"' in client.get(f"/llm/run/{rid}").text
    client.post(f"/llm/run/{rid}/approve", data={"issue-0": "adopt", "issue-1": "reject"})
    page = client.get(f"/llm/run/{rid}").text
    assert 'value="adopt"' not in page and 'type="radio"' not in page
    assert 'data-stance="adopt"' in page and "已采纳" in page
    assert 'data-stance="reject"' in page and "已驳回" in page


def test_approved_rewrite_marks_the_picked_candidate(client, store, sample):
    span = "林小雨说：「这块玉佩，是我母亲留给我的。」"
    repo.update(store, "episode", sample["episode"], script=f"林小雨低头。\n{span}\n")
    out = {"candidates": [{"text": "林小雨说：「玉佩是我母亲的。谁敢碰？」", "why": "冲突"},
                          {"text": "林小雨攥紧玉佩，一言不发。", "why": "留白"}]}
    rid = _real_run(store, "rewrite", sample["episode"], {"span": span, "intent": "加强冲突", "note": ""}, out)
    client.post(f"/llm/run/{rid}/approve", data={"pick": "1"})
    page = client.get(f"/llm/run/{rid}").text
    assert page.count("data-picked") == 1 and "已选用" in page and 'type="radio"' not in page
    assert page.index("data-picked") > page.index('data-candidate="1"')
