# -*- coding: utf-8 -*-
import pytest

from tools.drama import repo
from tools.llm import runs
from tools.llm.client import LLMUnavailable
from tools.serve.fakes import FakeTunnel, fake_backend
from tools.serve.runtime import Backend, Runtime, real_backend


def test_llm_job_runs_in_the_background_and_ends_proposed(store, sample, tmp_path):
    repo.update(store, "character", sample["lin"], appearance="二十出头,长发")
    backend, _ = fake_backend()
    rt = Runtime(store, tmp_path, backend)
    rid, reused = rt.start_llm("voice", sample["lin"], {})
    rt.wait_llm()
    row = runs.get(store, rid)
    assert not reused and row["state"] == "proposed" and row["llm_mode"] == "fake"
    assert (tmp_path / "journal" / "llm").is_dir()                  # LLM 记录落在工程的 journal/ 下


def test_no_llm_configured_is_loud(store, sample, tmp_path):
    rt = Runtime(store, tmp_path, Backend("测试", lambda h: None, FakeTunnel(), lambda h: None))
    with pytest.raises(LLMUnavailable, match="没有配置 LLM"):
        rt.start_llm("voice", sample["lin"], {})


def test_real_backend_looks_for_claude_only_when_asked(monkeypatch):
    """LLM 走本机 Claude Code 命令行(2026-10-23 起):建后端时不找,点按钮时才找,找不到就报错(不静默切 Fake)。"""
    import tools.llm.claude_cli as cc
    monkeypatch.setattr(cc.shutil, "which", lambda name: None)
    b = real_backend()
    with pytest.raises(LLMUnavailable, match="找不到本机 Claude Code"):
        b.llm()


def test_unexpected_errors_become_failed_runs_not_silence(store, sample, tmp_path, monkeypatch):
    from tools.llm import service
    repo.update(store, "character", sample["lin"], appearance="二十出头,长发")
    backend, _ = fake_backend()
    rt = Runtime(store, tmp_path, backend)
    monkeypatch.setattr(service, "finish", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("意外")))
    rid, _ = rt.start_llm("voice", sample["lin"], {})
    rt.wait_llm()
    row = runs.get(store, rid)
    assert row["state"] == "failed" and "RuntimeError: 意外" in row["error"]
