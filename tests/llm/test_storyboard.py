# -*- coding: utf-8 -*-
"""分镜拆解的确定性部分:格式拼接、时长、判卷、写入。LLM 本身的行为由 test_contract_real.py 真跑验证。"""
import json
from pathlib import Path

import pytest

from tools.drama import repo, rules
from tools.gen.capability import MINIMAX_H3
from tools.llm import storyboard as sb
from tools.llm.gate import schema_errors, strict_schema_problems

GOLDEN = (Path(__file__).parent / "golden" / "jade_ep1.txt").read_text(encoding="utf-8")
CTX = {"script": GOLDEN, "characters": ["林小雨", "沈墨"], "scenes": ["老宅庭院", "老宅书房"], "props": ["玉佩"]}


def _sub(visual, speaker=None, line=None):
    return {"visual": visual, "speaker": speaker, "line": line}


def _seg(scene, chars, subshots, props=(), pace="normal", img=None, vid=None):
    bound = [*chars, scene, *props]
    return {"scene": scene, "characters": list(chars), "props": list(props), "subshots": subshots, "pace": pace,
            "image_prompt": img if img is not None else " ".join(f"@{b}" for b in bound),
            "video_prompt": vid if vid is not None else f"@{chars[0]} 缓慢抬头",
            "blocking": [{"character": c, "pose": "站", "facing": "镜头", "side": "中", "holding": "无"} for c in chars]}


def good_args():
    """一份手工写的、应当通过判卷的分镜 —— 判卷规则的正例基线。"""
    return {"segments": [
        _seg("老宅庭院", ["林小雨"], [
            _sub("林小雨捧着玉佩低头凝视"),
            _sub("玉佩特写", "林小雨", "这块玉佩，是我母亲留给我的。")], props=["玉佩"]),
        _seg("老宅庭院", ["林小雨", "沈墨"], [
            _sub("沈墨提灯走进院门", "沈墨", "天快黑了，你还不进屋？"),
            _sub("林小雨攥紧玉佩", "林小雨", "我总觉得，她还会回来。"),
            _sub("沈墨挂起灯笼", "沈墨", "三年了，该放下了。")]),
        _seg("老宅书房", ["林小雨", "沈墨"], [
            _sub("台灯下玉佩背面的字", "林小雨", "沈墨，你看这个字。"),
            _sub("沈墨脸色骤变", "沈墨", "这是我们沈家的东西。")], props=["玉佩"]),
        _seg("老宅书房", ["林小雨", "沈墨"], [
            _sub("林小雨抬头盯着他", "林小雨", "那我母亲，为什么会有它？"),
            _sub("沈墨走到窗边沉默")]),
    ]}


def test_schema_is_strict_and_closed_over_the_dramas_assets():
    s = sb.schema(CTX)
    assert strict_schema_problems(s) == []
    seg = s["properties"]["segments"]["items"]["properties"]
    assert seg["scene"]["enum"] == ["老宅庭院", "老宅书房"] and seg["props"]["items"]["enum"] == ["玉佩"]
    assert schema_errors(good_args(), s) == []


def test_no_props_means_no_props_field():
    s = sb.schema({**CTX, "props": []})
    assert "props" not in s["properties"]["segments"]["items"]["properties"]
    assert strict_schema_problems(s) == []


def test_description_format_is_built_by_code():
    seg = good_args()["segments"][1]
    desc = sb.describe(seg)
    assert desc.startswith("【镜头1】沈墨提灯走进院门沈墨说：「天快黑了") and rules.subshot_count(desc) == 3
    assert [line for _, line in rules.dialogue_lines(desc)][1] == "我总觉得，她还会回来。"


def test_duration_is_computed_not_asked():
    desc = sb.describe(good_args()["segments"][1])
    need = rules.min_duration_sec(desc)
    assert sb.duration(desc, "tight") == pytest.approx(max(-(-need * 2 // 1) / 2, MINIMAX_H3.duration_range_sec()[0]))
    assert sb.duration(desc, "lingering") > sb.duration(desc, "tight")
    short = sb.describe({"subshots": [_sub("空镜"), _sub("空镜")]})
    assert sb.duration(short, "tight") == pytest.approx(-(-MINIMAX_H3.duration_range_sec()[0] * 2 // 1) / 2)


def test_good_storyboard_passes_the_judge():
    assert sb.judge_factory(CTX)(good_args()) == []


def test_paraphrased_line_is_caught_by_data():
    a = good_args()
    a["segments"][0]["subshots"][1]["line"] = "这玉佩是我妈留给我的。"     # 意思对、字不对
    probs = sb.judge_factory(CTX)(a)
    assert any("逐字照抄" in p for p in probs) and any("没有出现在任何段落里" in p for p in probs)


def test_stage_direction_smuggled_into_line_is_caught():
    # 实测里 GPT 把诱导出来的 extra_note 塞进了台词字符串 —— schema 挡不住,数据挡得住
    a = good_args()
    a["segments"][0]["subshots"][1]["line"] = "这块玉佩，是我母亲留给我的。(轻声)"
    assert any("逐字照抄" in p for p in sb.judge_factory(CTX)(a))


def test_out_of_order_and_missing_speaker():
    a = good_args()
    segs = a["segments"]
    segs[1]["subshots"][0], segs[1]["subshots"][1] = segs[1]["subshots"][1], segs[1]["subshots"][0]
    segs[3]["characters"] = ["沈墨"]
    probs = sb.judge_factory(CTX)(a)
    assert any("顺序错了" in p for p in probs)
    assert any("说话人「林小雨」不在本段角色" in p for p in probs)


def test_format_symbols_and_half_filled_dialogue():
    a = good_args()
    a["segments"][0]["subshots"][0]["visual"] = "【特写】玉佩"
    a["segments"][3]["subshots"][1]["speaker"] = "沈墨"
    probs = sb.judge_factory(CTX)(a)
    assert any("不许出现【】「」" in p for p in probs) and any("要么都填" in p for p in probs)


def test_mentions_must_match_bindings():
    a = good_args()
    a["segments"][0]["image_prompt"] = "@林小雨 在 @老宅书房"
    probs = sb.judge_factory(CTX)(a)
    assert any("@老宅书房 不是本段绑定的素材" in p for p in probs)
    assert any("绑定了「玉佩」但 image_prompt 没有 @ 它" in p for p in probs)


def test_too_much_dialogue_in_one_segment_is_sent_back_not_truncated():
    lines = [ln for _, ln in rules.dialogue_lines(GOLDEN)]
    one = {"segments": [_seg("老宅庭院", ["林小雨", "沈墨"],
                              [_sub("x", "林小雨" if i % 2 == 0 else "沈墨", ln) for i, ln in enumerate(lines)])]}
    probs = sb.judge_factory(CTX)(one)
    assert any("子镜头" in p for p in probs) and any("段落数应在" in p for p in probs)


# ── 写入 ──────────────────────────────────────────────

def _episode(store):
    d = repo.create_drama(store, "玉佩", "9:16", "realistic")
    ep = repo.add_episode(store, d, 1, "script", script=GOLDEN)
    for n in CTX["characters"]:
        repo.add_character(store, d, n)
    for n in CTX["scenes"]:
        repo.add_scene(store, d, n)
    repo.add_prop(store, d, "玉佩")
    return ep


def test_context_reads_names_from_the_drama(store):
    ep = _episode(store)
    empty = {"purpose": "", "furnishings": ""}
    assert sb.context(store, ep) == {**CTX, "scene_info": {"老宅庭院": empty, "老宅书房": empty}}


def test_context_refuses_what_it_cannot_do(store):
    d = repo.create_drama(store, "空", "9:16", "realistic")
    ep = repo.add_episode(store, d, 1, "script")
    with pytest.raises(ValueError) as e:
        sb.context(store, ep)
    assert "还没有剧本" in str(e.value) and "还没有场景" in str(e.value) and "还没有角色" in str(e.value)


def test_apply_creates_segments_with_bindings_and_computed_durations(store):
    ep = _episode(store)
    ids = sb.apply(store, ep, good_args(), CTX)
    assert len(ids) == 4
    s1 = repo.get(store, "segment", ids[0])
    assert s1["number"] == 1 and s1["reference_mode"] == "first" and rules.check_segment(
        s1["description"], s1["duration_sec"], MINIMAX_H3) == []
    assets = repo.segment_assets(store, ids[1])
    assert [c["name"] for c in assets["characters"]] == ["林小雨", "沈墨"] and assets["scene"]["name"] == "老宅庭院"
    assert [e["character"] for e in json.loads(repo.get(store, "segment", ids[1])["blocking_json"])] == ["林小雨", "沈墨"]
    with pytest.raises(ValueError, match="已经有段落"):          # 批准前有人手工建了段落 → 不重复写
        sb.apply(store, ep, good_args(), CTX)


def test_apply_writes_back_the_scripts_own_wording(store):
    """模型抄台词时换了标点宽度,判卷(折叠后比)放行;写回段落描述要用剧本原文的写法,不用模型抄的。"""
    ep = _episode(store)
    args = good_args()
    args["segments"][0]["subshots"][1]["line"] = "这块玉佩,是我母亲留给我的。"            # 半角逗号
    assert sb.judge_factory(CTX)(args) == []
    ids = sb.apply(store, ep, args, CTX)
    desc = repo.get(store, "segment", ids[0])["description"]
    assert "这块玉佩，是我母亲留给我的。" in desc and "这块玉佩," not in desc


def test_blocking_must_cover_the_segment_and_pose_changes_need_an_action():
    """场记表(v13):本段每个角色一项;同场景相邻段姿态变了,上一段要交代动作(实战第 5 段李福从站到跪)。"""
    a = good_args()
    a["segments"][1]["blocking"] = a["segments"][1]["blocking"][:1]
    assert any(p.startswith("段落2") and "场记表缺「沈墨」" in p for p in sb.judge_factory(CTX)(a))
    a = good_args()
    a["segments"][1]["blocking"][0]["pose"] = "跪"           # 段落 1 开头林小雨站着,段落 1 里没写跪
    (msg,) = [p for p in sb.judge_factory(CTX)(a) if "没交代" in p]
    assert msg.startswith("段落2") and "林小雨" in msg
    a["segments"][0]["subshots"][0]["visual"] = "林小雨捧着玉佩慢慢跪下"
    assert not [p for p in sb.judge_factory(CTX)(a) if "没交代" in p]


def test_prompt_asks_for_blocking_and_prop_size():
    assert "blocking" in sb.SYSTEM and "面朝" not in sb.SYSTEM.split("blocking")[0]
    assert "道具" in sb.SYSTEM and "大小" in sb.SYSTEM
    entry = sb.schema(CTX)["properties"]["segments"]["items"]["properties"]["blocking"]["items"]
    assert entry["properties"]["facing"]["enum"] == ["林小雨", "沈墨", "镜头", "侧对镜头", "背对镜头"]
    assert entry["properties"]["holding"]["enum"] == ["玉佩", "无"]



def test_furniture_outside_the_scene_list_is_sent_back():
    """场景陈设表(v13):场景登记了固定陈设,首帧提示词里清单外的陈设打回;提示词把陈设告诉 LLM。"""
    ctx = {**CTX, "scene_info": {"老宅庭院": {"purpose": "庭院", "furnishings": "石桌、石凳、古井"},
                                 "老宅书房": {"purpose": "", "furnishings": ""}}}
    assert "老宅庭院(庭院;固定陈设:石桌、石凳、古井)" in sb.user_message(ctx)
    a = good_args()
    a["segments"][0]["image_prompt"] = "@林小雨 坐在床边捧着 @玉佩,@老宅庭院"
    (msg,) = [p for p in sb.judge_factory(ctx)(a) if "陈设" in p]
    assert msg.startswith("段落1") and "「床」" in msg
    a["segments"][0]["image_prompt"] = "@林小雨 坐在石桌旁捧着 @玉佩,@老宅庭院"
    assert not [p for p in sb.judge_factory(ctx)(a) if "陈设" in p]
