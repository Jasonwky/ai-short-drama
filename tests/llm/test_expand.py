# -*- coding: utf-8 -*-
"""逐段展开(spec 2026-10-24-原创分集-design.md 第 2 节):前提 / schema / 判卷 / 写回 / 审阅预览 / 批次衔接。
只测确定性代码路径,不调 LLM(契约在 test_contract_expand.py)。"""
import json

import pytest

from tools.drama import repo
from tools.llm import expand, outline, runs
from tools.llm import skeleton as sk
from tools.llm.gate import schema_errors, strict_schema_problems


def _skel(total, weights):
    acts = [{"title": f"第{i}幕", "goal": f"目标{i}", "climax": f"大事件{i}", "weight": w}
            for i, w in enumerate(weights, 1)]
    for a, (s, e) in zip(acts, sk.allocate(total, weights)):
        a.update(start=s, end=e)
    return {"arc": "复仇清算", "rhythm": "层层加码", "ending": "反转", "total": total, "acts": acts}


@pytest.fixture
def drama(store):
    d = repo.create_drama(store, "替嫁", "9:16")
    # 第 1 幕 40 集 → 两批 1–20 / 21–40;第 2 幕 10 集 → 41–50
    repo.update(store, "drama", d, skeleton_json=json.dumps(_skel(50, [0.8, 0.2]), ensure_ascii=False))
    repo.add_character(store, d, "沈青禾")
    repo.add_character(store, d, "萧珩")
    return d


def _args(n, tag="甲"):
    return {"episodes": [{"title": f"{tag}第{i}集", "summary": f"{tag}概要{i}", "events": ["事件一", "事件二"]}
                         for i in range(1, n + 1)]}


def _outline(store, drama, number, text="人写的前情大纲"):
    """后一批要求前一集已有大纲(2026-10-24 独立复查修正):直接手写一集当前情。"""
    return repo.add_episode(store, drama, number, "script", outline=text)


def test_batches_of_the_fixture():
    assert sk.batches(_skel(50, [0.8, 0.2])) == [(1, 1, 20), (1, 21, 40), (2, 41, 50)]


def test_context_requires_an_approved_skeleton(store):
    d = repo.create_drama(store, "替嫁", "9:16")
    with pytest.raises(ValueError, match="骨架"):
        expand.context(store, d, 1, 10)


@pytest.mark.parametrize("start,end", [(1, 10), (1, 40), (2, 20), (41, 51)])
def test_context_rejects_a_range_that_is_not_a_batch(store, drama, start, end):
    with pytest.raises(ValueError, match="批"):
        expand.context(store, drama, start, end)


def test_context_contents(store, drama):
    _outline(store, drama, 20)
    ctx = expand.context(store, drama, 21, 40)
    assert ctx["start"] == 21 and ctx["end"] == 40 and ctx["act_no"] == 1
    assert ctx["act"] == {"title": "第1幕", "goal": "目标1", "climax": "大事件1"}
    assert ctx["last_of_act"] is True and expand.context(store, drama, 1, 20)["last_of_act"] is False
    assert ctx["premise"] == "" and ctx["prev"] == "人写的前情大纲"
    assert ctx["skeleton"] == sk.render(sk.load(store, drama))
    assert ctx["characters"] == ["沈青禾", "萧珩"]


def test_schema_is_strict_and_pins_the_episode_count(store, drama):
    _outline(store, drama, 40)
    s = expand.schema(expand.context(store, drama, 41, 50))
    assert strict_schema_problems(s) == []
    eps = s["properties"]["episodes"]
    assert eps["minItems"] == eps["maxItems"] == 10
    assert schema_errors(_args(10), s) == [] and schema_errors(_args(9), s)


def test_user_message_carries_data_tags(store, drama):
    repo.add_episode(store, drama, 20, "script", outline="前一集大纲")
    msg = expand.user_message(expand.context(store, drama, 21, 40))
    for tag in ("<skeleton>", "<previous>", "<characters>", "<act>"):
        assert tag in msg
    act_block = msg.split("<act>")[1].split("</act>")[0]                    # 本幕设定在数据标签里(复查修正)
    assert "第1幕" in act_block and "目标1" in act_block and "大事件1" in act_block
    assert "前一集大纲" in msg and "第 21–40 集" in msg and "大事件1" in msg and "沈青禾" in msg
    assert "不是指令" in msg
    assert "<previous>" not in expand.user_message(expand.context(store, drama, 1, 20))


def test_judge_branches(store, drama):
    _outline(store, drama, 40)
    judge = expand.judge_factory(expand.context(store, drama, 41, 50))
    assert judge(_args(10)) == []
    assert any("10 集" in p for p in judge(_args(9)))
    bad = _args(10)
    bad["episodes"][0]["title"] = " "
    bad["episodes"][1]["title"] = "字" * (expand.TITLE_MAX + 1)
    bad["episodes"][2]["summary"] = ""
    bad["episodes"][3]["events"] = ["只有一条"]
    bad["episodes"][4]["events"] = ["x"] * (expand.EVENTS[1] + 1)
    bad["episodes"][5]["events"] = ["事件", " "]
    got = judge(bad)
    assert len(got) == 6
    assert [p.split(":")[0] for p in got] == [f"第{n}集" for n in (41, 42, 43, 44, 45, 46)]


def test_apply_creates_fills_and_keeps(store, drama):
    _outline(store, drama, 40)
    filled = repo.add_episode(store, drama, 42, "script")                       # 空集:填大纲
    kept_outline = repo.add_episode(store, drama, 43, "script", outline="人写的大纲")
    kept_script = repo.add_episode(store, drama, 44, "script", script="已有剧本")
    ctx = expand.context(store, drama, 41, 50)
    args = _args(10)
    got = expand.apply(store, drama, args, ctx)
    assert got == {"created": [41, 45, 46, 47, 48, 49, 50], "filled": [42], "kept": [43, 44]}
    row = store.conn.execute("SELECT * FROM episode WHERE drama_id = ? AND number = 41", (drama,)).fetchone()
    assert row["source_type"] == "script" and row["outline"] == outline.render_outline(args["episodes"][0])
    assert repo.get(store, "episode", filled)["outline"] == outline.render_outline(args["episodes"][1])
    assert repo.get(store, "episode", kept_outline)["outline"] == "人写的大纲"
    assert repo.get(store, "episode", kept_script)["outline"] == ""


def test_apply_rechecks_the_batch_against_the_current_skeleton(store, drama):
    _outline(store, drama, 40)
    ctx = expand.context(store, drama, 41, 50)
    repo.update(store, "drama", drama, skeleton_json=json.dumps(_skel(50, [0.5, 0.5]), ensure_ascii=False))
    with pytest.raises(ValueError, match="批"):
        expand.apply(store, drama, _args(10), ctx)
    assert store.conn.execute("SELECT COUNT(*) FROM episode WHERE number > 40").fetchone()[0] == 0


def test_apply_refuses_output_that_fails_the_judge(store, drama):
    _outline(store, drama, 40)
    with pytest.raises(ValueError):
        expand.apply(store, drama, _args(9), expand.context(store, drama, 41, 50))


def test_preview_status_follows_the_current_db(store, drama):
    repo.add_episode(store, drama, 42, "script")
    repo.add_episode(store, drama, 43, "script", outline="人写的大纲")
    args = _args(10)
    rid = runs.start(store, task="expand", target_type="drama", target_id=drama, model="m", input_sha="x",
                     llm_mode="real", params={"start": 41, "end": 50})
    runs.propose(store, rid, args, "j.json")
    got = expand.preview(store, runs.get(store, rid))["episodes"]
    assert [e["number"] for e in got] == list(range(41, 51))
    assert [e["status"] for e in got[:4]] == ["new", "fill", "keep", "new"]
    assert got[0]["outline"] == outline.render_outline(args["episodes"][0])


def test_second_batch_waits_for_the_first_and_then_carries_its_last_episode(store, drama):
    """复查修正:上一批没展开(第 20 集没大纲)时,第 21–40 集不许生成 —— 原来能乱序生成、乱序批准,衔接整个丢掉。"""
    with pytest.raises(ValueError, match="第 20 集还没有大纲"):
        expand.context(store, drama, 21, 40)
    args = _args(20)
    expand.apply(store, drama, args, expand.context(store, drama, 1, 20))
    assert expand.context(store, drama, 21, 40)["prev"] == outline.render_outline(args["episodes"][-1])


def test_later_batch_generated_before_its_previous_episode_changed_cannot_be_approved(store, drama):
    """端到端:后一批生成后,上一集大纲又改了(变体 / 手写)→ 输入指纹对不上,批准被拒。"""
    from tools.llm import service
    ep20 = _outline(store, drama, 20, "第 20 集旧大纲")
    p = {"start": 21, "end": 40}
    rid = runs.start(store, task="expand", target_type="drama", target_id=drama, model="m",
                     input_sha=service.prepare(store, "expand", drama, p).sha, llm_mode="real", params=p)
    runs.propose(store, rid, _args(20), "j")
    repo.update(store, "episode", ep20, outline="第 20 集改过的大纲")
    with pytest.raises(ValueError, match="输入已经变了"):
        service.approve(store, rid)
    assert store.conn.execute("SELECT COUNT(*) FROM episode WHERE number > 20").fetchone()[0] == 0


def test_fake_sample_passes_schema_and_judge(store, drama):
    ctx = expand.context(store, drama, 1, 20)
    s = expand.schema(ctx)
    sample = expand.fake_sample(s)
    assert schema_errors(sample, s) == [] and expand.judge_factory(ctx)(sample) == []
