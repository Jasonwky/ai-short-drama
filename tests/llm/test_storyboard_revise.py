# -*- coding: utf-8 -*-
"""分镜修改(spec 2026-10-24-剧本分镜修改回路-design.md 第 2 节)。FakeLLM 只测确定性路径,不声称覆盖 LLM 契约。"""
import json
from pathlib import Path

import pytest

from tools.drama import repo
from tools.llm import runs, service, storyboard_revise as sv
from tools.llm.fake import FakeLLM
from tools.llm.service import LLMBackend

SCRIPT = "林小雨说：「这块玉佩，是我母亲留给我的。」\n林小雨说：「我会一直带着它。」\n"
BLOCK = [{"character": "林小雨", "pose": "站", "facing": "镜头", "side": "中", "holding": "玉佩"}]
BLANK = {s: None for s in sv.SLOTS}


def _ep(store):
    d = repo.create_drama(store, "玉佩", "9:16")
    lin = repo.add_character(store, d, "林小雨")
    repo.add_character(store, d, "沈墨")
    yard = repo.add_scene(store, d, "老宅庭院")
    jade = repo.add_prop(store, d, "玉佩")
    ep = repo.add_episode(store, d, 1, "script", script=SCRIPT)
    ids = []
    for n, line in enumerate(["这块玉佩，是我母亲留给我的。", "我会一直带着它。"], 1):
        sid = repo.add_segment(store, ep, n, scene_id=yard, duration_sec=8.0, reference_mode="first",
                               description=f"【镜头1】林小雨低头看着玉佩。【镜头2】林小雨说：「{line}」",
                               image_prompt="@林小雨 在 @老宅庭院 里,低头看 @玉佩",
                               video_prompt="@林小雨 在 @老宅庭院 低头看 @玉佩",
                               blocking_json=json.dumps(BLOCK, ensure_ascii=False), shot_type="中景")
        repo.bind_characters(store, sid, [lin])
        repo.bind_props(store, sid, [jade])
        ids.append(sid)
    return ep, ids


def _note(text="运镜太平", target="", must=False):
    return {"text": text, "target": target, "must": must}


def _p(segment, field, **slots):
    return {**BLANK, "segment": segment, "field": field, **slots}


def _r(note, *patches, verdict="改", reason="用意"):
    return {"note": note, "verdict": verdict, "reason": reason, "patches": list(patches)}


def test_context_refuses_structure_changes_and_bad_targets(store):
    ep, _ = _ep(store)
    ctx = sv.context(store, ep, [_note(target="1、2")])
    assert [v["number"] for v in ctx["segments"]] == [1, 2] and ctx["segments"][0]["blocking"] == BLOCK
    with pytest.raises(ValueError, match="重拆分镜"):
        sv.context(store, ep, [_note("第 2 段拆成两段")])
    with pytest.raises(ValueError, match="不在本集段落"):
        sv.context(store, ep, [_note(target="7")])
    with pytest.raises(ValueError, match="要填段号"):
        sv.context(store, ep, [_note(target="第一段")])


def test_judge_accepts_a_clean_video_prompt_patch(store):
    ep, _ = _ep(store)
    ctx = sv.context(store, ep, [_note()])
    patch = _p(1, "video_prompt", text="@林小雨 在 @老宅庭院 低头看 @玉佩,镜头缓缓推近")
    assert sv.judge_factory(ctx)({"responses": [_r(1, patch)]}) == []


def test_judge_checks_slots_targets_conflicts_and_no_op(store):
    ep, _ = _ep(store)
    ctx = sv.context(store, ep, [_note(target="1"), _note("别的")])
    got = sv.judge_factory(ctx)({"responses": [
        _r(1, _p(2, "video_prompt", text="@林小雨 在 @老宅庭院 看 @玉佩"), _p(1, "camera", text="x")),
        _r(2, _p(1, "image_prompt", text="@林小雨 在 @老宅庭院 里,低头看 @玉佩"),
           _p(2, "video_prompt", text="@林小雨 在 @老宅庭院 看 @玉佩,推近"))]})
    assert any("第 2 段不在这条意见针对的段落 [1] 里" in p for p in got)
    assert any("camera 只填 shot_type/angle/movement" in p for p in got)
    assert any("和现在一样" in p for p in got)
    assert any("已经被意见 1 的第 1 个补丁改了" in p for p in got)


def test_judge_only_blocks_problems_the_patch_introduces(store):
    ep, ids = _ep(store)
    repo.update(store, "segment", ids[1], image_prompt="@林小雨 站在 @老宅庭院 里,低头看 @玉佩")   # 原有问题
    ctx = sv.context(store, ep, [_note()])
    ok = _p(1, "video_prompt", text="@林小雨 在 @老宅庭院 低头看 @玉佩,推近")
    assert sv.judge_factory(ctx)({"responses": [_r(1, ok)]}) == []                # 原有的姿态词不怪这次修改
    assert any("原有问题" in h for h in sv.baseline_hints(ctx))
    bad = _p(1, "image_prompt", text="@林小雨 跪在 @老宅庭院 里,看 @玉佩")
    assert any("段落1:image_prompt 里写了姿态" in p for p in sv.judge_factory(ctx)({"responses": [_r(1, bad)]}))


def test_subshot_patch_recomputes_description_and_keeps_lines_verbatim(store):
    ep, _ = _ep(store)
    ctx = sv.context(store, ep, [_note()])
    subs = [{"visual": "林小雨抬眼看向远方。", "speaker": None, "line": None},
            {"visual": "她握紧玉佩。", "speaker": "林小雨", "line": "这块玉佩,是我母亲留给我的。"}]
    patch = _p(1, "subshots", subshots=subs, pace="normal")
    assert sv.judge_factory(ctx)({"responses": [_r(1, patch)]}) == []
    v = sv.patched(ctx, [patch])[0]
    assert v["description"] == "【镜头1】林小雨抬眼看向远方。【镜头2】她握紧玉佩。林小雨说：「这块玉佩，是我母亲留给我的。」"
    dropped = _p(1, "subshots", subshots=subs[:1], pace="normal")
    assert any("没有出现在任何段落里" in p for p in sv.judge_factory(ctx)({"responses": [_r(1, dropped)]}))


def test_apply_writes_accepted_patches_and_audits_scope(store):
    ep, ids = _ep(store)
    ctx = sv.context(store, ep, [_note(), _note("近一点")])
    args = {"responses": [_r(1, _p(1, "video_prompt", text="@林小雨 在 @老宅庭院 低头看 @玉佩,推近")),
                          _r(2, _p(2, "camera", shot_type="近景", angle="平视", movement="推"))]}
    res = sv.apply(store, ep, args, ctx, {"accept": [1], "reject": [2]})
    assert repo.get(store, "segment", ids[0])["video_prompt"].endswith("推近")
    assert repo.get(store, "segment", ids[1])["shot_type"] == "中景"                 # 驳回的不写
    assert res["touched"] == [1] and res["applied"]["segments"][0]["id"] == ids[0]
    assert res["applied"]["segments"][0]["before"]["video_prompt"] == "@林小雨 在 @老宅庭院 低头看 @玉佩"


def test_cost_lists_segments_whose_frames_or_videos_go_stale(store):
    ep, ids = _ep(store)
    repo.update(store, "segment", ids[0], first_frame_path="frames/a.png", video_path="videos/a.mp4")
    ctx = sv.context(store, ep, [_note()])
    lines = sv.cost(store, ctx, [_p(1, "image_prompt", text="x"), _p(2, "video_prompt", text="y")])
    assert len(lines) == 1 and lines[0].startswith("第 1 段:1 张参考帧、1 条视频")


def test_rollback_restores_the_touched_fields_once(store, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ep, ids = _ep(store)
    patch = _p(1, "camera", shot_type="近景", angle="俯拍", movement="推")
    llm = LLMBackend(FakeLLM({sv.TOOL: {"responses": [_r(1, patch)]}}), "m", "real")   # 确定性替身
    rid, _ = service.begin(store, sv.TASK, ep, {"notes": [_note()]}, llm)
    service.finish(store, rid, llm, Path("."))
    service.approve(store, rid, decision={"accept": [1], "reject": []})
    row = runs.get(store, rid)
    assert repo.get(store, "segment", ids[0])["angle"] == "俯拍"
    assert sv.rollback_problems(store, row) == [] and "第 1 段" in sv.rollback_summary(row)
    sv.rollback(store, row)
    seg = repo.get(store, "segment", ids[0])
    assert (seg["shot_type"], seg["angle"], seg["movement"]) == ("中景", "", "")    # 库默认是空串(写计划时核实)
    assert sv.rollback_problems(store, row) == ["第 1 段在这一轮之后又改过 —— 先回滚更晚的轮次"]
