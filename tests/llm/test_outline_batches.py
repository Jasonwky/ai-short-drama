# -*- coding: utf-8 -*-
"""长小说分批:按段落切批、按字数分配集数、逐批接续(集号、前情),批次顺序由输入指纹守住。"""
import pytest

from tests.llm.test_script_outline import NOVEL, good_outline
from tools.drama import repo
from tools.llm import outline, runs, service
from tools.llm.fake import FakeLLM
from tools.llm.service import LLMBackend


def test_batches_cut_on_paragraph_boundaries(monkeypatch):
    monkeypatch.setattr(outline, "MAX_NOVEL_CHARS", 25)
    b = outline.batches("一二三四五六七八九十\n十一十二十三十四十五\n甲乙丙丁戊己庚辛壬癸\n子丑寅卯")
    assert [len(x) for x in b] == [2, 2]                          # 两段一批,不在段中间切
    assert b[0][0] == "一二三四五六七八九十" and b[1][-1] == "子丑寅卯"
    with pytest.raises(ValueError, match="有一段就 30 字"):
        outline.batches("字" * 30)


def test_batches_also_respect_the_paragraph_cap(monkeypatch):
    monkeypatch.setattr(outline, "MAX_PARAGRAPHS", 3)
    assert [len(x) for x in outline.batches("\n".join("段" * 5 for _ in range(7)))] == [3, 3, 1]


def test_split_episodes_is_proportional_with_at_least_one_each():
    assert outline.split_episodes(10, [60000, 30000, 10000]) == [6, 3, 1]
    assert outline.split_episodes(3, [100, 1, 1]) == [1, 1, 1]
    assert sum(outline.split_episodes(7, [5, 5, 5])) == 7
    with pytest.raises(ValueError, match="至少要 3 集"):
        outline.split_episodes(2, [1, 1, 1])


def test_first_batch_prompt_is_unchanged_and_later_batches_carry_the_previous_outline(store):
    d = repo.create_drama(store, "玉佩", "9:16", "realistic")
    first = outline.context(store, d, NOVEL, 3)
    assert first["start_number"] == 1 and "<previous>" not in outline.user_message(first)   # 旧锁定版仍命中
    outline.apply(store, d, good_outline(), first)
    second = outline.context(store, d, NOVEL, 3)
    assert second["start_number"] == 4 and second["previous"].startswith(good_outline()["episodes"][2]["title"])
    assert outline.user_message(second).startswith("<previous>\n")
    ids = outline.apply(store, d, good_outline(), second)
    assert [repo.get(store, "episode", i)["number"] for i in ids] == [4, 5, 6]


def test_a_later_batch_started_too_early_cannot_be_approved(store, tmp_path):
    d = repo.create_drama(store, "玉佩", "9:16", "realistic")
    llm = LLMBackend(FakeLLM({outline.TOOL: good_outline()}), "m", "real")   # 确定性替身,不声称覆盖 LLM 契约
    b1, _ = service.begin(store, "outline", d, {"novel": NOVEL, "n_episodes": 3}, llm)
    b2, _ = service.begin(store, "outline", d, {"novel": NOVEL, "n_episodes": 3}, llm)   # 下一批(同文仅为省样例)
    for r in (b1, b2):
        service.finish(store, r, llm, tmp_path)
    service.approve(store, b1)
    with pytest.raises(ValueError, match="输入已经变了"):                        # b2 是在 b1 批准前生成的
        service.approve(store, b2)
    assert runs.get(store, b2)["state"] == "proposed"
