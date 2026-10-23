# -*- coding: utf-8 -*-
"""调度层的确定性路径(假 LLM)。不声称覆盖 LLM 契约 —— 那是 test_contract_real.py 的事。"""
import json
from dataclasses import replace

import pytest

from tools.drama import repo
from tools.llm import runs, service
from tools.llm.client import LLMUnavailable
from tools.llm.fake import FakeLLM
from tools.llm.service import LLMBackend

VOICE = {"gender": "female", "age": "young_adult", "timbre": "清亮柔和", "pace": "slow"}


class Recording(FakeLLM):
    """真实模式标记的脚本化客户端:用来测「真产物」的审批路径,不连网。"""


def _real(canned):
    return LLMBackend(Recording(canned), "gpt-5.5", "real")


@pytest.fixture(autouse=True)
def _lin_has_a_look(store, sample):
    # 没有设定、也没有台词的角色,voices.context 会拒绝(没有依据就不让 LLM 编)
    repo.update(store, "character", sample["lin"], appearance="二十出头,长发,素色上衣")


def test_voice_end_to_end_with_approval(store, sample, tmp_path):
    llm = _real({"submit_voice": VOICE})
    rid, reused = service.begin(store, "voice", sample["lin"], {}, llm)
    assert not reused and runs.get(store, rid)["state"] == "running"
    service.finish(store, rid, llm, tmp_path)
    assert runs.get(store, rid)["state"] == "proposed"
    assert repo.get(store, "character", sample["lin"])["voice_desc"] == "清亮女声"     # 批准前不写
    service.approve(store, rid)
    assert repo.get(store, "character", sample["lin"])["voice_desc"] == "青年女声,清亮柔和,语速偏慢"


def test_locked_version_is_reused_without_calling_the_llm(store, sample, tmp_path):
    llm = _real({"submit_voice": VOICE})
    rid, _ = service.begin(store, "voice", sample["lin"], {}, llm)
    service.finish(store, rid, llm, tmp_path)
    service.approve(store, rid)
    calls = llm.client.calls
    again, reused = service.begin(store, "voice", sample["lin"], {}, llm)
    assert (again, reused) == (rid, True) and llm.client.calls == calls


def test_changed_input_cannot_be_approved(store, sample, tmp_path):
    llm = _real({"submit_voice": VOICE})
    rid, _ = service.begin(store, "voice", sample["lin"], {}, llm)
    service.finish(store, rid, llm, tmp_path)
    repo.update(store, "character", sample["lin"], appearance="短发")          # 生成之后改了设定
    with pytest.raises(ValueError, match="输入已经变了"):
        service.approve(store, rid)


def test_fake_output_is_proposed_but_never_approved(store, sample, tmp_path):
    llm = LLMBackend(FakeLLM(), "gpt-5.5", "fake")
    rid, _ = service.begin(store, "voice", sample["lin"], {}, llm)
    service.finish(store, rid, llm, tmp_path)
    assert runs.get(store, rid)["state"] == "proposed"
    with pytest.raises(ValueError, match="假 LLM"):
        service.approve(store, rid)


def test_failures_are_recorded_not_swallowed(store, sample, tmp_path):
    class Down:
        def call_tool(self, **kw):
            raise LLMUnavailable("连不上 LLM 服务")
    llm = LLMBackend(Down(), "gpt-5.5", "real")
    rid, _ = service.begin(store, "voice", sample["lin"], {}, llm)
    service.finish(store, rid, llm, tmp_path)
    row = runs.get(store, rid)
    assert row["state"] == "failed" and "连不上 LLM 服务" in row["error"]


def test_gate_failure_keeps_the_journal(store, sample, tmp_path):
    llm = _real({"submit_voice": {**VOICE, "timbre": "像某明星一样123"}})
    rid, _ = service.begin(store, "voice", sample["lin"], {}, llm)
    service.finish(store, rid, llm, tmp_path, max_rounds=2)
    row = runs.get(store, rid)
    assert row["state"] == "failed" and "2 轮都没过判卷" in row["error"] and row["journal_path"]


def test_preconditions_are_checked_before_any_run_exists(store, sample):
    empty = repo.add_episode(store, sample["drama"], 2, "script")               # 没有剧本
    with pytest.raises(ValueError, match="还没有剧本"):
        service.begin(store, "storyboard", empty, {}, _real({}))
    assert store.conn.execute("SELECT COUNT(*) FROM llm_run").fetchone()[0] == 0
    with pytest.raises(ValueError, match="未知的 LLM 任务"):
        service.begin(store, "poem", 1, {}, _real({}))


def test_voice_without_any_basis_is_refused(store, sample):
    with pytest.raises(ValueError, match="没有依据定声音"):
        service.begin(store, "voice", sample["shen"], {}, _real({}))


def _proposed_voice_run(store, sample, tmp_path):
    llm = _real({"submit_voice": VOICE})
    rid, _ = service.begin(store, "voice", sample["lin"], {}, llm)
    service.finish(store, rid, llm, tmp_path)
    return rid


def test_tasks_without_choices_refuse_a_decision(store, sample, tmp_path):
    """只有评审团 / 局部改写这类要人挑的任务收选择;别的任务给了选择是调用方写错了,当场报错。"""
    rid = _proposed_voice_run(store, sample, tmp_path)
    with pytest.raises(ValueError, match="不需要选择"):
        service.approve(store, rid, decision={"pick": 0})
    assert runs.get(store, rid)["state"] == "proposed"


def test_tasks_with_choices_require_and_record_a_valid_decision(store, sample, tmp_path, monkeypatch):
    calls = []

    class PickingVoice:
        """借 voices 的一切,只多出「要人挑」的两个口子。"""
        def __getattr__(self, name):
            return getattr(service.voices, name)

        @staticmethod
        def decision_problems(args, decision):
            return [] if decision == {"pick": 0} else ["要从候选里挑一个"]

        @staticmethod
        def apply(store, target_id, args, ctx, decision):
            calls.append(decision)

    monkeypatch.setitem(service.TASKS, "voice", replace(service.TASKS["voice"], module=PickingVoice()))
    rid = _proposed_voice_run(store, sample, tmp_path)
    with pytest.raises(ValueError, match="先做选择"):
        service.approve(store, rid)
    with pytest.raises(ValueError, match="要从候选里挑一个"):
        service.approve(store, rid, decision={"pick": 9})
    assert not calls and runs.get(store, rid)["state"] == "proposed"
    service.approve(store, rid, decision={"pick": 0})
    assert calls == [{"pick": 0}]
    assert json.loads(runs.get(store, rid)["decision_json"]) == {"pick": 0}
