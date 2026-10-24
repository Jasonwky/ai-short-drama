# -*- coding: utf-8 -*-
"""逐段展开的**真 LLM 契约测试**(花钱,默认 `pytest -q` 不跑;`python -X utf8 -m pytest -m real_llm -q`)。

手造一个 3 幕 30 集的骨架,展开第 1 幕(第 1–10 集):真模型要在轮次上限内交齐正好 10 集、过判卷。
"""
import pytest

from tests.llm.test_contract_real import _gate, client  # noqa: F401
from tools.llm import expand
from tools.llm import skeleton as sk

pytestmark = pytest.mark.real_llm


def test_expand_first_act_of_a_30_episode_skeleton(client):  # noqa: F811
    acts = [{"title": "替嫁入府", "goal": "沈青禾在王府站稳脚跟", "climax": "当众揭穿管事下毒", "weight": 1 / 3},
            {"title": "暗流涌动", "goal": "查清生母当年的死因", "climax": "找到太医的旧账本", "weight": 1 / 3},
            {"title": "清算", "goal": "让幕后主使伏法", "climax": "金殿上真相大白", "weight": 1 / 3}]
    for a, (s, e) in zip(acts, sk.allocate(30, [a["weight"] for a in acts])):
        a.update(start=s, end=e)
    skel = {"arc": "复仇清算", "rhythm": "层层加码", "ending": "圆满", "total": 30, "acts": acts}
    assert sk.batches(skel)[0] == (1, 1, 10)
    ctx = {"premise": "", "skeleton": sk.render(skel), "act_no": 1,
           "act": {k: acts[0][k] for k in ("title", "goal", "climax")}, "last_of_act": True,
           "start": 1, "end": 10, "prev": "", "characters": ["沈青禾", "萧珩", "李福"]}
    res = _gate(client, "expand", expand, ctx)
    assert len(res.args["episodes"]) == 10
    assert expand.judge_factory(ctx)(res.args) == []
