# -*- coding: utf-8 -*-
"""拆解卡第二层 ref_card + 创作视图(spec 2026-11-07-参考库-design.md 2.2–2.5):上下文、判卷、人的选择、
批准时连带批准单元运行(先查后动)、写卡、旧卡被取代、创作视图去引用 / 换名 / 去驳回 / 12 字重叠拦批准。
FakeLLM 只当确定性替身走 begin / finish / approve,不声称覆盖 LLM 契约(那是真 LLM 契约测试的事)。"""
import copy
import json

import pytest

from tools.llm import ref_card as rc
from tools.llm import ref_unit as ru
from tools.llm import service
from tools.llm.fake import FakeLLM
from tools.llm.gate import strict_schema_problems
from tools.llm.service import LLMBackend
from tools.refs import creation, fingerprint, trends

EP = {1: "沈清辞推开门顾长风说你终于来了我等了你整整三年今天就把账算清楚",
      2: "顾长风跪在雪地里沈清辞冷笑一声当年的事你以为没人知道吗来人把他拖下去"}
REVIEWS = [{"text": "女主好飒", "rating": 5, "likes": 3, "at": "2026-11-01"},
           {"text": "第二集太解气了,渣男活该", "rating": 5, "likes": 9, "at": "2026-11-02"},
           {"text": "结局猜到了", "rating": 3, "likes": 0, "at": "2026-11-02"}]
JPG = b"\xff\xd8\xff\xe0fake-jpeg"


def _write(root, rel, data):
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def _content(c, kind, rel, unit, *, verdict="ok", reason="", meta=None):
    return c.execute("INSERT INTO ref_content (work_id, kind, unit, path, sha256, origin, verdict, reason, fetched_at, due_at,"
                     " hard_due_at, meta_json) VALUES (1, ?, ?, ?, 's', 'o', ?, ?, 't', 't', 't', ?)",
                     (kind, unit, rel, verdict, reason, json.dumps(meta or {}))).lastrowid


def _seed(refs, root, heats=(("2026-11-01", 100.0), ("2026-11-02", 200.0))):
    """一部红果剧:第 1、2 集转写(各带一张关键帧)、第 3 集录屏检查没过;一份剧评;详情热度。原文都进永久指纹。"""
    with refs.write() as c:
        c.execute("INSERT INTO ref_source (key) VALUES ('hongguo')")
        c.execute("INSERT INTO ref_work (id, source, external_id, title, tags_json, roles_json, first_seen)"
                  " VALUES (1, 'hongguo', '7', '掌上珠', '[\"古风爱情\",\"复仇\"]', '[\"沈清辞\",\"顾长风\"]', 't')")
    ids = {}
    for n, text in EP.items():
        chars = [[ch, round(i * 0.5, 2), round(i * 0.5 + 0.4, 2)] for i, ch in enumerate(text)]
        rel = f"content/hongguo/7/ep{n}-x.transcript.json"
        _write(root, rel, json.dumps({"text": text, "chars": chars}, ensure_ascii=False))
        _write(root, f"content/hongguo/7/ep{n}-x-kf/1.jpg", JPG)
        with refs.write() as c:
            ids[n] = _content(c, "transcript", rel, n)
            _content(c, "keyframe", f"content/hongguo/7/ep{n}-x-kf/1.jpg", n, meta={"sec": 1.0})
        fingerprint.add(refs, ids[n], text)
    _write(root, "content/hongguo/7/reviews-1-x.jsonl", "\n".join(json.dumps(r, ensure_ascii=False) for r in REVIEWS))
    with refs.write() as c:
        _content(c, "transcript", "content/hongguo/7/ep3-x.transcript.json", 3, verdict="rejected", reason="静音")
        rid = _content(c, "review", "content/hongguo/7/reviews-1-x.jsonl", None)
        for day, h in heats:
            c.execute("INSERT INTO ref_heat (work_id, board, day, at, heat, raw_json) VALUES (1, 'detail', ?, 't', ?, ?)",
                      (day, h, json.dumps({"reviews_total": 142})))
    fingerprint.add(refs, rid, "\n".join(r["text"] for r in REVIEWS))
    return ids


UNIT_ARGS = {
    1: {"hook": "有", "reversal": "小", "emotion": "压", "paywall": "否", "summary": "女主上门,男人说等了她多年。",
        "quote": "你终于来了我等了你", "names": ["沈清辞", "顾长风"], "visual_hook": "有", "visual_sec": 1.0},
    2: {"hook": "有", "reversal": "大", "emotion": "放", "paywall": "是", "summary": "男人跪地求饶,女主翻出旧账。",
        "quote": "当年的事你以为没人知道吗", "names": ["顾长风", "沈清辞"], "visual_hook": "无", "visual_sec": 1.0},
}


NO_PERIOD = {"from": "", "to": ""}


def _card_args(**kw):
    return {"genre": "重生复仇", "payoff": "复仇", "conflict": "情感背叛", "hook_type": "直接冲突",
            "persona": {"protagonist": {"label": "隐忍归来的女子", "contrast": "沈清辞柔弱外表下步步为营"},
                        "antagonist": {"label": "负心人", "contrast": "人前深情人后薄情"}},
            "cast_types": [{"name": "沈清辞", "type": "女主"}, {"name": "顾长风", "type": "负心男主"}],
            "hypotheses": [
                {"claim": "开场沈清辞直接上门对峙,冲突前置", "evidence": "剧情", "units": [1], "review_ids": [], "quote": "你终于来了我等了你",
                 "period": NO_PERIOD},
                {"claim": "观众爱看顾长风被反杀", "evidence": "剧评", "units": [], "review_ids": [2], "quote": "第二集太解气了",
                 "period": NO_PERIOD},
                {"claim": "上线后热度涨得快", "evidence": "热度", "units": [], "review_ids": [], "quote": "",
                 "period": {"from": "2026-11-01", "to": "2026-11-02"}}],
            "techniques": ["开场一分钟内让主角与对手正面对峙"], "not_borrowable": ["顾长风雪地下跪的标志性场面"], **kw}


DECISION = {"adopt": [0, 2], "reject": [1], "extra_not_borrowable": ["沈清辞的名字"]}


def _backend(tool, args, mode="real"):
    return LLMBackend(FakeLLM({tool: args}), "m", mode)     # 确定性替身,不声称覆盖 LLM 契约


def _units(refs, root, ids, which=(1, 2), args=UNIT_ARGS):
    out = {}
    for n in which:
        llm = _backend(ru.TOOL, args[n])
        rid, _ = service.begin(refs, "ref_unit", ids[n], {}, llm, data_root=root)
        assert service.finish(refs, rid, llm, root / "journal", data_root=root) is None
        out[n] = rid
    return out


HEAT_UNTIL = "2026-11-02"            # 卡运行的热度截止日(_seed 的热度到这一天)


def _card(refs, root, args=None, heat_until=HEAT_UNTIL):
    llm = _backend(rc.TOOL, args or _card_args())
    rid, reused = service.begin(refs, "ref_card", 1, {"heat_until": heat_until}, llm, data_root=root)
    assert not reused
    assert service.finish(refs, rid, llm, root / "journal", data_root=root) is None
    return rid


def _state(refs, rid):
    return refs.conn.execute("SELECT state FROM llm_run WHERE id = ?", (rid,)).fetchone()[0]


# ---------- 上下文 ----------

def test_context_refuses_missing_units(refs, tmp_path):
    ids = _seed(refs, tmp_path)
    with pytest.raises(ValueError, match="第 1–2 集还没有单元标注"):
        rc.context(refs, 1, tmp_path, heat_until=HEAT_UNTIL)
    _units(refs, tmp_path, ids, which=(1,))
    with pytest.raises(ValueError, match="第 2 集还没有单元标注"):
        rc.context(refs, 1, tmp_path, heat_until=HEAT_UNTIL)


def test_context_carries_units_reviews_heat_and_coverage(refs, tmp_path):
    ids = _seed(refs, tmp_path)
    runs_ = _units(refs, tmp_path, ids)
    ctx = rc.context(refs, 1, tmp_path, heat_until=HEAT_UNTIL)
    assert [(u["unit"], u["run_id"], u["state"]) for u in ctx["units"]] == [(1, runs_[1], "proposed"), (2, runs_[2], "proposed")]
    assert ctx["names"] == ["沈清辞", "顾长风"] and ctx["covered"] == [1, 2]
    assert [r["id"] for r in ctx["reviews"]] == [1, 2, 3]
    assert ctx["coverage"] == "第 1–2 集 + 剧评 3 条(平台共 142 条);后面的集没看过;第 3 集没进拆解(静音)"
    h = ctx["heat"]
    assert h["history"] and h["peak"] == {"day": "2026-11-02", "heat": 200.0}
    assert h["last_delta"] == {"day": "2026-11-02", "delta": 100.0} and h["rises"][0]["why"] == "热度涨 100%"
    assert ctx["pacing"]["units"] == 2 and ctx["pacing"]["reversals"] == 2
    sch = rc.schema(ctx)
    assert strict_schema_problems(sch) == []
    assert "古装甜宠" in sch["properties"]["genre"]["enum"] and "无限流" in sch["properties"]["genre"]["enum"]
    msg = rc.user_message(ctx)
    assert EP[1] in msg and "2. 第二集太解气了,渣男活该" in msg and "热度涨 100%" in msg and "沈清辞、顾长风" in msg
    assert "2026-11-01" not in msg.split("<reviews>")[1].split("</reviews>")[0]          # 剧评只带正文,不带时间昵称


def test_context_requires_heat_until_and_cuts_heat_at_it(refs, tmp_path):
    ids = _seed(refs, tmp_path)
    _units(refs, tmp_path, ids)
    with pytest.raises(ValueError, match="热度截止日"):
        rc.context(refs, 1, tmp_path)
    h = rc.context(refs, 1, tmp_path, heat_until="2026-11-01")["heat"]
    assert not h["history"] and h["rises"] == []                    # 只算到 11-01:只见过一天


def test_next_day_heat_does_not_change_the_card_input(refs, tmp_path):
    """审查 I1:热度进 input_sha,出卡第二天 monitor 一拉榜,卡就批不了。截止日记进运行参数,之后的热度不算。"""
    ids = _seed(refs, tmp_path)
    _units(refs, tmp_path, ids)
    rid = _card(refs, tmp_path)
    with refs.write() as c:
        c.execute("INSERT INTO ref_heat (work_id, board, day, at, rank, raw_json) VALUES (1, 'hot', '2026-11-03', 't', 5, '{}')")
        c.execute("INSERT INTO ref_heat (work_id, board, day, at, heat, raw_json) VALUES (1, 'detail', '2026-11-03', 't', 900, ?)",
                  (json.dumps({"reviews_total": 300}),))
    service.approve(refs, rid, data_root=tmp_path, decision=DECISION)
    assert _state(refs, rid) == "approved"


def test_same_day_heat_after_card_does_not_change_the_card_input(refs, tmp_path):
    """截止日是开卡当天:同一天开卡之后再拉到的热度(新榜单 / 当天详情)也不许改卡的输入 —— 开卡时记下热度行号上限。"""
    ids = _seed(refs, tmp_path)
    _units(refs, tmp_path, ids)
    top = refs.conn.execute("SELECT MAX(id) FROM ref_heat").fetchone()[0]
    llm = _backend(rc.TOOL, _card_args())
    rid, _ = service.begin(refs, "ref_card", 1, {"heat_until": HEAT_UNTIL, "heat_max_id": top}, llm, data_root=tmp_path)
    assert service.finish(refs, rid, llm, tmp_path / "journal", data_root=tmp_path) is None
    with refs.write() as c:
        c.execute("INSERT INTO ref_heat (work_id, board, day, at, rank, raw_json) VALUES (1, 'new', ?, 't', 3, '{}')", (HEAT_UNTIL,))
    service.approve(refs, rid, data_root=tmp_path, decision=DECISION)
    assert _state(refs, rid) == "approved"


def test_changed_input_error_uses_reference_library_wording(refs, tmp_path):
    ids = _seed(refs, tmp_path)
    _units(refs, tmp_path, ids)
    rid = _card(refs, tmp_path)
    _write(tmp_path, "content/hongguo/7/reviews-1-x.jsonl", json.dumps({"text": "换了一条剧评"}, ensure_ascii=False))
    with pytest.raises(ValueError, match="原文 / 单元标注 / 剧评 / 关键帧") as e:
        service.approve(refs, rid, data_root=tmp_path, decision=DECISION)
    assert "剧本" not in str(e.value)


# ---------- 判卷 ----------

def _judge(refs, root):
    ids = _seed(refs, root)
    _units(refs, root, ids)
    return rc.judge_factory(rc.context(refs, 1, root, heat_until=HEAT_UNTIL))


def test_judge_passes_good_card(refs, tmp_path):
    assert _judge(refs, tmp_path)(_card_args()) == []


def test_judge_sends_back_bad_cast_hypotheses_and_lists(refs, tmp_path):
    judge = _judge(refs, tmp_path)
    a = _card_args()
    assert any("漏了" in p and "顾长风" in p for p in judge({**a, "cast_types": a["cast_types"][:1]}))
    assert any("单元标注里没有" in p and "林婉" in p for p in judge({**a, "cast_types": [*a["cast_types"], {"name": "林婉", "type": "女配"}]}))
    assert any("重复" in p for p in judge({**a, "cast_types": [*a["cast_types"], {"name": "沈清辞", "type": "女二"}]}))

    def hyp(i, **kw):
        b = copy.deepcopy(a)
        b["hypotheses"][i].update(kw)
        return judge(b)
    assert any("不在依据范围内" in p for p in hyp(0, units=[3]))
    assert any("没写是哪几集" in p for p in hyp(0, units=[]))
    assert any("原文里找不到" in p for p in hyp(0, quote="我要你血债血偿"))
    assert any("原文里找不到" in p for p in hyp(0, units=[2]))                        # 引用在第 1 集,说成第 2 集
    assert hyp(0, quote="你中于来了我等了你") == []                                          # 转写按拼音核:同音字算对
    assert any("不存在" in p for p in hyp(1, review_ids=[9]))
    assert any("不在第 2 条剧评里" in p for p in hyp(1, quote="女主好飒女主好飒"))
    assert hyp(2, quote="随便写") == []
    assert any("太短" in p for p in hyp(0, quote="你终于来"))                         # 审查 M2:引用至少 6 个字 / 音节
    assert any("太短" in p for p in hyp(1, quote="女主好飒"))                                              # 热度类不回炉
    assert any("3–6 条" in p for p in judge({**a, "hypotheses": a["hypotheses"][:2]}))
    assert any("techniques" in p for p in judge({**a, "techniques": []}))
    assert any("not_borrowable" in p for p in judge({**a, "not_borrowable": ["  "]}))


def test_heat_hypothesis_period_is_closed_and_checked(refs, tmp_path):
    """审查 I6:热度类假设要写区间(只能选有热度数据的日子);没有热度历史时热度类不可选。"""
    ids = _seed(refs, tmp_path)
    _units(refs, tmp_path, ids)
    ctx = rc.context(refs, 1, tmp_path, heat_until=HEAT_UNTIL)
    item = rc.schema(ctx)["properties"]["hypotheses"]["items"]["properties"]
    assert item["evidence"]["enum"] == ["剧情", "剧评", "热度"]
    assert item["period"]["properties"]["from"]["enum"] == ["", "2026-11-01", "2026-11-02"]
    assert "有热度数据的日子:2026-11-01、2026-11-02" in rc.user_message(ctx)
    one = rc.context(refs, 1, tmp_path, heat_until="2026-11-01")              # 只见过一天 = 无历史
    item = rc.schema(one)["properties"]["hypotheses"]["items"]["properties"]
    assert item["evidence"]["enum"] == ["剧情", "剧评"] and item["period"]["properties"]["to"]["enum"] == [""]

    judge = rc.judge_factory(ctx)
    a = _card_args()

    def hyp(i, period):
        b = copy.deepcopy(a)
        b["hypotheses"][i]["period"] = period
        return judge(b)
    assert any("第 3 条" in p and "区间" in p for p in hyp(2, NO_PERIOD))
    assert any("早于" in p for p in hyp(2, {"from": "2026-11-02", "to": "2026-11-01"}))
    assert any("早于" in p for p in hyp(2, {"from": "2026-11-02", "to": "2026-11-02"}))
    assert any("第 1 条" in p and "不是热度类" in p for p in hyp(0, {"from": "2026-11-01", "to": "2026-11-02"}))
    with refs.write() as c:                                                    # 11-03 只有 hot 榜、11-02 只有 detail:两端对不上
        c.execute("INSERT INTO ref_heat (work_id, board, day, at, rank, raw_json) VALUES (1, 'hot', '2026-11-03', 't', 5, '{}')")
    ctx3 = rc.context(refs, 1, tmp_path, heat_until="2026-11-03")
    b = copy.deepcopy(a)
    b["hypotheses"][2]["period"] = {"from": "2026-11-02", "to": "2026-11-03"}
    assert any("同一个榜单" in p for p in rc.judge_factory(ctx3)(b))


def test_review_hypothesis_may_go_beyond_coverage(refs, tmp_path):
    judge = _judge(refs, tmp_path)
    a = _card_args()
    a["hypotheses"][1]["units"] = [30]                     # 剧评说到第 30 集:可以,批准时标「据剧评(二手)」
    assert judge(a) == []


# ---------- 人的选择 ----------

def test_decision_problems_strict():
    a = _card_args()
    assert rc.decision_problems(a, DECISION) == []
    assert rc.decision_problems(a, {"adopt": [0, 1, 2]}) == []
    assert any("还没表态" in p for p in rc.decision_problems(a, {"adopt": [0]}))
    assert any("既采纳又驳回" in p for p in rc.decision_problems(a, {"adopt": [0, 1, 2], "reject": [1]}))
    assert any("不存在" in p for p in rc.decision_problems(a, {"adopt": [0, 1, 2, 3]}))
    assert any("不是序号" in p for p in rc.decision_problems(a, {"adopt": [0, 1, True]}))
    assert any("重复" in p for p in rc.decision_problems(a, {"adopt": [0, 0, 1, 2]}))
    assert any("空的" in p for p in rc.decision_problems(a, {"adopt": [0, 1, 2], "extra_not_borrowable": [" "]}))
    assert any("60 字" in p for p in rc.decision_problems(a, {"adopt": [0, 1, 2], "extra_not_borrowable": ["字" * 61]}))
    assert any("不认识" in p for p in rc.decision_problems(a, {"adopt": [0, 1, 2], "note": "x"}))


def test_decision_can_drop_techniques_and_not_borrowable_strictly():
    """审查 I2c:人可以逐条去掉可借鉴 / 不可借鉴(序号严格校验)。"""
    a = _card_args(techniques=["甲", "乙"])
    ok = {"adopt": [0, 1, 2], "reject_techniques": [1], "reject_not_borrowable": [0]}
    assert rc.decision_problems(a, ok) == []
    assert any("可借鉴" in p and "不存在" in p for p in rc.decision_problems(a, {**ok, "reject_techniques": [2]}))
    assert any("不可借鉴" in p and "不存在" in p for p in rc.decision_problems(a, {**ok, "reject_not_borrowable": [1]}))
    assert any("重复" in p for p in rc.decision_problems(a, {**ok, "reject_techniques": [0, 0]}))
    assert any("不是序号" in p for p in rc.decision_problems(a, {**ok, "reject_techniques": ["0"]}))
    assert any("序号列表" in p for p in rc.decision_problems(a, {**ok, "reject_not_borrowable": 0}))


# ---------- 批准 ----------

def test_approve_cascades_units_and_writes_card(refs, tmp_path):
    ids = _seed(refs, tmp_path)
    u = _units(refs, tmp_path, ids)
    service.approve(refs, u[1], data_root=tmp_path)            # 第 1 集已经批过:连带批准时跳过,不重写
    rid = _card(refs, tmp_path)
    with pytest.raises(ValueError, match="先做选择"):
        service.approve(refs, rid, data_root=tmp_path)
    service.approve(refs, rid, data_root=tmp_path, decision=DECISION)
    assert _state(refs, rid) == _state(refs, u[1]) == _state(refs, u[2]) == "approved"
    rows = refs.conn.execute("SELECT content_id, run_id FROM ref_unit ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [(ids[1], u[1]), (ids[2], u[2])]

    card = refs.conn.execute("SELECT * FROM ref_card").fetchone()
    assert (card["work_id"], card["run_id"], card["version"], card["state"]) == (1, rid, 1, "approved")
    assert json.loads(card["decision_json"]) == DECISION
    assert card["coverage"].startswith("第 1–2 集 + 剧评 3 条")
    review = json.loads(card["review_json"])
    h = review["hypotheses"]
    assert h[0]["check"]["unit"] == 1 and h[0]["check"]["restored"] == "你终于来了我等了你" and h[0]["check"]["at"] == 5.0
    assert h[1]["label"] == "据剧评(二手)" and h[1]["check"]["text"] == REVIEWS[1]["text"]
    assert h[2]["label"].startswith("与数据相符")
    view = json.loads(card["creation_json"])
    assert card["creation_sha"] == creation.sha(view)
    assert [x["claim"] for x in view["hypotheses"]] == ["开场女主直接上门对峙,冲突前置", "上线后热度涨得快"]   # 驳回的不进
    blob = json.dumps(view, ensure_ascii=False)
    for gone in ("沈清辞", "顾长风", "你终于来了", "第二集太解气了", "quote", "review_ids", "掌上珠"):
        assert gone not in blob
    assert view["not_borrowable"] == ["负心男主雪地下跪的标志性场面", "女主的名字"]
    assert view["persona"]["protagonist"]["contrast"] == "女主柔弱外表下步步为营"
    assert view["cast_types"] == ["女主", "负心男主"] and view["genre_premise"] == "重生复仇"
    assert trends.by_month(refs)[0]["genre"]["重生复仇"] == 1                         # 卡的字段名对得上趋势统计


def test_heat_claim_without_rise_is_marked_mismatch(refs, tmp_path):
    ids = _seed(refs, tmp_path, heats=(("2026-11-01", 100.0), ("2026-11-02", 101.0)))
    _units(refs, tmp_path, ids)
    rid = _card(refs, tmp_path)
    service.approve(refs, rid, data_root=tmp_path, decision=DECISION)
    view = json.loads(refs.conn.execute("SELECT creation_json FROM ref_card").fetchone()[0])
    assert view["hypotheses"][1]["label"].startswith("与数据不符")


def test_heat_claim_is_checked_on_its_own_period_not_any_day(refs, tmp_path):
    """11-02 涨过一倍,但假设引的区间 11-02–11-03 是跌的:标「与数据不符」(以前只要库里任何一天涨过就算相符)。"""
    ids = _seed(refs, tmp_path, heats=(("2026-11-01", 100.0), ("2026-11-02", 200.0), ("2026-11-03", 100.0)))
    _units(refs, tmp_path, ids)
    a = _card_args()
    a["hypotheses"][2]["period"] = {"from": "2026-11-02", "to": "2026-11-03"}
    rid = _card(refs, tmp_path, a, heat_until="2026-11-03")
    service.approve(refs, rid, data_root=tmp_path, decision=DECISION)
    label = json.loads(refs.conn.execute("SELECT review_json FROM ref_card").fetchone()[0])["hypotheses"][2]["label"]
    assert label.startswith("与数据不符") and "detail 热度从 200 到 100" in label and "跌" in label


def test_heat_claim_without_direction_asks_a_person(refs, tmp_path):
    ids = _seed(refs, tmp_path)
    _units(refs, tmp_path, ids)
    a = _card_args()
    a["hypotheses"][2]["claim"] = "热度曲线说明观众留得住"
    rid = _card(refs, tmp_path, a)
    service.approve(refs, rid, data_root=tmp_path, decision=DECISION)
    label = json.loads(refs.conn.execute("SELECT review_json FROM ref_card").fetchone()[0])["hypotheses"][2]["label"]
    assert label.startswith("请人核对:区间 2026-11-01–2026-11-02 内") and "detail 热度从 100 到 200" in label


def test_unit_that_cannot_be_approved_blocks_whole_card(refs, tmp_path):
    ids = _seed(refs, tmp_path)
    u = _units(refs, tmp_path, ids)
    rid = _card(refs, tmp_path)
    _write(tmp_path, "content/hongguo/7/ep2-x-kf/1.jpg", JPG + b"changed")   # 第 2 集关键帧换了:卡的输入不变,单元的变了
    with pytest.raises(ValueError, match="第 2 集的单元标注.*输入已经变了"):
        service.approve(refs, rid, data_root=tmp_path, decision=DECISION)
    assert _state(refs, rid) == _state(refs, u[1]) == _state(refs, u[2]) == "proposed"   # 第 1 集也没被先批掉
    assert refs.conn.execute("SELECT COUNT(*) FROM ref_unit").fetchone()[0] == 0
    assert refs.conn.execute("SELECT COUNT(*) FROM ref_card").fetchone()[0] == 0


def test_twelve_char_overlap_blocks_approval_and_writes_nothing(refs, tmp_path):
    ids = _seed(refs, tmp_path)
    u = _units(refs, tmp_path, ids)
    rid = _card(refs, tmp_path, _card_args(techniques=["反派一开口就是当年的事你以为没人知道吗"]))
    with pytest.raises(ValueError, match="「当年的事你以为没人知道吗」"):
        service.approve(refs, rid, data_root=tmp_path, decision=DECISION)
    assert _state(refs, u[1]) == _state(refs, u[2]) == _state(refs, rid) == "proposed"
    assert refs.conn.execute("SELECT COUNT(*) FROM ref_unit").fetchone()[0] == 0


def test_overlap_in_rejected_hypothesis_does_not_block(refs, tmp_path):
    ids = _seed(refs, tmp_path)
    _units(refs, tmp_path, ids)
    a = _card_args()
    a["hypotheses"][1]["claim"] = "观众说当年的事你以为没人知道吗这句最爽"
    rid = _card(refs, tmp_path, a)
    service.approve(refs, rid, data_root=tmp_path, decision=DECISION)       # 第 2 条驳回了,不进创作视图
    assert _state(refs, rid) == "approved"


def test_new_card_supersedes_old_one(refs, tmp_path):
    ids = _seed(refs, tmp_path)
    _units(refs, tmp_path, ids)
    service.approve(refs, _card(refs, tmp_path), data_root=tmp_path, decision=DECISION)
    _write(tmp_path, "content/hongguo/7/ep1-x-kf/1.jpg", JPG + b"re-recorded")    # 第 1 集换了关键帧,重拆
    redo = _units(refs, tmp_path, ids, which=(1,), args={1: {**UNIT_ARGS[1], "summary": "女主登门赴一个多年的约。"}})
    rid2 = _card(refs, tmp_path)
    service.approve(refs, rid2, data_root=tmp_path, decision={"adopt": [0, 1, 2]})
    cards = refs.conn.execute("SELECT version, state, run_id FROM ref_card ORDER BY version").fetchall()
    assert [(c["version"], c["state"]) for c in cards] == [(1, "superseded"), (2, "approved")] and cards[1]["run_id"] == rid2
    assert _state(refs, redo[1]) == "approved"
    assert refs.conn.execute("SELECT COUNT(*) FROM ref_unit").fetchone()[0] == 3     # 只许追加:重拆的第 1 集是新行
    assert len(trends.by_month(refs)) == 1 and trends.by_month(refs)[0]["n"] == 1    # 被取代的不算


def test_work_title_in_techniques_blocks_until_that_item_is_dropped(refs, tmp_path):
    """审查 I2a/c:创作视图里出现作品名(含去掉书名号的写法)就拦;人把那条「去掉」后能批,去掉的不进创作视图。"""
    ids = _seed(refs, tmp_path)
    _units(refs, tmp_path, ids)
    rid = _card(refs, tmp_path, _card_args(techniques=["学《掌上珠》用信物贯穿全剧", "开场一分钟内让主角与对手正面对峙"]))
    with pytest.raises(ValueError, match="掌上珠"):
        service.approve(refs, rid, data_root=tmp_path, decision=DECISION)
    assert _state(refs, rid) == "proposed" and refs.conn.execute("SELECT COUNT(*) FROM ref_unit").fetchone()[0] == 0
    service.approve(refs, rid, data_root=tmp_path, decision={**DECISION, "reject_techniques": [0]})
    view = json.loads(refs.conn.execute("SELECT creation_json FROM ref_card").fetchone()[0])
    assert view["techniques"] == ["开场一分钟内让主角与对手正面对峙"]


def test_overlap_is_checked_before_renaming_and_across_fields(refs, tmp_path):
    """审查 M3:换名前也比一遍(名字换掉后对不上原文的照抄照样拦);所有段落连起来再比一次(拆成两条的照抄也拦)。"""
    ids = _seed(refs, tmp_path)
    _units(refs, tmp_path, ids)
    rid = _card(refs, tmp_path, _card_args(techniques=["沈清辞推开门顾长风说你终于"]))
    with pytest.raises(ValueError, match="「沈清辞推开门顾长风说你终于」"):
        service.approve(refs, rid, data_root=tmp_path, decision=DECISION)
    rid = _card(refs, tmp_path, _card_args(techniques=["当年的事你以为", "没人知道吗来人"]))
    with pytest.raises(ValueError, match="全部连起来"):
        service.approve(refs, rid, data_root=tmp_path, decision=DECISION)


def test_fake_card_cannot_be_approved(refs, tmp_path):
    ids = _seed(refs, tmp_path)
    _units(refs, tmp_path, ids)
    llm = _backend(rc.TOOL, _card_args(), mode="fake")
    rid, _ = service.begin(refs, "ref_card", 1, {"heat_until": HEAT_UNTIL}, llm, data_root=tmp_path)
    service.finish(refs, rid, llm, tmp_path / "journal", data_root=tmp_path)
    with pytest.raises(ValueError, match="假 LLM"):
        service.approve(refs, rid, data_root=tmp_path, decision=DECISION)


def test_registered_and_never_touches_drama_tables(refs, tmp_path):
    sp = service.TASKS["ref_card"]
    assert sp.module is rc and sp.target == "ref_work" and sp.needs_root and sp.model is None
    assert (sp.max_tokens, sp.max_rounds, sp.resets_segments) == (8000, 4, False)
    assert not service.TASKS["ref_unit"].resets_segments
    tables = {r[0] for r in refs.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "segment" not in tables and "episode" not in tables          # 碰剧库表就会 no such table
    ids = _seed(refs, tmp_path)
    _units(refs, tmp_path, ids)
    rid = _card(refs, tmp_path)
    assert service.reset_cost(refs, rid) is None
    service.approve(refs, rid, data_root=tmp_path, decision=DECISION)
    assert _state(refs, rid) == "approved"


# ---------- 创作视图(纯函数)----------

def _review(**kw):
    return {"source": "hongguo", "genre": "仙侠", "payoff": "逆袭", "conflict": "误会", "hook_type": "强悬念",
            "persona": {"protagonist": {"label": "废柴弟子", "contrast": "沈清被人看不起"}, "antagonist": {"label": "师兄", "contrast": "笑里藏刀"}},
            "cast_types": [{"name": "沈清", "type": "师妹"}, {"name": "沈清辞", "type": "女主"}],
            "hypotheses": [{"claim": "沈清辞每集都被沈清陷害", "evidence": "剧情", "units": [1], "review_ids": [], "quote": "原话", "label": ""}],
            "techniques": ["每集结尾给主角一个新麻烦"], "not_borrowable": ["灵根设定"], "names": ["沈清辞", "沈清"], "roles": [],
            "pacing": {"units": 1}, "contrast": {"rows": [], "note": "无对照"}, "coverage": "第 1 集", **kw}


def test_view_replaces_longer_names_first_and_drops_quotes():
    v = creation.view(_review(), {"adopt": [0], "reject": []})
    assert v["hypotheses"] == [{"claim": "女主每集都被师妹陷害", "evidence": "剧情", "label": ""}]
    assert v["persona"]["protagonist"]["contrast"] == "师妹被人看不起"
    assert v["genre_premise"] == "其他" and v["format"] == creation.FORMAT
    assert creation.sha(v) == creation.sha(json.loads(json.dumps(v)))


def test_view_refuses_leftover_names_but_not_types_containing_a_name():
    with pytest.raises(ValueError, match="林婉"):
        creation.view(_review(roles=["林婉"], techniques=["林婉出场要早"]), {"adopt": [0]})
    # 类型里恰好含名字(「沈清」⊂「沈清的师父」)不算残留
    v = creation.view(_review(cast_types=[{"name": "沈清", "type": "沈清的师父"}, {"name": "沈清辞", "type": "女主"}]), {"adopt": [0]})
    assert v["hypotheses"][0]["claim"] == "女主每集都被沈清的师父陷害"


def test_gate_lists_overlapping_fragments(refs):
    with refs.write() as c:
        c.execute("INSERT INTO ref_source (key) VALUES ('hongguo')")
        c.execute("INSERT INTO ref_work (id, source, external_id, first_seen) VALUES (1, 'hongguo', '7', 't')")
        cid = _content(c, "transcript", "p", 1)
    fingerprint.add(refs, cid, EP[2])
    v = creation.view(_review(techniques=["比如:当年的事你以为没人知道吗!", "当年的事你以为没人"]), {"adopt": [0]})
    hits = creation.gate(refs, v)
    assert hits == [{"field": "techniques[0]", "text": "当年的事你以为没人知道吗", "content_id": cid}]


def test_frequent_fragments_finds_repeated_proper_nouns_but_not_common_words():
    texts = ["青云宗弟子下山,青云宗长老大怒。", "你是青云宗的人?我们已经知道了,我们已经知道了,我们已经知道了。"]
    got = creation.frequent_fragments(texts, min_count=3, exclude=["复仇"])
    assert {"青云宗", "青云", "云宗"} <= set(got)
    assert not {"我们", "已经", "知道", "已经知道"} & set(got)                       # 代词 / 虚词 / 常见动词不算专名
    assert creation.frequent_fragments(["复仇复仇复仇"], min_count=3, exclude=["复仇"]) == []   # 闭集取值里有的不算


def test_frequent_fragments_min_len_drops_two_char_words():
    texts = ["青云宗弟子下山,将军来了。", "青云宗长老见将军。", "青云宗的将军走了。"]
    assert "将军" in creation.frequent_fragments(texts, min_count=3)
    got = creation.frequent_fragments(texts, min_count=3, min_len=3)
    assert "青云宗" in got and "将军" not in got and all(len(g) >= 3 for g in got)


def test_view_refuses_frequent_source_fragments_and_work_title():
    with pytest.raises(ValueError, match="青云宗"):
        creation.view(_review(techniques=["学青云宗的门规设定"]), {"adopt": [0]}, proper=["青云宗", "青云"])
    with pytest.raises(ValueError, match="掌上珠"):
        creation.view(_review(title="《掌上珠》", techniques=["像掌上珠那样用信物"]), {"adopt": [0]})
    v = creation.view(_review(techniques=["学青云宗的门规设定"]), {"adopt": [0], "reject_techniques": [0]}, proper=["青云宗"])
    assert v["techniques"] == []


def test_frequent_fragments_in_persona_pass_once_a_human_acknowledges_them():
    """真机(运行 8):高频片段启发式把「将军」「找到」「瞎子」这类常用词也当专名;人设 / 角色类型没有「去掉」入口,
    卡永远批不了。高频片段改成提示:人勾「看过了,不是专名」才放行;名字残留照样硬拦。"""
    rv = _review(persona={"protagonist": {"label": "装瞎自保的将军孤女", "contrast": "柔弱外表"},
                          "antagonist": {"label": "逼婚的亲戚", "contrast": "以亲情为名"}})
    with pytest.raises(ValueError, match="看过了"):
        creation.view(rv, {"adopt": [0]}, proper=["将军"])
    assert creation.fragments(rv, {"adopt": [0]}, proper=["将军", "青云宗"]) == ["将军"]
    v = creation.view(rv, {"adopt": [0], "ack_fragments": True}, proper=["将军"])
    assert v["persona"]["protagonist"]["label"] == "装瞎自保的将军孤女"
    with pytest.raises(ValueError, match="沈清辞"):                                   # 名字残留不因勾选放行
        creation.view(_review(roles=["沈清辞"], techniques=["沈清辞出场要早"], cast_types=[]),
                      {"adopt": [0], "ack_fragments": True}, proper=["将军"])


def test_fragments_list_only_the_longest_not_their_pieces():
    """真机(《太上无情》):「蛟龙渡劫」连带「蛟龙渡」「龙渡劫」「蛟龙」都列出来,36 个里一半是半截,人看不过来。只列最长的。"""
    rv = _review(techniques=["插入蛟龙渡劫的奇观", "海东青送信"])
    got = creation.fragments(rv, {"adopt": [0]}, proper=["蛟龙渡劫", "蛟龙渡", "龙渡劫", "蛟龙", "海东青", "东青", "海东"])
    assert got == ["蛟龙渡劫", "海东青"]


def test_ack_fragments_must_be_a_bool():
    args = _card_args()
    n = len(args["hypotheses"])
    base = {"adopt": list(range(n)), "reject": []}
    assert rc.decision_problems(args, {**base, "ack_fragments": True}) == []
    assert any("ack_fragments" in p or "看过了" in p for p in rc.decision_problems(args, {**base, "ack_fragments": "yes"}))


def test_view_renames_homophones_of_cast_names():
    """审查 I2b:平台角色表与转写里的名字按拼音对齐,同音的当别名换成同一个类型。"""
    v = creation.view(_review(roles=["沈清词"], techniques=["沈清词出场要早"]), {"adopt": [0]})
    assert v["techniques"] == ["女主出场要早"]


SECT = "林墨背着剑上了青云宗。青云宗的长老看着他说:你就是那个被逐出青云宗的弃徒?林墨没有回答,只把令牌放在桌上。"


def test_sect_name_repeated_in_the_source_blocks_the_card(refs, tmp_path):
    """审查 I2a:原文里反复出现(≥ PROPER_MIN 次)、又不是常用词的片段(门派名)出现在创作视图里就拦,报出片段。"""
    with refs.write() as c:
        c.execute("INSERT INTO ref_source (key) VALUES ('fanqie')")
        c.execute("INSERT INTO ref_work (id, source, external_id, title, first_seen) VALUES (1, 'fanqie', 'b', '剑归', 't')")
        _write(tmp_path, "content/fanqie/b/ch1.txt", SECT)
        cid = _content(c, "chapter", "content/fanqie/b/ch1.txt", 1)
    fingerprint.add(refs, cid, SECT)
    unit = {"hook": "有", "reversal": "小", "emotion": "压", "paywall": "是", "summary": "弃徒回到旧地,被长老当面奚落。",
            "quote": "只把令牌放在桌上", "names": ["林墨"]}
    llm = _backend(ru.TOOL, unit)
    rid, _ = service.begin(refs, "ref_unit", cid, {}, llm, data_root=tmp_path)
    assert service.finish(refs, rid, llm, tmp_path / "journal", data_root=tmp_path) is None
    plot = {"evidence": "剧情", "units": [1], "review_ids": [], "quote": "只把令牌放在桌上", "period": NO_PERIOD}
    args = _card_args(cast_types=[{"name": "林墨", "type": "男主"}],
                      hypotheses=[{**plot, "claim": c} for c in ("弃徒回归开局", "沉默代替辩解", "信物压轴")],
                      techniques=["学青云宗那样给主角一个回不去的师门"], not_borrowable=["弃徒令牌这个道具"])
    card = _backend(rc.TOOL, args)
    rid, _ = service.begin(refs, "ref_card", 1, {"heat_until": HEAT_UNTIL}, card, data_root=tmp_path)
    assert service.finish(refs, rid, card, tmp_path / "journal", data_root=tmp_path) is None
    with pytest.raises(ValueError, match="青云宗"):
        service.approve(refs, rid, data_root=tmp_path, decision={"adopt": [0, 1, 2]})
    service.approve(refs, rid, data_root=tmp_path, decision={"adopt": [0, 1, 2], "reject_techniques": [0]})
    assert _state(refs, rid) == "approved"


def test_recheck_failing_after_units_are_approved_leaves_the_card_pending_and_retryable(refs, tmp_path, monkeypatch):
    """审查 M1:连带批准单元之后重算创作视图(冷门对照此时才算进这部)若没过:卡运行仍待审、报清楚,改好后再批准能接着批。"""
    ids = _seed(refs, tmp_path)
    u = _units(refs, tmp_path, ids)
    rid = _card(refs, tmp_path)
    calls = []
    real = creation.gate

    def gate(refs_, *views):
        calls.append(1)
        return [] if len(calls) == 1 else [{"field": "contrast.note", "text": "某段十二个字以上的原文片段", "content_id": 1}]
    monkeypatch.setattr(creation, "gate", gate)
    with pytest.raises(ValueError, match="单元标注已经连带批准") as e:
        service.approve(refs, rid, data_root=tmp_path, decision=DECISION)
    assert "卡运行还是待审" in str(e.value)
    assert _state(refs, rid) == "proposed" and _state(refs, u[1]) == _state(refs, u[2]) == "approved"
    assert refs.conn.execute("SELECT COUNT(*) FROM ref_card").fetchone()[0] == 0
    monkeypatch.setattr(creation, "gate", real)
    service.approve(refs, rid, data_root=tmp_path, decision=DECISION)                # 接着批:已批准的单元跳过
    assert _state(refs, rid) == "approved" and refs.conn.execute("SELECT COUNT(*) FROM ref_unit").fetchone()[0] == 2


def test_card_input_over_the_cap_is_refused_not_truncated(refs, tmp_path, monkeypatch):
    """审查 M9:单元原文合计超过 config.CARD_INPUT_CHARS 就拒(不截断),叫人把「拆前 N 章」调小。"""
    from tools.refs import config
    ids = _seed(refs, tmp_path)
    _units(refs, tmp_path, ids)
    monkeypatch.setattr(config, "CARD_INPUT_CHARS", len(EP[1]) + len(EP[2]) - 1)
    with pytest.raises(ValueError, match="单元原文太长.*拆前 N 章"):
        rc.context(refs, 1, tmp_path, heat_until=HEAT_UNTIL)
    monkeypatch.setattr(config, "CARD_INPUT_CHARS", len(EP[1]) + len(EP[2]))
    assert rc.context(refs, 1, tmp_path, heat_until=HEAT_UNTIL)["units"]


def test_novel_coverage_says_how_many_chapters_are_free(refs, tmp_path):
    """spec 2.1:依据范围写明「第 1 章(共 32 章免费)」,免费章数取最近一次详情(截止日之前)记下的。"""
    with refs.write() as c:
        c.execute("INSERT INTO ref_source (key) VALUES ('fanqie')")
        c.execute("INSERT INTO ref_work (id, source, external_id, title, first_seen) VALUES (1, 'fanqie', 'b', '剑归', 't')")
        _write(tmp_path, "content/fanqie/b/ch1.txt", SECT)
        cid = _content(c, "chapter", "content/fanqie/b/ch1.txt", 1)
        for day, n in (("2026-11-01", 30), ("2026-11-02", 32), ("2026-11-03", 40)):
            c.execute("INSERT INTO ref_heat (work_id, board, day, at, heat, raw_json) VALUES (1, 'detail', ?, 't', 1, ?)",
                      (day, json.dumps({"units_free": n})))
    unit = {"hook": "有", "reversal": "小", "emotion": "压", "paywall": "是", "summary": "弃徒回到旧地,被长老当面奚落。",
            "quote": "只把令牌放在桌上", "names": ["林墨"]}
    llm = _backend(ru.TOOL, unit)
    rid, _ = service.begin(refs, "ref_unit", cid, {}, llm, data_root=tmp_path)
    service.finish(refs, rid, llm, tmp_path / "journal", data_root=tmp_path)
    assert rc.context(refs, 1, tmp_path, heat_until="2026-11-02")["coverage"].startswith("第 1 章(共 32 章免费);后面的章没看过")
