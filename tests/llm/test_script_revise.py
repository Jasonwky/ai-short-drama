# -*- coding: utf-8 -*-
"""剧本修改(spec 2026-10-24-剧本分镜修改回路-design.md 第 1 节)。FakeLLM 只测确定性路径,不声称覆盖 LLM 契约。"""
from pathlib import Path

import pytest

from tools.drama import repo
from tools.llm import runs, script_revise as sr, service
from tools.llm.fake import FakeLLM
from tools.llm.gate import Fatal
from tools.llm.service import LLMBackend

SCRIPT = ("【老宅庭院 · 黄昏】\n林小雨捧着玉佩低头凝视。\n林小雨说：「这块玉佩，是我母亲留给我的。」\n"
          "沈墨提灯走进院门。\n沈墨说：「天快黑了，你还不进屋？」\n\n【老宅书房 · 夜】\n林小雨捧着玉佩低头凝视。\n")
LINE = "林小雨说：「这块玉佩，是我母亲留给我的。」"


def _ep(store, script=SCRIPT):
    d = repo.create_drama(store, "玉佩", "9:16")
    repo.add_character(store, d, "林小雨")
    repo.add_character(store, d, "沈墨")
    return repo.add_episode(store, d, 1, "script", script=script)


def _note(text="台词太平", target="", must=False):
    return {"text": text, "target": target, "must": must}


def _e(old, new, why="更有冲突"):
    return {"old": old, "new": new, "why": why}


def _args(*responses):
    return {"responses": list(responses)}


def _r(note, *edits, verdict="改", reason="用意"):
    return {"note": note, "verdict": verdict, "reason": reason, "edits": list(edits)}


def test_locate_expands_to_whole_lines_and_refuses_ambiguity():
    i = SCRIPT.index(LINE)
    assert sr.locate(SCRIPT, "是我母亲留给我的") == (i, i + len(LINE))
    assert sr.locate(SCRIPT, LINE.replace("，", ",")) == (i, i + len(LINE))      # 标点宽度不同也认
    assert sr.locate(SCRIPT, "林小雨捧着玉佩低头凝视。") is None                    # 整行出现两次:不唯一
    assert sr.locate(SCRIPT, "她哭了") is None and sr.locate(SCRIPT, " ") is None


def test_context_checks_script_targets_and_reads_last_rejected(store):
    ep = _ep(store)
    ctx = sr.context(store, ep, [_note(target="是我母亲留给我的")])
    assert ctx["characters"] == ["林小雨", "沈墨"] and ctx["rejected"] == []
    with pytest.raises(ValueError, match="第 1 条意见针对的原文"):
        sr.context(store, ep, [_note(target="林小雨捧着玉佩低头凝视。")])
    with pytest.raises(ValueError, match="还没有剧本"):
        sr.context(store, _ep(store, ""), [_note()])


def test_judge_accepts_a_clean_edit(store):
    ctx = sr.context(store, _ep(store), [_note()])
    assert sr.judge_factory(ctx)(_args(_r(1, _e(LINE, "林小雨说：「这玉佩，谁也别想拿走！」")))) == []


def test_judge_rejects_missing_unchanged_overlapping_and_out_of_target_edits(store):
    ctx = sr.context(store, _ep(store), [_note(target="沈墨提灯走进院门。"), _note("别的")])
    got = sr.judge_factory(ctx)(_args(
        _r(1, _e("她哭了", "x"), _e(LINE, LINE + "\n林小雨攥紧玉佩。")),
        _r(2, _e("是我母亲留给我的", "林小雨说：「不给。」"), _e("沈墨提灯走进院门。", "沈墨提灯走进院门。"))))
    assert any("意见 1 的第 1 处修改:old 在剧本里找不到" in p for p in got)
    assert any("意见 1 的第 2 处修改:不在这条意见针对的原文范围里" in p for p in got)
    assert any("意见 2 的第 2 处修改:new 和原文一样" in p for p in got)
    assert any("改的位置重叠了" in p for p in got)


def test_new_lines_keep_the_script_format(store):
    ctx = sr.context(store, _ep(store), [_note()])
    got = sr.judge_factory(ctx)(_args(_r(1, _e(LINE, "【书房】\n林小雨：玉佩呢\n林小雨说：「好」"))))
    assert any("场景标题" in p for p in got) and not any("台词" in p and "好" in p for p in got)


def test_unknown_speaker_asked_for_in_the_notes_is_fatal(store):
    ctx = sr.context(store, _ep(store), [_note("加一个丫鬟小翠递茶")])
    got = sr.judge_factory(ctx)(_args(_r(1, _e(LINE, LINE + "\n小翠说：「小姐请用茶。」"))))
    assert [p for p in got if isinstance(p, Fatal)] and "先在资产里建" in got[0]
    ctx = sr.context(store, _ep(store), [_note("台词太平")])
    got = sr.judge_factory(ctx)(_args(_r(1, _e(LINE, "老管家说：「好吧。」"))))   # 意见里没提老管家
    assert got and not [p for p in got if isinstance(p, Fatal)]               # 模型自己写错:照常回炉
    # 注意别用「林小雨叹气说」这类写法做反例:规则层按已知角色名前缀认说话人,会认成林小雨,不算错


def test_apply_writes_only_accepted_notes_and_records_the_snapshot(store):
    ep = _ep(store)
    sid = repo.add_segment(store, ep, 1, description="【镜头1】" + LINE)
    ctx = sr.context(store, ep, [_note(), _note("删铺垫")])
    args = _args(_r(1, _e(LINE, "林小雨说：「玉佩是娘的。」")), _r(2, _e("沈墨提灯走进院门。", "")))
    res = sr.apply(store, ep, args, ctx, {"accept": [1], "reject": [2]})
    want = SCRIPT.replace(LINE, "林小雨说：「玉佩是娘的。」")
    assert repo.get(store, "episode", ep)["script"] == want
    assert repo.get(store, "segment", sid)["description"] == "【镜头1】林小雨说：「玉佩是娘的。」"
    assert res["synced"] == [1] and res["unsynced"] == 0
    assert res["applied"] == {"script": {"before": SCRIPT, "after": want},
                              "segments": [{"id": sid, "before": "【镜头1】" + LINE,
                                            "after": "【镜头1】林小雨说：「玉佩是娘的。」"}]}


def test_deleting_a_line_takes_its_newline_with_it(store):
    ep = _ep(store)
    ctx = sr.context(store, ep, [_note("删铺垫")])
    sr.apply(store, ep, _args(_r(1, _e("沈墨提灯走进院门。", ""))), ctx, {"accept": [1], "reject": []})
    assert repo.get(store, "episode", ep)["script"] == SCRIPT.replace("沈墨提灯走进院门。\n", "")


def test_audit_catches_a_change_outside_the_accepted_spans():
    i = SCRIPT.index(LINE)
    edits = [(i, i + len(LINE), "新")]
    assert sr.audit_problems(SCRIPT, sr.splice(SCRIPT, edits), edits) == []
    assert sr.audit_problems(SCRIPT, sr.splice(SCRIPT, edits).replace("沈墨", "沈默", 1), edits)


def test_drift_lists_lines_on_one_side_only():
    got = sr.drift(SCRIPT, ["【镜头1】" + LINE.replace("，", ","), "【镜头1】沈墨说：「走吧。」"], ["林小雨", "沈墨"])
    assert got == {"missing": ["天快黑了，你还不进屋？"], "orphan": ["走吧。"]}


def _approved_round(store, ep, notes, args, decision):
    """走一遍真实的 begin → finish(假 LLM)→ approve,拿到带快照的已批准记录。journal 落在当前目录(测试已 chdir 到临时目录)。"""
    llm = LLMBackend(FakeLLM({sr.TOOL: args}), "m", "real")      # 确定性替身,不声称覆盖 LLM 契约
    rid, _ = service.begin(store, sr.TASK, ep, {"notes": notes}, llm)
    service.finish(store, rid, llm, Path("."))
    service.approve(store, rid, decision=decision)
    return runs.get(store, rid)


def test_rollback_restores_script_and_synced_segment_once(store, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ep = _ep(store)
    sid = repo.add_segment(store, ep, 1, description="【镜头1】" + LINE)
    row = _approved_round(store, ep, [_note()], _args(_r(1, _e(LINE, "林小雨说：「玉佩是娘的。」"))),
                          {"accept": [1], "reject": []})
    assert sr.rollback_problems(store, row) == []
    assert "撤销 1 处修改" in sr.rollback_summary(row)
    sr.rollback(store, row)
    assert repo.get(store, "episode", ep)["script"] == SCRIPT
    assert repo.get(store, "segment", sid)["description"] == "【镜头1】" + LINE
    assert "剧本在这一轮之后又改过 —— 先回滚更晚的轮次" in sr.rollback_problems(store, row)   # 段落描述那条也会在
    with pytest.raises(ValueError, match="又改过"):
        sr.rollback(store, row)


def test_next_round_carries_the_rejected_edits(store, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ep = _ep(store)
    _approved_round(store, ep, [_note(), _note("删铺垫")],
                    _args(_r(1, _e(LINE, "林小雨说：「玉佩是娘的。」")), _r(2, _e("沈墨提灯走进院门。", ""))),
                    {"accept": [1], "reject": [2]})
    ctx = sr.context(store, ep, [_note("再狠一点")])
    assert ctx["rejected"] == [{"note": "删铺垫", "changes": [_e("沈墨提灯走进院门。", "")]}]
    assert "<rejected>" in sr.user_message(ctx) and "沈墨提灯走进院门。 → (删除)" in sr.user_message(ctx)
