# -*- coding: utf-8 -*-
"""单集大纲变体(spec 第 2b 节):N 个维度上真不一样的重写,人挑一个写回;本集已有剧本时拒绝。"""
import pytest

from tools.drama import repo
from tools.llm import outline_variants as ov

OUTLINE = "沈青禾替嫁冲喜,大婚夜被嫡姐羞辱,她当众一针救活昏迷的王爷,揭穿管事下毒。"


def _ep(store, script=""):
    d = repo.create_drama(store, "替嫁", "9:16")
    e = repo.add_episode(store, d, 1, "novel", script=script)
    repo.update(store, "episode", e, outline=OUTLINE)
    return e


def _v(hook="直接冲突", reversal="真相揭露", cliff="危机未解", text=OUTLINE + "(变体)", typicality=0.5):
    return {"outline": text, "hook_type": hook, "reversal_type": reversal, "cliff_type": cliff, "typicality": typicality}


def test_episode_with_a_script_is_refused(store):
    with pytest.raises(ValueError, match="已有剧本"):
        ov.context(store, _ep(store, script="林小雨说：「……」"), n=3)
    with pytest.raises(ValueError, match="2–4"):
        ov.context(store, _ep(store), n=7)


def test_judge_counts_distinct_and_non_empty(store):
    judge = ov.judge_factory(ov.context(store, _ep(store), n=3))
    good = {"variants": [_v(), _v("强悬念", "身份反转", "身份待揭"), _v("极致反差", "立场反转", "抉择")]}
    assert judge(good) == []
    assert any("要 3 个" in p for p in judge({"variants": good["variants"][:2]}))
    same = {"variants": [_v(), _v(), _v("极致反差", "立场反转", "抉择")]}
    assert any("方案 1 和 2" in p for p in judge(same))
    blank = {"variants": [_v(text=" "), *good["variants"][1:]]}
    assert any("空" in p for p in judge(blank))


def test_pick_writes_the_outline_and_refuses_if_a_script_appeared(store):
    e = _ep(store)
    ctx = ov.context(store, e, n=3)
    out = {"variants": [_v(text="甲"), _v("强悬念", "身份反转", "身份待揭", text="乙"), _v("极致反差", "立场反转", "抉择", text="丙")]}
    assert ov.decision_problems(out, {"pick": 1}) == [] and ov.decision_problems(out, {"pick": True}) != []
    ov.apply(store, e, out, ctx, {"pick": 1})
    assert repo.get(store, "episode", e)["outline"] == "乙"
    repo.update(store, "episode", e, script="有了剧本")
    with pytest.raises(ValueError, match="已有剧本"):
        ov.apply(store, e, out, ctx, {"pick": 0})
