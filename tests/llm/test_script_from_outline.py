# -*- coding: utf-8 -*-
"""大纲 → 剧本(spec 2026-10-24-逐集批量出剧本分镜-design.md 第 3 节)。"""
import pytest

from tools.drama import repo
from tools.llm import script_writer as sw, service


def _drama(store):
    d = repo.create_drama(store, "玉佩", "9:16")
    repo.add_character(store, d, "林小雨")
    eps = [repo.add_episode(store, d, n, "script", outline=f"第{n}集大纲:林小雨在老宅里找线索,事件{n}。")
           for n in (1, 2, 3)]
    return d, eps


def test_context_carries_the_previous_and_next_outlines(store):
    _, (e1, e2, e3) = _drama(store)
    ctx = sw.context_outline(store, e2, 1)
    assert ctx["kind"] == "outline" and ctx["prev"].startswith("第1集") and ctx["next"].startswith("第3集")
    msg = sw.user_message(ctx)
    assert "<previous>\n第1集" in msg and "<next>\n第3集" in msg and "本集大纲:\n第2集" in msg
    first = sw.user_message(sw.context_outline(store, e1, 1))
    assert "<previous>" not in first and "<next>" in first


def test_outline_is_required_and_novel_episodes_use_adapt(store):
    d, _ = _drama(store)
    blank = repo.add_episode(store, d, 4, "script")
    with pytest.raises(ValueError, match="还没有大纲"):
        sw.context_outline(store, blank, 1)
    novel = repo.add_episode(store, d, 5, "novel", outline="大纲", source_text="原文")
    with pytest.raises(ValueError, match="按大纲改写原文"):
        sw.context_outline(store, novel, 1)


def test_other_entries_keep_their_prompts_byte_for_byte(store):
    """另两个入口的提示词不变:锁定版照样命中。"""
    ctx = {"kind": "synopsis", "source": "梗概", "outline": "", "minutes": 1, "characters": ["林小雨"], "premise": ""}
    assert sw.user_message(ctx).endswith("<source>\n梗概\n</source>\n请根据这段梗概写本集剧本。")
    assert "script_outline" in service.TASKS and service.TASKS["script_outline"].resets_segments
