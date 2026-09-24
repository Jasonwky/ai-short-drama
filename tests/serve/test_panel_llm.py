# -*- coding: utf-8 -*-
"""控制台里的 LLM 路径(TestClient + 假 / 脚本化 LLM)。不声称覆盖 LLM 契约。"""
import pytest

from tools.drama import repo
from tools.llm import runs
from tools.llm.client import LLMUnavailable
from tools.llm.fake import FakeLLM
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
    r = client.post(f"/llm/start/storyboard/{sample['episode']}")
    assert r.status_code == 400 and "已经有段落" in r.text
