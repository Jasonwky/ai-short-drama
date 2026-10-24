# -*- coding: utf-8 -*-
"""修改回路的共用部分(spec 2026-10-24-剧本分镜修改回路-design.md 第 0 节)。"""
import pytest

from tools.drama import repo
from tools.llm import revise, runs


def _note(text="开场太慢", target="", must=False):
    return {"text": text, "target": target, "must": must}


def test_notes_from_form_skips_blank_rows():
    form = {"note-0": " 开场太慢 ", "target-0": "", "note-1": "", "note-2": "台词太书面", "target-2": "李福说",
            "must-2": "1"}
    assert revise.notes_from_form(form) == [_note(), _note("台词太书面", "李福说", True)]


def test_check_notes_counts_and_shape():
    revise.check_notes([_note()])
    with pytest.raises(ValueError, match="1–10"):
        revise.check_notes([])
    with pytest.raises(ValueError, match="1–10"):
        revise.check_notes([_note()] * 11)
    with pytest.raises(ValueError, match="第 1 条"):
        revise.check_notes([_note(" ")])


def _r(note, verdict="改", changes=("x",), reason="用意"):
    return {"note": note, "verdict": verdict, "reason": reason, "edits": list(changes)}


def test_every_note_answered_once_and_must_change_cannot_be_skipped():
    notes = [_note(), _note("加狠话", must=True)]
    assert revise.response_problems([_r(1), _r(2)], notes, "edits") == []
    got = revise.response_problems([_r(1), _r(1), _r(2, "不改", (), "不该改")], notes, "edits")
    assert "意见 1 要恰好回应一次,现在 2 次" in got and "意见 2 标了「必须改」,不能回答「不改」" in got
    got = revise.response_problems([_r(1, changes=()), _r(2, "不改", (), " ")], [_note(), _note()], "edits")
    assert "意见 1 回答「改」却没有给出修改" in got and "意见 2 回答「不改」要写理由" in got


def test_decision_covers_exactly_the_changed_notes():
    args = {"responses": [_r(1), _r(2, "不改", (), "理由"), _r(3)]}
    assert revise.decision_problems(args, {"accept": [1], "reject": [3]}) == []
    assert revise.decision_problems(args, {"accept": [1], "reject": []}) == ["意见 [3] 还没表态"]
    assert revise.decision_problems(args, {"accept": [1, 2], "reject": [3]}) == ["意见 [2] 没有修改,不用表态"]
    assert revise.decision_problems(args, {"accept": [1], "reject": [1, 3]}) == ["意见 [1] 既接受又驳回"]
    assert revise.decision_problems(args, {"pick": 0}) == ["要对每条改了的意见选「接受」或「驳回」"]
    assert revise.accepted(args, {"accept": [3], "reject": [1]}) == [_r(3)]


def test_last_rejected_comes_from_the_latest_approved_round(store):
    d = repo.create_drama(store, "玉佩", "9:16")
    ep = repo.add_episode(store, d, 1, "script", script="甲\n")
    assert revise.last_rejected(store, "script_revise", "episode", ep, "edits") == []
    rid = runs.start(store, task="script_revise", target_type="episode", target_id=ep, model="m", input_sha="x",
                     llm_mode="real", params={"notes": [_note(), _note("台词太书面")]})
    edit = {"old": "甲", "new": "乙", "why": "w"}
    runs.propose(store, rid, {"responses": [_r(1, changes=[edit]), _r(2, changes=[edit])]}, "j")
    runs.approve(store, rid, {"accept": [1], "reject": [2]})
    assert revise.last_rejected(store, "script_revise", "episode", ep, "edits") == [
        {"note": "台词太书面", "changes": [edit]}]


def test_render_notes_marks_target_and_must():
    text = revise.render_notes([_note(), _note("加近景", "3", True)], "针对第 {} 段")
    assert text == "1. 开场太慢\n2. 加近景(针对第 3 段)【必须改】"
