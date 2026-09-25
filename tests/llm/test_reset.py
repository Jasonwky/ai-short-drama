# -*- coding: utf-8 -*-
"""已有段落的集重拆分镜 / 重写剧本:批准必须带人确认过的 confirm_reset,删旧段落、历史保留。"""
import pytest

from tests.llm.test_storyboard import _episode, good_args
from tools.drama import repo
from tools.drama.tasks import create_task, tasks_for
from tools.llm import runs, service, storyboard
from tools.llm.fake import FakeLLM
from tools.llm.service import LLMBackend


def _proposed_resplit(store, tmp_path):
    ep = _episode(store)
    old = repo.add_segment(store, ep, 1, description="旧段落", duration_sec=8.0, video_path="videos/old.mp4")
    create_task(store, kind="video", target_type="segment", target_id=old, model="m", params={}, seed=1, prompt="p",
                refs=[])
    llm = LLMBackend(FakeLLM({storyboard.TOOL: good_args()}), "m", "real")      # 确定性替身,不声称覆盖 LLM 契约
    rid, _ = service.begin(store, "storyboard", ep, {}, llm)                     # 已有段落也能开跑
    service.finish(store, rid, llm, tmp_path)
    assert runs.get(store, rid)["state"] == "proposed"
    return ep, old, rid


def test_resplit_needs_confirm_then_replaces_segments_and_keeps_history(store, tmp_path):
    ep, old, rid = _proposed_resplit(store, tmp_path)
    n, imp = service.reset_cost(store, rid)
    assert n == 1 and imp.videos == 1
    with pytest.raises(ValueError, match="删除本集已有的 1 个段落.*需要人确认代价"):
        service.approve(store, rid)
    assert [r[0] for r in store.conn.execute("SELECT id FROM segment WHERE episode_id = ?", (ep,))] == [old]

    new = service.approve(store, rid, confirm_reset=True)
    assert len(new) == 4 and old not in new and min(new) > old                  # 编号不复用
    assert [r["number"] for r in store.conn.execute("SELECT number FROM segment WHERE episode_id = ? ORDER BY number",
                                                    (ep,))] == [1, 2, 3, 4]
    assert len(tasks_for(store, "segment", old)) == 1                            # 旧段落的生成历史还在
    assert runs.get(store, rid)["state"] == "approved"


def test_no_segments_means_no_reset_needed(store, tmp_path):
    ep = _episode(store)
    llm = LLMBackend(FakeLLM({storyboard.TOOL: good_args()}), "m", "real")
    rid, _ = service.begin(store, "storyboard", ep, {}, llm)
    service.finish(store, rid, llm, tmp_path)
    assert service.reset_cost(store, rid) is None
    assert len(service.approve(store, rid)) == 4


def test_apply_itself_never_deletes(store, tmp_path):
    ep = _episode(store)
    repo.add_segment(store, ep, 1)
    with pytest.raises(ValueError, match="已经有段落"):
        storyboard.apply(store, ep, good_args(), storyboard.context(store, ep))
