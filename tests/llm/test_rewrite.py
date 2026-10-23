# -*- coding: utf-8 -*-
"""局部改写(spec 2026-10-23-剧集脑爆系统-design.md 第 3b 节):只改人选中的那一段,2–3 个候选,人挑一个。"""
import pytest

from tools.drama import repo
from tools.llm import rewrite as rw

SCRIPT = ("【老宅庭院 · 黄昏】\n林小雨捧着玉佩低头凝视。\n林小雨说：「这块玉佩，是我母亲留给我的。」\n"
          "沈墨提灯走进院门。\n沈墨说：「天快黑了，你还不进屋？」\n")
SPAN = "林小雨说：「这块玉佩，是我母亲留给我的。」"


def _ep(store, script=SCRIPT):
    d = repo.create_drama(store, "玉佩", "9:16")
    repo.add_character(store, d, "林小雨")
    repo.add_character(store, d, "沈墨")
    return repo.add_episode(store, d, 1, "script", script=script)


def _c(text, why="更有冲突"):
    return {"text": text, "why": why}


def test_span_must_be_in_the_script_exactly_once(store):
    ep = _ep(store)
    assert rw.context(store, ep, span=SPAN.replace("，", ","), intent="加强冲突", note="")["span"] == SPAN
    with pytest.raises(ValueError, match="找不到"):
        rw.context(store, ep, span="她哭了", intent="加强冲突", note="")
    with pytest.raises(ValueError, match="出现了 2 次"):
        rw.context(store, _ep(store, SCRIPT + SPAN + "\n"), span=SPAN, intent="加强冲突", note="")
    with pytest.raises(ValueError, match="意图"):
        rw.context(store, ep, span=SPAN, intent="随便改改", note="")


def test_partial_line_span_is_expanded_to_whole_lines(store):
    ep = _ep(store)
    ctx = rw.context(store, ep, span="这块玉佩，是我母亲留给我的", intent="按评审意见改", note="")
    assert ctx["span"] == SPAN                                             # 半句台词扩成整行,不会套出两层「说：「」
    two = rw.context(store, ep, span="低头凝视。\n林小雨说：「这块", intent="加强冲突", note="")
    assert two["span"] == "林小雨捧着玉佩低头凝视。\n" + SPAN              # 跨行:首尾各扩到整行,不含换行


def test_half_line_span_in_two_lines_is_refused(store):
    ep = _ep(store, SCRIPT + "沈墨说：「这块玉佩，是我母亲留给我的。」\n")
    with pytest.raises(ValueError, match="出现了 2 次"):                   # 半句出现在两行里:位置不确定
        rw.context(store, ep, span="这块玉佩，是我母亲留给我的", intent="加强冲突", note="")


def test_mid_line_quote_rewrite_stays_well_formed_end_to_end(store):
    ep = _ep(store)
    ctx = rw.context(store, ep, span="这块玉佩，是我母亲留给我的", intent="按评审意见改", note="")
    out = {"candidates": [_c("林小雨说：「玉佩是我母亲的。你们，谁敢碰？」"), _c("林小雨攥紧玉佩，一言不发。")]}
    assert rw.judge_factory(ctx)(out) == []
    rw.apply(store, ep, out, ctx, {"pick": 0})
    got = repo.get(store, "episode", ep)["script"]
    assert "说：「林小雨说" not in got and got.count("「") == got.count("」") == 2
    assert got == SCRIPT.replace(SPAN, "林小雨说：「玉佩是我母亲的。你们，谁敢碰？」")


def test_candidates_are_distinct_new_and_keep_the_dialogue_format(store):
    judge = rw.judge_factory(rw.context(store, _ep(store), span=SPAN, intent="加强冲突", note=""))
    good = [_c("林小雨攥紧玉佩。\n林小雨说：「这是我娘留下的唯一念想，谁也别想拿走。」"),
            _c("林小雨说：「玉佩是我母亲的。你们，谁敢碰？」")]
    assert judge({"candidates": good}) == []
    assert any("2–3" in p for p in judge({"candidates": good[:1]}))
    assert any("一样" in p for p in judge({"candidates": [good[0], good[0]]}))
    assert any("和原文一样" in p for p in judge({"candidates": [good[0], _c(SPAN)]}))
    assert any("周老板" in p for p in judge({"candidates": [good[0], _c("周老板说：「卖给我吧。」")]}))
    assert any("长度" in p for p in judge({"candidates": [good[0], _c("嗯")]}))


def test_pick_replaces_only_that_span(store):
    ep = _ep(store)
    ctx = rw.context(store, ep, span=SPAN, intent="加强冲突", note="")
    out = {"candidates": [_c("林小雨说：「玉佩是我母亲的。你们，谁敢碰？」"), _c("林小雨攥紧玉佩，一言不发。")]}
    assert rw.decision_problems(out, {"pick": 1}) == [] and rw.decision_problems(out, {"pick": 5}) != []
    assert rw.apply(store, ep, out, ctx, {"pick": 1}) is None              # 本集没有段落:只改剧本
    got = repo.get(store, "episode", ep)["script"]
    assert got == SCRIPT.replace(SPAN, "林小雨攥紧玉佩，一言不发。") and "沈墨说：「天快黑了" in got


def test_apply_refuses_when_the_span_is_gone(store):
    ep = _ep(store)
    ctx = rw.context(store, ep, span=SPAN, intent="加强冲突", note="")
    repo.update(store, "episode", ep, script=SCRIPT.replace(SPAN, "林小雨沉默。"))
    with pytest.raises(ValueError, match="已经不在剧本里"):
        rw.apply(store, ep, {"candidates": [_c("a" * 10), _c("b" * 10)]}, ctx, {"pick": 0})


def _segments(store, ep):
    s1 = repo.add_segment(store, ep, 1, duration_sec=8.0, video_path="videos/s1.mp4",
                          description="【镜头1】林小雨低头看着玉佩。【镜头2】林小雨说：「这块玉佩，是我母亲留给我的。」")
    s2 = repo.add_segment(store, ep, 2, duration_sec=8.0, video_path="videos/s2.mp4",
                          description="【镜头1】沈墨提灯走进院门。【镜头2】沈墨说：「天快黑了，你还不进屋？」")
    return s1, s2


def test_existing_segments_are_kept_and_only_the_matching_one_gets_the_new_line(store):
    """用户 2026-10-23 定:已经生成的保留,不删段落;对得上的那一段描述换成新台词,视频不动。"""
    ep = _ep(store)
    s1, s2 = _segments(store, ep)
    ctx = rw.context(store, ep, span=SPAN, intent="加强冲突", note="")
    out = {"candidates": [_c("林小雨说：「玉佩是我母亲的。你们，谁敢碰？」"), _c("林小雨攥紧玉佩，一言不发。")]}
    assert rw.apply(store, ep, out, ctx, {"pick": 0}) == 1                # 返回跟着改了台词的段号
    a, b = repo.get(store, "segment", s1), repo.get(store, "segment", s2)
    assert a["description"] == "【镜头1】林小雨低头看着玉佩。【镜头2】林小雨说：「玉佩是我母亲的。你们，谁敢碰？」"
    assert a["video_path"] == "videos/s1.mp4" and b["description"].endswith("你还不进屋？」")


def test_lines_that_do_not_map_to_one_segment_change_only_the_script(store):
    ep = _ep(store)
    s1, _ = _segments(store, ep)
    ctx = rw.context(store, ep, span=SPAN, intent="加强冲突", note="")
    out = {"candidates": [_c("林小雨攥紧玉佩，一言不发。"), _c("林小雨说：「谁敢碰？」\n沈墨说：「我。」")]}
    assert rw.apply(store, ep, out, ctx, {"pick": 0}) is None              # 新文字没有台词:句数对不上
    assert "这块玉佩，是我母亲留给我的" in repo.get(store, "segment", s1)["description"]


def test_decision_pick_must_be_a_real_index():
    out = {"candidates": [_c("a" * 10), _c("b" * 10)]}
    for bad in (True, -1, 5, "1", None):
        assert rw.decision_problems(out, {"pick": bad}) != [], bad
    assert rw.decision_problems(out, {"pick": 0}) == []


def test_sync_replaces_by_position_when_a_new_line_equals_a_later_old_line(store):
    ep = _ep(store, "甲说：「一」\n甲说：「二」\n")
    d = repo.get(store, "episode", ep)["drama_id"]
    repo.add_character(store, d, "甲")
    s = repo.add_segment(store, ep, 1, duration_sec=8.0, video_path="videos/s1.mp4",
                         description="【镜头1】甲说：「一」【镜头2】甲说：「二」")
    span = "甲说：「一」\n甲说：「二」"
    ctx = rw.context(store, ep, span=span, intent="加强冲突", note="")
    out = {"candidates": [_c("甲说：「二」\n甲说：「三」"), _c("甲说：「四」\n甲说：「五」")]}
    assert rw.apply(store, ep, out, ctx, {"pick": 0}) == 1
    assert repo.get(store, "segment", s)["description"] == "【镜头1】甲说：「二」【镜头2】甲说：「三」"


def test_judge_parses_speakers_like_rules_and_rejects_empty(store):
    judge = rw.judge_factory(rw.context(store, _ep(store), span=SPAN, intent="加强冲突", note=""))
    ok = _c("林小雨攥紧玉佩说：「甲乙丙丁戊己庚辛。」")  # 反例
    other = _c("林小雨攥紧玉佩。\n林小雨说：「这是我娘留下的唯一念想。」")
    assert judge({"candidates": [ok, other]}) != []                      # 动作连在名字后面:rules 认不出说话人,拒
    assert any("林小雨攥紧玉佩" in p and "不是本剧角色" in p for p in judge({"candidates": [ok, other]}))
    plain = _c("林小雨说：「这是我娘留下的唯一念想。」")
    assert judge({"candidates": [plain, _c("林小雨攥紧玉佩。\n林小雨说：「谁也别想拿走。」")]}) == []
    assert any("是空的" in p for p in judge({"candidates": [ok, _c("   ")]}))
    assert any("格式" in p for p in judge({"candidates": [ok, _c("林小雨「这是我娘留下的唯一念想。」")]}))
