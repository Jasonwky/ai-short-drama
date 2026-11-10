# -*- coding: utf-8 -*-
"""拆解卡面板(spec 2026-11-07-参考库-design.md 2.4、2.5、3.1;计划 1c Task 7):列表、审阅页、批准带 decision、驳回、
重叠拦批准时页面说清楚、创作视图预览不写库、录屏详情 `&t=` 跳转、跨作品趋势、来源设置的 LLM 调用统计。
FakeLLM(mode 用 "real")只当确定性替身产出待审运行,不声称覆盖 LLM 契约。"""
import json

import pytest
from fastapi.testclient import TestClient

from tests.refs.test_ref_card import DECISION, _card, _card_args, _seed, _units
from tools.llm import ref_card as rc
from tools.llm import runs, service
from tools.llm.fake import FakeLLM
from tools.llm.service import LLMBackend
from tools.refs import clock, jobs, settings
from tools.refs.db import RefStore
from tools.serve.app import create_app
from tools.serve.fakes import fake_backend
from tools.serve.runtime import Runtime

FORM = {"hyp-0": "adopt", "hyp-1": "reject", "hyp-2": "adopt", "extra_not_borrowable": "沈清辞的名字\n\n"}


@pytest.fixture
def cc(store, tmp_path):
    refs = RefStore(":memory:")
    root = tmp_path / "refs"
    ids = _seed(refs, root)
    settings.ensure(refs)
    rt = Runtime(store, tmp_path, fake_backend()[0], refs=refs, refs_root=root)
    yield TestClient(create_app(rt)), refs, root, ids
    refs.close()


def _pending(refs, root, ids, args=None):
    u = _units(refs, root, ids)
    return u, _card(refs, root, args)


def _count(refs, table):
    return refs.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_cards_panel_lists_each_work_with_progress_and_buttons(cc):
    c, refs, root, ids = cc
    t = c.get("/refs/cards").text
    assert "data-ref-card-row" in t and "掌上珠" in t and "缺 2" in t
    assert 'action="/refs/work/1/breakdown"' in t and 'href="/refs/card?work=1"' in t
    _, rid = _pending(refs, root, ids)
    t = c.get("/refs/cards").text
    assert "待审 2" in t and f"卡待审(运行 {rid})" in t


def test_breakdown_button_queues_a_human_job_and_refuses_without_units(cc):
    c, refs, root, ids = cc
    r = c.post("/refs/work/1/breakdown", follow_redirects=False)
    assert r.status_code == 303
    j = [x for x in jobs.recent(refs) if x["kind"] == "breakdown"]
    assert len(j) == 1 and j[0]["auto"] == 0 and j[0]["work_id"] == 1
    with refs.write() as cn:
        cn.execute("INSERT INTO ref_work (id, source, external_id, title, first_seen) VALUES (2, 'hongguo', '8', '空的', 't')")
    r = c.post("/refs/work/2/breakdown")
    assert r.status_code == 400 and "没有可拆的集" in r.text


def test_review_page_shows_hypotheses_units_and_choices(cc):
    c, refs, root, ids = cc
    _, rid = _pending(refs, root, ids)
    t = c.get("/refs/card?work=1").text
    for h in range(3):
        assert f'name="hyp-{h}" value="adopt"' in t and f'name="hyp-{h}" value="reject"' in t
    assert "开场沈清辞直接上门对峙" in t and rc.SECONDHAND in t and "第二集太解气了,渣男活该" in t      # 剧评类显示那条剧评
    assert "与数据相符" in t                                                             # 热度类代码核对
    assert t.count("data-unit-row") == 2 and "女主上门,男人说等了她多年。" in t
    assert "无对照" in t and "第 1–2 集 + 剧评 3 条" in t
    kf = refs.conn.execute("SELECT id FROM ref_content WHERE kind = 'keyframe' ORDER BY id LIMIT 1").fetchone()[0]
    assert f'src="/refs/file/{kf}"' in t                                                 # 画面钩子缩略图
    assert f'action="/refs/card/{rid}/approve"' in t and f'action="/refs/card/{rid}/reject"' in t
    assert 'name="extra_not_borrowable"' in t and "data-card-preview" in t


def test_plot_hypothesis_links_to_record_view_at_the_quote_second(cc):
    c, refs, root, ids = cc
    with refs.write() as cn:
        rec = cn.execute("INSERT INTO ref_content (work_id, kind, unit, path, sha256, origin, verdict, fetched_at, due_at,"
                         " hard_due_at) VALUES (1, 'recording', 1, 'content/hongguo/7/ep1-x.webm', 's', 'o', 'ok', 't', 't', 't')").lastrowid
    _pending(refs, root, ids)
    t = c.get("/refs/card?work=1").text
    assert f'href="/refs/record/view?id={rec}&amp;t=5"' in t        # 「你终于来了我等了你」从第 11 个字起,0.5 秒一字


def test_record_view_jumps_to_t(cc):
    c, refs, root, ids = cc
    with refs.write() as cn:
        rec = cn.execute("INSERT INTO ref_content (work_id, kind, unit, path, sha256, origin, verdict, fetched_at, due_at,"
                         " hard_due_at) VALUES (1, 'recording', 1, 'content/hongguo/7/ep1-x.webm', 's', 'o', 'ok', 't', 't', 't')").lastrowid
    assert 'data-start="12.5"' in c.get(f"/refs/record/view?id={rec}&t=12.5").text
    assert "data-start" not in c.get(f"/refs/record/view?id={rec}").text


def test_approve_with_choices_writes_the_card_and_shows_it_locked(cc):
    c, refs, root, ids = cc
    u, rid = _pending(refs, root, ids)
    r = c.post(f"/refs/card/{rid}/approve", data=FORM, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/refs/card?work=1"
    card = refs.conn.execute("SELECT * FROM ref_card").fetchone()
    assert json.loads(card["decision_json"]) == {**DECISION, "reject_techniques": [], "reject_not_borrowable": [],
                                                  "ack_fragments": False}      # 页面总带上「去掉」两项与「看过了」
    assert runs.get(refs, u[1])["state"] == runs.get(refs, u[2])["state"] == "approved"   # 单元运行连带批准
    t = c.get("/refs/card?work=1").text
    assert "data-card-approved" in t and card["creation_sha"] in t and "版本 1" in t
    assert "观众爱看顾长风被反杀" in t and "已驳回" in t and "已采纳" in t
    assert "女主的名字" in t                                       # 创作视图:人补的不可借鉴也换了名
    assert 'name="hyp-0"' not in t                                 # 没有待审的了,不再出表单


def test_approve_without_every_stance_is_a_400_that_says_which(cc):
    c, refs, root, ids = cc
    _, rid = _pending(refs, root, ids)
    r = c.post(f"/refs/card/{rid}/approve", data={"hyp-0": "adopt", "hyp-1": "reject"})
    assert r.status_code == 400 and "第 3 条假设还没表态" in r.text
    assert _count(refs, "ref_card") == 0


def test_overlap_blocks_approval_with_the_fragment_on_the_page(cc):
    c, refs, root, ids = cc
    _, rid = _pending(refs, root, ids, _card_args(techniques=["反派一开口就是当年的事你以为没人知道吗"]))
    r = c.post(f"/refs/card/{rid}/approve", data=FORM)
    assert r.status_code == 400 and "连续 12 字以上相同" in r.text and "「当年的事你以为没人知道吗」" in r.text
    assert _count(refs, "ref_card") == 0 and runs.get(refs, rid)["state"] == "proposed"


def test_fake_run_cannot_be_approved_and_page_says_so(cc):
    c, refs, root, ids = cc
    _units(refs, root, ids)
    llm = LLMBackend(FakeLLM({rc.TOOL: _card_args()}), "m", "fake")
    rid, _ = service.begin(refs, "ref_card", 1, {"heat_until": "2026-11-02"}, llm, data_root=root)
    assert service.finish(refs, rid, llm, root / "journal", data_root=root) is None
    assert "假 LLM" in c.get("/refs/card?work=1").text
    r = c.post(f"/refs/card/{rid}/approve", data=FORM)
    assert r.status_code == 400 and "假 LLM 的产物不许通过审批闸门" in r.text


def test_reject_marks_the_run_rejected(cc):
    c, refs, root, ids = cc
    _, rid = _pending(refs, root, ids)
    r = c.post(f"/refs/card/{rid}/reject", follow_redirects=False)
    assert r.status_code == 303 and runs.get(refs, rid)["state"] == "rejected"


def test_unit_runs_are_not_approved_here(cc):
    c, refs, root, ids = cc
    u, _ = _pending(refs, root, ids)
    r = c.post(f"/refs/card/{u[1]}/approve", data=FORM)
    assert r.status_code == 400 and "随卡连带批准" in r.text


def test_preview_renders_the_creation_view_without_writing(cc):
    c, refs, root, ids = cc
    u, rid = _pending(refs, root, ids)
    r = c.post("/refs/card/preview", data={"run": str(rid), **FORM})
    assert r.status_code == 200
    t = r.text
    assert 'data-adopted="0,2"' in t and "开场女主直接上门对峙,冲突前置" in t      # 换了名
    assert "观众爱看" not in t                                                      # 驳回的不进
    assert "你终于来了" not in t                                                    # 引用去掉
    assert "data-gate-ok" in t
    assert _count(refs, "ref_card") == 0 and _count(refs, "ref_unit") == 0
    assert runs.get(refs, rid)["state"] == runs.get(refs, u[1])["state"] == "proposed"
    t = c.post("/refs/card/preview", data={"run": str(rid), "hyp-0": "adopt"}).text   # 只表态了一条:照样出预览,写明还差什么
    assert 'data-adopted="0"' in t and "第 2 条假设还没表态" in t


def test_preview_shows_overlap_hits(cc):
    c, refs, root, ids = cc
    _, rid = _pending(refs, root, ids, _card_args(techniques=["反派一开口就是当年的事你以为没人知道吗"]))
    t = c.post("/refs/card/preview", data={"run": str(rid), **FORM}).text
    assert "data-gate-hit" in t and "当年的事你以为没人知道吗" in t and "data-gate-ok" not in t


def test_preview_lists_frequent_fragments_and_the_ack_checkbox_lets_them_pass(cc, monkeypatch):
    """高频片段只提示:没勾「看过了」时预览列出来并写明批准会拒;勾了就照常出创作视图。"""
    c, refs, root, ids = cc
    u, rid = _pending(refs, root, ids)
    monkeypatch.setattr(rc, "proper_names", lambda ctx: ["开场"])
    t = c.post("/refs/card/preview", data={"run": str(rid), **FORM}).text
    assert "data-fragments" in t and "开场" in t and "data-gate-name" in t
    t = c.post("/refs/card/preview", data={"run": str(rid), **FORM, "ack_fragments": "1"}).text
    assert "data-fragments" in t and "data-gate-ok" in t and "data-gate-name" not in t
    page = c.get("/refs/card?work=1").text
    assert 'name="ack_fragments"' in page


def test_work_page_has_breakdown_button_and_card_state(cc):
    c, refs, root, ids = cc
    _pending(refs, root, ids)
    t = c.get("/refs/work?id=1").text
    assert 'action="/refs/work/1/breakdown"' in t and "data-ref-breakdown" in t and 'href="/refs/card?work=1"' in t
    assert "卡待审" in t


def test_ranks_page_shows_cross_work_trends(cc):
    c, refs, root, ids = cc
    _, rid = _pending(refs, root, ids)
    service.approve(refs, rid, data_root=root, decision=DECISION)
    t = c.get("/refs/ranks?source=hongguo").text
    assert "跨作品趋势" in t and "data-ref-trend-row" in t and "重生复仇 1" in t


def test_sources_page_counts_llm_calls_per_source(cc):
    c, refs, root, ids = cc
    u, rid = _pending(refs, root, ids)
    runs.fail(refs, runs.start(refs, task="ref_unit", target_type="ref_content", target_id=ids[1], model="m",
                               input_sha="x", llm_mode="real", params={}), "坏了")
    with refs.write() as cn:                                         # 8 天前的不算进近 7 天
        cn.execute("INSERT INTO llm_run (task, target_type, target_id, model, input_sha, params_json, llm_mode, created_at, state)"
                   " VALUES ('ref_card', 'ref_work', 1, 'm', 'old', '{}', 'real', ?, 'running')", (clock.add_days(clock.now_iso(), -8),))
    t = c.get("/refs/sources").text
    hg = t.split('data-ref-source="hongguo"')[1].split("</section>")[0]
    assert "LLM 调用:今天 4 次(失败 1)/ 近 7 天 4 次(失败 1)" in hg
    jj = t.split('data-ref-source="jjwxc"')[1].split("</section>")[0]
    assert "LLM 调用:今天 0 次" in jj


def test_todo_counts_pending_cards(cc):
    c, refs, root, ids = cc
    _pending(refs, root, ids)
    assert "有 1 张拆解卡待审" in c.get("/refs/sources").text


def test_failed_unit_is_flagged_and_can_be_rebroken_alone(cc):
    """审查 I4:判卷失败的单元不进汇总,审阅页醒目提示;「重拆这一集」排一条只拆这一集的任务。"""
    from tests.refs.test_ref_card import UNIT_ARGS, _units
    c, refs, root, ids = cc
    _units(refs, root, ids, which=(1,))
    bad = LLMBackend(FakeLLM({"submit_unit": {**UNIT_ARGS[2], "quote": "原文里根本没有这一句话"}}), "m", "real")
    rid, _ = service.begin(refs, "ref_unit", ids[2], {}, bad, data_root=root)
    assert service.finish(refs, rid, bad, root / "journal", data_root=root) == "judge"
    card = _card(refs, root)
    t = c.get("/refs/card?work=1").text
    assert "data-card-failed-units" in t and "第 2 单元拆解失败,未计入这张卡" in t
    assert f'data-unit-failed="2"' in t and f'action="/refs/unit/{ids[2]}/rebreak"' in t
    assert "依据范围:第 1 集 + 剧评 3 条(平台共 142 条);后面的集没看过;第 2 集拆解失败,未计入" in t and runs.get(refs, card)["state"] == "proposed"
    r = c.post(f"/refs/unit/{ids[2]}/rebreak", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/refs/card?work=1"
    j = [x for x in jobs.recent(refs) if x["kind"] == "breakdown"]
    assert len(j) == 1 and jobs.params(j[0]) == {"content_id": ids[2]} and j[0]["auto"] == 0
    r = c.post(f"/refs/unit/{ids[1]}/rebreak")
    assert r.status_code == 400 and "不用重拆" in r.text


def test_drop_checkboxes_remove_items_from_preview_and_approved_view(cc):
    """审查 I2c:审阅页每条可借鉴 / 不可借鉴有「去掉」勾选;预览按勾选刷新,批准时存进选择、去掉的不进创作视图。"""
    c, refs, root, ids = cc
    _, rid = _pending(refs, root, ids, _card_args(techniques=["学《掌上珠》用信物贯穿全剧", "开场一分钟内让主角与对手正面对峙"]))
    t = c.get("/refs/card?work=1").text
    assert 'name="rt-0"' in t and 'name="rt-1"' in t and 'name="rn-0"' in t and 'form="card-form"' in t
    t = c.post("/refs/card/preview", data={"run": str(rid), **FORM}).text
    assert "data-gate-name" in t and "掌上珠" in t
    t = c.post("/refs/card/preview", data={"run": str(rid), **FORM, "rt-0": "1", "rn-0": "1"}).text
    assert "data-gate-name" not in t and "学《掌上珠》" not in t and "雪地下跪" not in t
    r = c.post(f"/refs/card/{rid}/approve", data={**FORM, "rt-0": "1"}, follow_redirects=False)
    assert r.status_code == 303
    dec = json.loads(refs.conn.execute("SELECT decision_json FROM ref_card").fetchone()[0])
    assert dec["reject_techniques"] == [0] and dec["reject_not_borrowable"] == []
    t = c.get("/refs/card?work=1").text
    assert "(已去掉,不进创作视图)" in t


def test_card_page_reports_a_missing_unit_file_instead_of_500(cc):
    """审查 M8:单元原文文件没了(库里还是 ok)时,审阅页如实写出来,不 500。"""
    c, refs, root, ids = cc
    _pending(refs, root, ids)
    (root / "content/hongguo/7/ep1-x.transcript.json").unlink()
    r = c.get("/refs/card?work=1")
    assert r.status_code == 200 and "data-card-error" in r.text and "FileNotFoundError" in r.text
