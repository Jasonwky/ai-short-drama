# -*- coding: utf-8 -*-
"""剧本生成与分集大纲的确定性部分:格式、篇幅、原句核对、写入。LLM 行为由 test_contract_real.py 真跑。"""
import copy
from pathlib import Path

import pytest

from tools.drama import repo, rules
from tools.llm import outline, script_writer as sw
from tools.llm.gate import schema_errors, strict_schema_problems

NOVEL = (Path(__file__).parent / "golden" / "jade_novel.txt").read_text(encoding="utf-8")


def _script_args(n_lines=40):
    lines = []
    for i in range(n_lines):
        if i % 3 == 0:
            lines.append({"kind": "action", "speaker": None, "text": "林小雨低头看着手里的玉佩,很久没有说话。"})
        elif i % 3 == 1:
            lines.append({"kind": "dialogue", "speaker": "林小雨", "text": "这块玉佩,是我母亲留给我的。"})
        else:
            lines.append({"kind": "narration", "speaker": None, "text": "三年过去,老宅一直空着。"})
    return {"scenes": [{"place": "老宅庭院", "time": "黄昏", "lines": lines}]}


def _minutes_for(args):
    return rules.count_chars(sw.render(args)) / rules.CHARS_PER_MINUTE


# ── 剧本 ──────────────────────────────────────────────

def test_script_schema_is_strict():
    assert strict_schema_problems(sw.SCHEMA) == [] and schema_errors(_script_args(), sw.SCHEMA) == []


def test_render_uses_the_canonical_format_that_rules_can_parse():
    text = sw.render(_script_args(3))
    assert text.startswith("【老宅庭院 · 黄昏】\n") and "旁白：三年过去" in text
    assert rules.dialogue_lines(text) == [("林小雨", "这块玉佩,是我母亲留给我的。")]


def test_length_is_judged_against_the_target():
    args = _script_args(40)
    ctx = {"minutes": 1}
    minutes = _minutes_for(args)
    probs = sw.judge_factory(ctx)(args)
    assert (0.8 <= minutes <= 1.2) == (not any("篇幅" in p for p in probs))
    assert any("篇幅" in p for p in sw.judge_factory({"minutes": 3})(args))


def test_length_feedback_says_how_many_characters_to_add():
    # 验收时真 LLM 3 轮没收敛:反馈只说「约 45 秒,目标 60 秒」,模型不知道该补多少,越改越短
    args = _script_args(40)
    n = rules.count_chars(sw.render(args))
    (msg,) = [p for p in sw.judge_factory({"minutes": 3})(args) if "篇幅" in p]
    assert "1200–1800 字" in msg and f"现在 {n} 字" in msg and f"再加约 {1350 - n} 字" in msg and "不要超过 1800" in msg


def test_length_feedback_aims_inside_the_range_not_at_the_middle():
    """2026-10-09 验收:按中点给数,模型改过头约一倍(叫加 120 实际加 231:380 → 611 → 397),3 轮在边界两侧来回摆。
    目标改成区间内靠近当前一侧的四分之一处:过头一倍也落在区间中部。"""
    short = _script_args(40)                                         # 623 字;3 分钟要 1200–1800
    (msg,) = [p for p in sw.judge_factory({"minutes": 3})(short) if "篇幅" in p]
    assert "再加约 727 字" in msg                                     # 目标 1350,不是 1500
    long = _script_args(200)                                         # 3077 字;1 分钟要 400–600
    (msg,) = [p for p in sw.judge_factory({"minutes": 1})(long) if "篇幅" in p]
    assert "删掉约 2527 字" in msg and "不要少于 400" in msg          # 目标 550


def test_prompt_says_how_characters_are_counted():
    ctx = {"kind": "synopsis", "source": "x" * 30, "outline": "", "minutes": 2, "characters": []}
    assert "800–1200 字" in sw.user_message(ctx) and "不含标点" in sw.user_message(ctx)


def test_format_violations_go_back():
    args = _script_args(40)
    args["scenes"][0]["lines"][1]["speaker"] = None
    args["scenes"][0]["lines"][0]["speaker"] = "林小雨"
    args["scenes"][0]["lines"][2]["text"] = "「旁白」"
    probs = sw.judge_factory({"minutes": 1})(args)
    assert any("台词要有说话人" in p for p in probs) and any("speaker 必须是 null" in p for p in probs)
    assert any("不能含【】「」·" in p for p in probs)


def test_synopsis_context_and_apply(store):
    d = repo.create_drama(store, "玉佩", "9:16", "realistic")
    ep = repo.add_episode(store, d, 1, "synopsis")
    with pytest.raises(ValueError, match="梗概太短"):
        sw.context_synopsis(store, ep, "太短", 1)
    ctx = sw.context_synopsis(store, ep, "林小雨回到老宅,发现母亲留下的玉佩背面刻着邻居沈家的姓。" * 2, 1)
    sw.apply(store, ep, _script_args(40), ctx)
    row = repo.get(store, "episode", ep)
    assert row["script"].startswith("【老宅庭院") and row["source_text"] == ctx["source"]


def test_adapt_needs_an_outline_first(store):
    d = repo.create_drama(store, "玉佩", "9:16", "realistic")
    ep = repo.add_episode(store, d, 1, "script")
    with pytest.raises(ValueError, match="先在「大纲」面板生成"):
        sw.context_adapt(store, ep, 2)


# ── 大纲 ──────────────────────────────────────────────

CTX = {"paragraphs": outline.paragraphs(NOVEL), "n": 3}      # 黄金样本共 15 段


def good_outline():
    return {"episodes": [
        {"title": "红布包", "summary": "林小雨回老宅,找到母亲留下的玉佩。",
         "events": ["回到老宅", "找到红布包", "读到字条"], "start_para": 1, "end_para": 3},
        {"title": "一个沈字", "summary": "沈墨常来,林小雨发现玉佩上的沈字。",
         "events": ["沈墨来访", "黄昏对话", "发现沈字", "书房对质"], "start_para": 4, "end_para": 9},
        {"title": "等他来取", "summary": "老管家道出三十年前的往事。",
         "events": ["沈墨失踪", "老管家讲往事", "桂花树下", "院门不关"], "start_para": 10, "end_para": 15},
    ]}


def test_outline_schema_is_strict():
    assert strict_schema_problems(outline.SCHEMA) == [] and schema_errors(good_outline(), outline.SCHEMA) == []


def test_input_numbers_the_paragraphs():
    msg = outline.user_message(CTX)
    assert "原文共 15 段" in msg and "〔1〕林小雨回到老宅的那天" in msg and "〔15〕那天晚上" in msg


def test_good_outline_passes():
    assert outline.judge_factory(CTX)(good_outline()) == []


def test_gaps_overlaps_and_uncovered_tails_are_caught():
    gap = copy.deepcopy(good_outline())
    gap["episodes"][1]["start_para"] = 5
    assert any("应从第 4 段开始" in p for p in outline.judge_factory(CTX)(gap))
    short = copy.deepcopy(good_outline())
    short["episodes"][2]["end_para"] = 14
    assert any("最后一集要到第 15 段结束" in p for p in outline.judge_factory(CTX)(short))
    bad = copy.deepcopy(good_outline())
    bad["episodes"][0]["end_para"] = 99
    assert any("段号 1–99 不合法" in p for p in outline.judge_factory(CTX)(bad))


def test_episode_count_and_event_count():
    probs = outline.judge_factory({**CTX, "n": 4})(good_outline())
    assert any("要 4 集,给了 3 集" in p for p in probs)
    few = copy.deepcopy(good_outline())
    few["episodes"][0]["events"] = ["只有一条"]
    assert any("events 要 2–8 条" in p for p in outline.judge_factory(CTX)(few))


def test_outline_context_limits(store):
    d = repo.create_drama(store, "玉佩", "9:16", "realistic")
    with pytest.raises(ValueError, match="太短"):
        outline.context(store, d, "短", 3)
    with pytest.raises(ValueError, match="请分批"):
        outline.context(store, d, "字" * (outline.MAX_NOVEL_CHARS + 1), 3)
    with pytest.raises(ValueError, match="拆不出 20 集"):
        outline.context(store, d, NOVEL, 20)


def test_apply_creates_episodes_with_outline_and_source_paragraphs(store):
    d = repo.create_drama(store, "玉佩", "9:16", "realistic")
    ctx = outline.context(store, d, NOVEL, 3)
    ids = outline.apply(store, d, good_outline(), ctx)
    eps = [repo.get(store, "episode", i) for i in ids]
    assert [e["number"] for e in eps] == [1, 2, 3] and all(e["source_type"] == "novel" for e in eps)
    assert eps[0]["outline"].startswith("红布包\n") and "- 找到红布包" in eps[0]["outline"]
    assert eps[1]["source_text"].startswith("第二天一早,隔壁的沈墨来敲门。")
    assert eps[2]["source_text"].endswith("香气从墙这边飘到墙那边。")
    assert eps[0]["source_text"].count("\n") == 2                    # 3 段原文,分段保留
    with pytest.raises(ValueError, match="这一批要接在后面,请重跑"):     # 同一批批两次 → 集号已被占
        outline.apply(store, d, good_outline(), ctx)


def test_bed_in_a_hall_is_sent_back():
    """实战(题甲第 1 集):剧本把「走向床边昏睡的王爷」写在【靖王府正堂】,出图就成了正堂正中摆床。"""
    args = _script_args(40)
    args["scenes"][0]["place"] = "靖王府正堂"
    args["scenes"][0]["lines"][0]["text"] = "沈青禾径直走向床边昏睡的王爷。"
    (msg,) = [p for p in sw.judge_factory({"minutes": 1})(args) if "床" in p]
    assert "靖王府正堂" in msg and "卧房" in msg
    for place in ("靖王寝殿", "王府卧房", "老宅庭院"):        # 卧房 / 寝殿里有床天经地义;不是厅堂的不管
        args["scenes"][0]["place"] = place
        assert not [p for p in sw.judge_factory({"minutes": 1})(args) if "床" in p]


_PREMISE = {"title": "替嫁王妃", "logline": "冲喜新娘一针救活王爷", "genre": "古装权谋", "payoff": "打脸",
            "conflict": "身份错位", "hook_type": "直接冲突",
            "protagonist": {"name": "顾长歌", "tag": "庶女神医", "contrast": "表面柔弱"}, "antagonist": "嫡姐",
            "hook": "大婚夜她跪在堂中", "typicality": 0.4}


def test_outline_prompt_carries_the_premise_when_there_is_one(store):
    import json
    from tools.llm import premise
    d = repo.create_drama(store, "替嫁", "9:16")
    novel = "\n".join(f"第{i}段。" + "字" * 30 for i in range(1, 12))
    before = outline.user_message(outline.context(store, d, novel=novel, n_episodes=2))
    assert "<premise>" not in before                      # 没做过立项:提示词不变(锁定版照旧可复用)
    repo.update(store, "drama", d, premise_json=json.dumps(_PREMISE, ensure_ascii=False))
    after = outline.user_message(outline.context(store, d, novel=novel, n_episodes=2))
    assert "<premise>" in after and premise.render(_PREMISE) in after


def test_script_prompt_carries_the_premise_when_there_is_one(store):
    import json
    from tools.llm import premise
    d = repo.create_drama(store, "替嫁", "9:16")
    ep = repo.add_episode(store, d, 1, "synopsis")
    syn = "林小雨回到老宅,发现母亲留下的玉佩背面刻着邻居沈家的姓。" * 2
    before = sw.user_message(sw.context_synopsis(store, ep, syn, 1))
    assert "<premise>" not in before
    repo.update(store, "drama", d, premise_json=json.dumps(_PREMISE, ensure_ascii=False))
    after = sw.user_message(sw.context_synopsis(store, ep, syn, 1))
    assert "<premise>" in after and premise.render(_PREMISE) in after
    ctx = sw.context_synopsis(store, ep, syn, 1)
    ctx.pop("premise")                                     # 手搭的 ctx(真 LLM 契约测试)没有这一项也要能用
    assert "<premise>" not in sw.user_message(ctx)
