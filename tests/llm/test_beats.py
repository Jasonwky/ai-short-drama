# -*- coding: utf-8 -*-
"""节拍标注(spec 第 2a 节):逐集闭集字段 + 逐字引用自该集大纲;批准写回 episode.beats_json。"""
import json

import pytest

from tools.drama import repo
from tools.llm import beats as bt

OUT1 = "沈青禾替嫁冲喜,大婚夜被嫡姐羞辱,她一针救活王爷。"
OUT2 = "王爷醒来,李福跪地求饶,沈玉瑶被杖毙,王爷追问她的身份。"


def _drama(store):
    d = repo.create_drama(store, "替嫁", "9:16")
    e1 = repo.add_episode(store, d, 1, "novel")
    e2 = repo.add_episode(store, d, 2, "novel")
    repo.update(store, "episode", e1, outline=OUT1)
    repo.update(store, "episode", e2, outline=OUT2)
    return d, e1, e2


def _ep(n, quote, **kw):
    return {"number": n, "hook": "有", "reversal": "无", "emotion": "平", "paywall": "否", "quote": quote, **kw}


def test_context_needs_outlines(store):
    d = repo.create_drama(store, "空", "9:16")
    repo.add_episode(store, d, 1, "script")
    with pytest.raises(ValueError, match="大纲"):
        bt.context(store, d)


def test_judge_needs_every_episode_once_and_verbatim_quotes(store):
    d, *_ = _drama(store)
    judge = bt.judge_factory(bt.context(store, d))
    good = {"episodes": [_ep(1, "她一针救活王爷"), _ep(2, "王爷追问她的身份")]}
    assert judge(good) == []
    assert any("第 2 集" in p for p in judge({"episodes": good["episodes"][:1]}))
    assert any("重复" in p for p in judge({"episodes": good["episodes"] + [_ep(1, "她一针救活王爷")]}))
    bad = {"episodes": [_ep(1, "她用毒药毒死了王爷"), _ep(2, "王爷追问她的身份")]}
    assert any("第 1 集" in p and "找不到" in p for p in judge(bad))


def test_apply_writes_each_episode(store):
    d, e1, e2 = _drama(store)
    out = {"episodes": [_ep(1, "她一针救活王爷", reversal="大"), _ep(2, "王爷追问她的身份", paywall="是")]}
    bt.apply(store, d, out, bt.context(store, d))
    assert json.loads(repo.get(store, "episode", e1)["beats_json"])["reversal"] == "大"
    assert json.loads(repo.get(store, "episode", e2)["beats_json"])["paywall"] == "是"
    assert "number" not in json.loads(repo.get(store, "episode", e1)["beats_json"])
