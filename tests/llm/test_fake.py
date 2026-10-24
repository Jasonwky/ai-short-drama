# -*- coding: utf-8 -*-
"""假 LLM 的样例只给自测用,但要真能过判卷 —— 不然自测走不到要演示的那一步。"""
from tools.llm import script_writer as sw
from tools.llm.fake import script_sample


def test_script_sample_passes_the_one_minute_judge():
    ctx = {"kind": "synopsis", "minutes": 1, "outline": "", "characters": ["林小雨"], "source": "x", "premise": ""}
    assert sw.judge_factory(ctx)(script_sample(sw.SCHEMA)) == []
