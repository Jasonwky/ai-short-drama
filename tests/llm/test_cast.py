# -*- coding: utf-8 -*-
"""AI 出资产表的确定性部分(spec 2026-10-25-全剧审与资产表-design.md 第 1 节):覆盖 / 并错判卷、只填空字段、
带意见的认领、出过图拒改、别名排剧本修改、回滚。FakeLLM 不进来 —— 产出由测试手写,LLM 契约在 test_contract_cast.py。"""
import copy

import pytest

from tools.drama import repo
from tools.drama.db import Store
from tools.llm import cast, llm_queue, runs, service

EP1 = "【校场 · 白天】\n陈老三说：「来了。」\n兵卒甲说：「谁?」\n兵卒乙说：「新人。」"
EP2 = "【营地空地 · 夜】\n老陈走过来。\n老陈说：「行。」\n燕无咎说：「好。」"
FP = "A weathered middle-aged soldier in worn leather armor."


def _char(name, aliases=()):
    return {"name": name, "aliases": list(aliases), "appearance": "中年,黑瘦", "styling": "旧皮甲", "final_prompt": FP}


def _scene(name, places):
    return {"name": name, "places": places, "location": "军营", "time": "白天", "lighting": "日光",
            "furnishings": "木桩", "final_prompt": "An empty army camp yard, wooden posts."}


GOOD = {"characters": [_char("陈老三", ["老陈"]), _char("兵卒甲"), _char("兵卒乙"), _char("燕无咎")],
        "scenes": [_scene("校场", ["校场"]), _scene("营地", ["营地空地"])], "props": []}


@pytest.fixture
def drama():
    s = Store(":memory:")
    d = repo.create_drama(s, "将军", "9:16", "realistic")
    e1 = repo.add_episode(s, d, 1, "script", script=EP1)
    e2 = repo.add_episode(s, d, 2, "script", script=EP2)
    yield s, d, e1, e2
    s.close()


def _judge(s, d, out, notes=()):
    ctx = cast.context(s, d, list(notes))
    return cast.judge_factory(ctx)(out), ctx


def test_context_lists_speakers_pairs_and_places(drama):
    s, d, *_ = drama
    ctx = cast.context(s, d, [])
    assert ctx["speakers"] == {"陈老三": [1], "兵卒甲": [1], "兵卒乙": [1], "老陈": [2], "燕无咎": [2]}
    assert ("兵卒甲", "陈老三", 1) in ctx["pairs"] and ("兵卒乙", "兵卒甲", 1) in ctx["pairs"]
    assert ctx["places"] == {"校场": [1], "营地空地": [2]}


def test_context_refuses_a_drama_without_scripts():
    s = Store(":memory:")
    d = repo.create_drama(s, "空", "9:16")
    repo.add_episode(s, d, 1, "script")
    with pytest.raises(ValueError, match="还没有任何一集剧本"):
        cast.context(s, d, [])


def test_good_table_passes(drama):
    s, d, *_ = drama
    assert _judge(s, d, GOOD)[0] == []


def test_every_speaker_and_place_must_belong_somewhere(drama):
    s, d, *_ = drama
    out = copy.deepcopy(GOOD)
    out["characters"][0]["aliases"] = []
    out["scenes"] = out["scenes"][:1]
    problems = _judge(s, d, out)[0]
    assert any("说话人「老陈」没有归到任何角色" in p for p in problems)
    assert any("地点「营地空地」没有归到任何场景" in p for p in problems)


def test_two_people_talking_to_each_other_cannot_be_merged(drama):
    s, d, *_ = drama
    out = copy.deepcopy(GOOD)
    out["characters"] = [_char("陈老三", ["老陈"]), _char("兵卒", ["兵卒甲", "兵卒乙"]), _char("燕无咎")]
    assert any("「兵卒乙」和「兵卒甲」前后接话" in p for p in _judge(s, d, out)[0])


def test_a_name_cannot_belong_to_two_characters_and_existing_names_must_stay(drama):
    s, d, *_ = drama
    repo.add_character(s, d, "主帅")
    out = copy.deepcopy(GOOD)
    out["characters"][3]["aliases"] = ["老陈"]
    problems = _judge(s, d, out)[0]
    assert any("「老陈」同时归到了" in p for p in problems)
    assert any("已有的角色「主帅」要原样出现" in p for p in problems)


def test_final_prompt_must_be_english(drama):
    s, d, *_ = drama
    out = copy.deepcopy(GOOD)
    out["characters"][0]["final_prompt"] = "黑瘦的中年老兵"
    assert any("final_prompt 要写英文" in p for p in _judge(s, d, out)[0])


def _approve(s, d, out, notes=(), decision=None):
    params = {"notes": list(notes)}
    rid = runs.start(s, task="cast", target_type="drama", target_id=d, model="m",
                     input_sha=service.prepare(s, "cast", d, params).sha, llm_mode="real", params=params)
    runs.propose(s, rid, out, "j")
    service.approve(s, rid, decision=decision or {"accept": [], "reject": []})
    return rid


def test_approve_creates_assets_fills_only_empty_fields_and_queues_alias_fixes(drama):
    s, d, e1, e2 = drama
    yan = repo.add_character(s, d, "燕无咎", appearance="人写的外貌")
    rid = _approve(s, d, GOOD)
    names = {r[0] for r in s.conn.execute("SELECT name FROM character WHERE drama_id = ?", (d,))}
    assert names == {"陈老三", "兵卒甲", "兵卒乙", "燕无咎"}
    y = repo.get(s, "character", yan)
    assert y["appearance"] == "人写的外貌" and y["styling"] == "旧皮甲"        # 人写的不覆盖,空的补上
    assert {r[0] for r in s.conn.execute("SELECT name FROM scene WHERE drama_id = ?", (d,))} == {"校场", "营地"}
    item = llm_queue.next_waiting(s)
    assert (item["task"], item["target_id"]) == ("script_revise", e2)                  # 只有第 2 集出现了「老陈」
    assert "「老陈」→「陈老三」" in llm_queue.params(item)["notes"][0]["text"]
    a = runs.applied(runs.get(s, rid))
    assert len(a["created"]) == 5 and a["alias_episodes"] == [2]


def test_rollback_deletes_unused_new_assets_and_restores_fields(drama):
    s, d, *_ = drama
    yan = repo.add_character(s, d, "燕无咎")
    rid = _approve(s, d, GOOD)
    row = runs.get(s, rid)
    lao = s.conn.execute("SELECT id FROM character WHERE name = '陈老三'").fetchone()[0]
    repo.update(s, "character", lao, turnaround_path="assets/x.png")
    s.conn.execute("INSERT INTO task (kind, target_type, target_id, model, params_json, seed, prompt, refs_json,"
                   " created_at) VALUES ('image', 'character', ?, 'm', '{}', 1, 'p', '[]', 'now')", (lao,))
    assert any("陈老三" in p and "出过图" in p for p in cast.rollback_problems(s, row))
    s.conn.execute("DELETE FROM task")
    assert cast.rollback_problems(s, row) == []
    cast.rollback(s, row)
    assert [r[0] for r in s.conn.execute("SELECT name FROM character WHERE drama_id = ?", (d,))] == ["燕无咎"]
    assert repo.get(s, "character", yan)["appearance"] == ""


NOTE = [{"text": "陈老三的造型要更破旧", "target": "", "must": False}]


def _with_notes(s, d, changes):
    out = copy.deepcopy(GOOD)
    out["characters"][0]["styling"] = "补丁摞补丁的旧皮甲"
    out["responses"] = [{"note": 1, "verdict": "改", "reason": "更破", "changes": changes}]
    return out


def test_notes_mode_only_writes_claimed_and_accepted_changes(drama):
    s, d, *_ = drama
    _approve(s, d, GOOD)
    s.conn.execute("DELETE FROM llm_queue")
    out = _with_notes(s, d, [{"kind": "character", "name": "陈老三", "field": "styling"}])
    out["characters"][1]["styling"] = "没人认领的改动"
    assert _judge(s, d, out, NOTE)[0] == []
    _approve(s, d, out, NOTE, {"accept": [1], "reject": []})
    get = lambda n: s.conn.execute("SELECT styling FROM character WHERE name = ?", (n,)).fetchone()[0]  # noqa: E731
    assert get("陈老三") == "补丁摞补丁的旧皮甲" and get("兵卒甲") == "旧皮甲"


def test_notes_mode_claims_must_be_real_and_images_lock_text(drama):
    s, d, *_ = drama
    _approve(s, d, GOOD)
    lao = s.conn.execute("SELECT id FROM character WHERE name = '陈老三'").fetchone()[0]
    repo.update(s, "character", lao, turnaround_path="assets/x.png")
    out = _with_notes(s, d, [{"kind": "character", "name": "陈老三", "field": "styling"},
                             {"kind": "character", "name": "兵卒甲", "field": "styling"}])
    problems = _judge(s, d, out, NOTE)[0]
    assert any("「兵卒甲」styling」其实没有改动" in p for p in problems)
    assert any("「陈老三」已经出过图" in p for p in problems)


def test_decision_without_notes_must_be_empty():
    assert cast.decision_problems(GOOD, {"accept": [], "reject": []}) == []
    assert cast.decision_problems(GOOD, {"accept": [1], "reject": []})


def test_constraint_goes_into_the_prompt_only_when_given(drama):
    s, d, *_ = drama
    plain = cast.user_message(cast.context(s, d, []))
    assert "<constraint>" not in plain and plain == cast.user_message(cast.context(s, d, [], ""))
    assert "<constraint>\n场景合并到 10 个左右\n</constraint>" in cast.user_message(cast.context(s, d, [], "场景合并到 10 个左右"))


ART = '{"era": "架空古代边军", "costume": "交领右衽", "banned": ["uniform", "口袋"]}'


def test_art_goes_into_the_message_and_banned_words_are_rejected(drama):
    """spec 2026-10-28 第 2 节:美术设定进提示词;禁用词判卷打回(孙老栓的 uniform 出成了近代制服)。"""
    s, d, *_ = drama
    assert "<art>" not in cast.user_message(cast.context(s, d, []))      # 没填:提示词逐字节不变
    s.conn.execute("UPDATE drama SET art_json = ? WHERE id = ?", (ART, d))
    msg = cast.user_message(cast.context(s, d, []))
    assert "<art>\n时代:架空古代边军\n服饰总则:交领右衽\nbanned:uniform、口袋\n</art>" in msg
    bad = copy.deepcopy(GOOD)
    bad["characters"][0]["final_prompt"] = "An old soldier in a grey Uniform."
    bad["characters"][1]["styling"] = "灰布衣,胸前两个口袋"
    problems, _ = _judge(s, d, bad)
    assert "角色「陈老三」的 final_prompt 含本剧禁用词「uniform」" in problems
    assert "角色「兵卒甲」的 styling 含本剧禁用词「口袋」" in problems
    assert _judge(s, d, GOOD)[0] == []


def test_banned_words_in_already_imaged_assets_are_not_chased(drama):
    """已出图的老资产文字不让改(改字不改图),禁用词交给资产图审,这里不打回。"""
    s, d, *_ = drama
    s.conn.execute("UPDATE drama SET art_json = ? WHERE id = ?", (ART, d))
    repo.add_character(s, d, "陈老三", appearance="中年,黑瘦", styling="旧皮甲", final_prompt="A soldier in a uniform.",
                       turnaround_path="assets/c.png")
    out = copy.deepcopy(GOOD)
    out["characters"][0]["final_prompt"] = "A soldier in a uniform."
    assert not [p for p in _judge(s, d, out)[0] if "禁用词" in p]
