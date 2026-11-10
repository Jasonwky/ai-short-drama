# -*- coding: utf-8 -*-
"""拆解编排(spec 2026-11-07-参考库-design.md 2.1、3.2、1.3):进度各状态、按顺序开运行、单元失败停住、
A 模式自动拆解上限(现算、立即生效、不补跑、人点不受限)、拆过之后清理不再顺延。
FakeLLM(mode 用 "real")只当确定性替身走 begin / finish,不声称覆盖 LLM 契约。"""
import copy

import pytest

from tests.refs.test_ref_card import UNIT_ARGS, _card_args, _seed
from tools.llm import ref_card as rc
from tools.llm import ref_unit as ru
from tools.llm import runs, service
from tools.llm.fake import FakeLLM
from tools.llm.service import LLMBackend
from tools.refs import breakdown, clock, jobs, monitor, retention, settings

DAY = "2026-11-07"


def _llm(unit_args=None, card_args=None):
    """按开运行的先后依次给第 1、2 集的标注;卡给 _card_args。"""
    seq = list(unit_args or [UNIT_ARGS[1], UNIT_ARGS[2]])
    canned = {ru.TOOL: lambda schema: seq.pop(0) if len(seq) > 1 else seq[0], rc.TOOL: card_args or _card_args()}
    return LLMBackend(FakeLLM(canned), "m", "real")


class Sync:
    """同步的 start 回调:begin 后当场 finish(Runtime 里 finish 在 LLM 池后台跑)。"""
    def __init__(self, refs, root, llm, finish=True):
        self.refs, self.root, self.llm, self.finish, self.started, self.params = refs, root, llm, finish, [], []

    def __call__(self, task, tid, params):
        rid, reused = service.begin(self.refs, task, tid, params, self.llm, self.root)
        self.started.append((task, tid, reused))
        self.params.append(params)
        if not reused and self.finish:
            if service.finish(self.refs, rid, self.llm, self.root / "journal", data_root=self.root) is not None:
                breakdown.fail_for_run(self.refs, rid)
        return rid, reused


def _job(refs, wid=1, **kw):
    jid = breakdown.enqueue(refs, wid, day=DAY, **kw)
    jobs.start(refs, jid)
    return jobs.get(refs, jid)


def _run_all(refs, job, start, n=10):
    for _ in range(n):
        if jobs.get(refs, job["id"])["state"] != "running":
            return
        breakdown.advance(refs, job, start, start.root)


def test_progress_blocked_without_units(refs, tmp_path):
    with refs.write() as c:
        c.execute("INSERT INTO ref_source (key) VALUES ('hongguo')")
        c.execute("INSERT INTO ref_work (id, source, external_id, title, first_seen) VALUES (1, 'hongguo', '7', 'x', 't')")
    p = breakdown.progress(refs, 1)
    assert p["next"] is None and "没有可拆" in p["blocked"]
    job = _job(refs)
    assert "失败" in breakdown.advance(refs, job, Sync(refs, tmp_path, _llm()))
    assert jobs.get(refs, job["id"])["error"].startswith("没有可拆")


def test_progress_states_follow_the_runs(refs, tmp_path):
    ids = _seed(refs, tmp_path)
    p = breakdown.progress(refs, 1)
    assert [(u["unit"], u["state"]) for u in p["units"]] == [(1, "缺"), (2, "缺")]          # 第 3 集判过不收,不进拆解
    assert p["next"] == ("ref_unit", ids[1]) and p["card"]["state"] == "缺" and p["blocked"] is None
    rid, _ = service.begin(refs, "ref_unit", ids[1], {}, _llm(), tmp_path)
    p = breakdown.progress(refs, 1)
    assert p["units"][0]["state"] == "跑中" and p["next"] is None                          # 在跑就等,不并发开第 2 集
    runs.fail(refs, rid, "坏了")
    p = breakdown.progress(refs, 1)
    assert (p["units"][0]["state"], p["units"][0]["error"]) == ("失败", "坏了") and p["next"] == ("ref_unit", ids[1])


def test_rerecorded_unit_counts_once_with_the_latest_content(refs, tmp_path):
    """真机:第 1 集录了两次、两份都收进来并转写了,两份都进了拆解、卡里第 1 集算了两次。同一集 / 章只认最新那份。"""
    from tests.refs.test_ref_card import EP, JPG, _content, _write
    import json
    ids = _seed(refs, tmp_path)
    rel = "content/hongguo/7/ep1-y.transcript.json"
    _write(tmp_path, rel, json.dumps({"text": EP[1], "chars": [[ch, i * 0.5, i * 0.5 + 0.4] for i, ch in enumerate(EP[1])]},
                                     ensure_ascii=False))
    _write(tmp_path, "content/hongguo/7/ep1-y-kf/1.jpg", JPG)
    with refs.write() as c:
        new = _content(c, "transcript", rel, 1)
        _content(c, "keyframe", "content/hongguo/7/ep1-y-kf/1.jpg", 1, meta={"sec": 1.0})
    p = breakdown.progress(refs, 1)
    assert [(u["unit"], u["content_id"]) for u in p["units"]] == [(1, new), (2, ids[2])]
    _run_all(refs, _job(refs), Sync(refs, tmp_path, _llm()))
    ctx = rc.context(refs, 1, tmp_path, heat_until=DAY)
    assert [(u["unit"], u["content_id"]) for u in ctx["units"]] == [(1, new), (2, ids[2])]
    assert ctx["coverage"].startswith("第 1–2 集")


def test_advance_opens_units_in_order_then_the_card_then_finishes(refs, tmp_path):
    ids = _seed(refs, tmp_path)
    job = _job(refs)
    start = Sync(refs, tmp_path, _llm())
    _run_all(refs, job, start)
    assert [(t, i) for t, i, _ in start.started] == [("ref_unit", ids[1]), ("ref_unit", ids[2]), ("ref_card", 1)]
    top = refs.conn.execute("SELECT MAX(id) FROM ref_heat").fetchone()[0]
    assert start.params == [{}, {}, {"heat_until": clock.today(), "heat_max_id": top}]   # 卡的热度只算到开卡这一刻
    assert jobs.get(refs, job["id"])["state"] == "done"
    p = breakdown.progress(refs, 1)
    assert [u["state"] for u in p["units"]] == ["待审", "待审"] and p["card"]["state"] == "待审" and p["next"] is None


def test_pending_card_whose_input_changed_is_redone(refs, tmp_path):
    """真机:卡待审后输入变了(审阅页提示「点拆解这部重跑」),再点「拆解这部」任务却当场完成、不重开卡。输入变了的待审卡要重开。"""
    from tests.refs.test_ref_card import REVIEWS, _write
    import json
    _seed(refs, tmp_path)
    _run_all(refs, _job(refs), Sync(refs, tmp_path, _llm()))
    old = breakdown.progress(refs, 1)["card"]["run_id"]
    assert breakdown.progress(refs, 1, tmp_path)["next"] is None
    _write(tmp_path, "content/hongguo/7/reviews-1-x.jsonl",
           "\n".join(json.dumps(r, ensure_ascii=False) for r in [*REVIEWS, {"text": "又多了一条剧评,说得很长很长"}]))
    assert breakdown.progress(refs, 1)["next"] is None                    # 不给素材根(列表页)不查
    assert breakdown.progress(refs, 1, tmp_path)["next"] == ("ref_card", 1)
    job = _job(refs)
    _run_all(refs, job, Sync(refs, tmp_path, _llm()))
    p = breakdown.progress(refs, 1)
    assert p["card"]["run_id"] != old and p["card"]["state"] == "待审" and breakdown.progress(refs, 1, tmp_path)["next"] is None
    assert jobs.get(refs, job["id"])["state"] == "done"


def test_card_reopened_by_this_job_is_not_reopened_again(refs, tmp_path, monkeypatch):
    """护栏:卡的输入摘要若不稳定(每次重建都对不上),同一个任务最多重开两次,不无限烧 LLM。"""
    _seed(refs, tmp_path)
    _run_all(refs, _job(refs), Sync(refs, tmp_path, _llm()))
    monkeypatch.setattr(breakdown, "_card_stale", lambda refs, rid, root: True)
    job = _job(refs)
    start = Sync(refs, tmp_path, _llm())
    _run_all(refs, job, start)
    assert [t for t, _, _ in start.started] in (["ref_card"], ["ref_card", "ref_card"])   # 秒级时间:最多两次
    assert "不再自动重开" in jobs.get(refs, job["id"])["error"]


def test_advance_waits_while_a_run_is_in_flight(refs, tmp_path):
    ids = _seed(refs, tmp_path)
    job = _job(refs)
    start = Sync(refs, tmp_path, _llm(), finish=False)
    assert "开 ref_unit" in breakdown.advance(refs, job, start)
    assert breakdown.advance(refs, job, start) is None                                   # 第 1 集还在跑
    assert len(start.started) == 1 and jobs.get(refs, job["id"])["state"] == "running"
    assert "第 1 集标注中" == jobs.get(refs, job["id"])["progress"]


def test_unit_judge_failure_is_left_out_and_the_card_still_runs(refs, tmp_path):
    """spec 2.1(审查 I4):单元判卷超限失败的不进汇总,汇总照样能出,依据范围写明「第 2 集拆解失败,未计入」;不自动重试。"""
    ids = _seed(refs, tmp_path)
    bad = {**UNIT_ARGS[2], "quote": "原文里根本没有这一句话"}
    job = _job(refs)
    start = Sync(refs, tmp_path, _llm([UNIT_ARGS[1], bad]))
    _run_all(refs, job, start)
    assert [(t, i) for t, i, _ in start.started] == [("ref_unit", ids[1]), ("ref_unit", ids[2]), ("ref_card", 1)]
    assert jobs.get(refs, job["id"])["state"] == "done"
    p = breakdown.progress(refs, 1)
    assert [(u["unit"], u["state"], u["judge_failed"]) for u in p["units"]] == [(1, "待审", False), (2, "失败", True)]
    assert p["next"] is None and p["card"]["state"] == "待审"
    ctx = rc.context(refs, 1, tmp_path, heat_until=DAY)
    assert [u["unit"] for u in ctx["units"]] == [1] and ctx["failed_units"] == [2]
    assert "第 2 集拆解失败,未计入" in ctx["coverage"]
    _run_all(refs, _job(refs), start)
    assert len(start.started) == 3                                                      # 判卷失败的单元不自动重拆


def test_unit_llm_failure_still_fails_the_job(refs, tmp_path):
    """LLM 本身失败(连不上 / 截断)不是「判卷失败」:任务失败、不开卡;人再点会重拆这一集。"""
    from tools.llm.client import LLMError
    ids = _seed(refs, tmp_path)
    calls = []

    def unit(schema):
        calls.append(1)
        if len(calls) == 2:
            raise LLMError("连不上")
        return UNIT_ARGS[len(calls)] if len(calls) <= 2 else UNIT_ARGS[2]
    llm = LLMBackend(FakeLLM({ru.TOOL: unit, rc.TOOL: _card_args()}), "m", "real")
    job = _job(refs)
    start = Sync(refs, tmp_path, llm)
    _run_all(refs, job, start)
    j = jobs.get(refs, job["id"])
    assert j["state"] == "failed" and j["error"].startswith("第 2 集标注失败")
    assert [t for t, _, _ in start.started] == ["ref_unit", "ref_unit"]
    again = _job(refs)
    _run_all(refs, again, start)
    assert [(t, i) for t, i, _ in start.started[2:]] == [("ref_unit", ids[2]), ("ref_card", 1)]


def test_all_units_failing_fails_the_job_without_a_card(refs, tmp_path):
    _seed(refs, tmp_path)
    bad = {**UNIT_ARGS[1], "quote": "原文里根本没有这一句话"}
    job = _job(refs)
    start = Sync(refs, tmp_path, _llm([bad, bad]))
    _run_all(refs, job, start)
    j = jobs.get(refs, job["id"])
    assert j["state"] == "failed" and "第 1–2 集都拆解失败" in j["error"]
    assert [t for t, _, _ in start.started] == ["ref_unit", "ref_unit"]
    assert "都拆解失败" in breakdown.progress(refs, 1)["blocked"]
    with pytest.raises(ValueError, match="都拆解失败"):
        rc.context(refs, 1, tmp_path, heat_until=DAY)


def test_rebreak_one_unit_reruns_only_that_unit(refs, tmp_path):
    ids = _seed(refs, tmp_path)
    bad = {**UNIT_ARGS[2], "quote": "原文里根本没有这一句话"}
    _run_all(refs, _job(refs), Sync(refs, tmp_path, _llm([UNIT_ARGS[1], bad])))
    card = breakdown.progress(refs, 1)["card"]["run_id"]
    with pytest.raises(ValueError, match="不用重拆"):
        breakdown.enqueue_unit(refs, ids[1], day=DAY)                                   # 待审的那集不用重拆
    whole = breakdown.enqueue(refs, 1, day=DAY)
    with pytest.raises(ValueError, match="正在拆解"):
        breakdown.enqueue_unit(refs, ids[2], day=DAY)                                   # 整部在排 / 在跑:等它
    jobs.start(refs, whole)
    jobs.finish(refs, whole)
    jid = breakdown.enqueue_unit(refs, ids[2], day=DAY)
    assert jobs.params(jobs.get(refs, jid)) == {"content_id": ids[2]}
    assert breakdown.enqueue_unit(refs, ids[2], day=DAY) == jid                         # 排着的不重复排
    assert breakdown.enqueue(refs, 1, day=DAY) == jid                                   # 单集重拆排着时「拆解这部」返回它
    jobs.start(refs, jid)
    start = Sync(refs, tmp_path, _llm([UNIT_ARGS[2]]))
    _run_all(refs, jobs.get(refs, jid), start)
    assert start.started == [("ref_unit", ids[2], False)]                              # 只重拆这一集,不开卡
    j = jobs.get(refs, jid)
    assert j["state"] == "done" and j["progress"] == "第 2 集重拆中"
    p = breakdown.progress(refs, 1)
    assert p["units"][1]["state"] == "待审" and p["next"] == ("ref_card", 1)            # 卡比单元旧:人再点「拆解这部」重出卡
    assert breakdown.progress(refs, 1)["card"]["run_id"] == card


def test_rebreak_that_fails_again_fails_its_job(refs, tmp_path):
    ids = _seed(refs, tmp_path)
    bad = {**UNIT_ARGS[2], "quote": "原文里根本没有这一句话"}
    _run_all(refs, _job(refs), Sync(refs, tmp_path, _llm([UNIT_ARGS[1], bad])))
    jid = breakdown.enqueue_unit(refs, ids[2], day=DAY)
    jobs.start(refs, jid)
    _run_all(refs, jobs.get(refs, jid), Sync(refs, tmp_path, _llm([bad])))
    j = jobs.get(refs, jid)
    assert j["state"] == "failed" and j["error"].startswith("第 2 集标注失败")


def test_new_card_run_rejects_the_older_pending_one(refs, tmp_path):
    """审查 M6:开新卡运行时,同一部作品旧的待审卡运行标驳回、写原因;待办不再多算。"""
    from tools.serve.panels.refs import todo
    ids = _seed(refs, tmp_path)
    _run_all(refs, _job(refs), Sync(refs, tmp_path, _llm()))
    old = breakdown.progress(refs, 1)["card"]["run_id"]
    other = {**UNIT_ARGS[2], "summary": "男人跪下,女主当众翻旧账。"}
    rid, _ = service.begin(refs, "ref_unit", ids[2], {}, _llm([other]), tmp_path)
    service.finish(refs, rid, _llm([other]), tmp_path / "journal", data_root=tmp_path)
    _run_all(refs, _job(refs), Sync(refs, tmp_path, _llm()))
    new = breakdown.progress(refs, 1)["card"]["run_id"]
    row = runs.get(refs, old)
    assert new > old and (row["state"], row["error"]) == ("rejected", "已被新一次拆解取代")
    assert [x for x in todo(refs, DAY) if "拆解卡待审" in x] == ["有 1 张拆解卡待审 —— 在「拆解卡」面板逐条表态、批准"]


def test_start_refusing_fails_the_job_with_the_reason(refs, tmp_path):
    _seed(refs, tmp_path)
    job = _job(refs)

    def start(task, tid, params):
        raise ValueError("关键帧文件不见了")
    breakdown.advance(refs, job, start)
    assert jobs.get(refs, job["id"])["error"] == "ValueError: 关键帧文件不见了"


@pytest.mark.parametrize("exc", [OSError("磁盘坏了"), FileNotFoundError("转写文件不见了")])
def test_start_raising_other_errors_fails_the_job_too(refs, tmp_path, exc):
    """审查 C1:start 抛的不是 ValueError(文件缺失、磁盘错误)也要把任务标失败,不能让异常冒出去卡住参考库线程。"""
    _seed(refs, tmp_path)
    job = _job(refs)

    def start(task, tid, params):
        raise exc
    line = breakdown.advance(refs, job, start)
    j = jobs.get(refs, job["id"])
    assert j["state"] == "failed" and j["error"] == f"{type(exc).__name__}: {exc}"
    assert line == f"拆解 {job['id']} 失败:{type(exc).__name__}: {exc}"


def test_missing_unit_file_fails_the_job_instead_of_wedging(refs, tmp_path):
    """真链路:库里还是 ok / 未清理,文件没了 → begin 里 FileNotFoundError → 任务失败(原来每轮都抛、任务永远 running)。"""
    _seed(refs, tmp_path)
    (tmp_path / "content/hongguo/7/ep1-x.transcript.json").unlink()
    job = _job(refs)
    breakdown.advance(refs, job, Sync(refs, tmp_path, _llm()))
    j = jobs.get(refs, job["id"])
    assert j["state"] == "failed" and j["error"].startswith("FileNotFoundError: ")


def test_card_is_rerun_when_a_unit_was_rerun_after_it(refs, tmp_path):
    ids = _seed(refs, tmp_path)
    _run_all(refs, _job(refs), Sync(refs, tmp_path, _llm()))
    card = breakdown.progress(refs, 1)["card"]["run_id"]
    other = copy.deepcopy(UNIT_ARGS[2])
    other["summary"] = "男人跪下,女主当众翻旧账。"
    rid, _ = service.begin(refs, "ref_unit", ids[2], {}, _llm([other]), tmp_path)        # 已有待审的那版,再拆一次
    service.finish(refs, rid, _llm([other]), tmp_path / "journal", data_root=tmp_path)
    assert breakdown.progress(refs, 1)["next"] == ("ref_card", 1)
    start = Sync(refs, tmp_path, _llm())
    _run_all(refs, _job(refs), start)
    assert start.started == [("ref_card", 1, False)] and breakdown.progress(refs, 1)["card"]["run_id"] > card


def test_approved_card_with_same_input_is_reused_and_job_done(refs, tmp_path):
    _seed(refs, tmp_path)
    _run_all(refs, _job(refs), Sync(refs, tmp_path, _llm()))
    rid = breakdown.progress(refs, 1)["card"]["run_id"]
    service.approve(refs, rid, data_root=tmp_path, decision={"adopt": [0, 2], "reject": [1], "extra_not_borrowable": []})
    p = breakdown.progress(refs, 1)
    assert p["card"]["state"] == "已批" and p["card"]["version"] == 1
    job = _job(refs)
    start = Sync(refs, tmp_path, _llm())
    assert "沿用已批准的卡" in breakdown.advance(refs, job, start)
    assert start.started == [("ref_card", 1, True)] and jobs.get(refs, job["id"])["state"] == "done"


def test_human_enqueue_dedupes_live_jobs_only(refs, tmp_path):
    _seed(refs, tmp_path)
    a = breakdown.enqueue(refs, 1, day=DAY)
    assert breakdown.enqueue(refs, 1, day=DAY) == a
    jobs.start(refs, a)
    jobs.finish(refs, a)
    assert breakdown.enqueue(refs, 1, day=DAY) != a                                     # 做完了人还能再点
    with pytest.raises(ValueError, match="没有作品"):
        breakdown.enqueue(refs, 99)


# ---------- A 模式自动拆解上限 ----------

def _a_mode(refs, root, cap="1"):
    settings.ensure(refs)
    settings.update(refs, root, "hongguo", {"mode": "A", "retention_days": "30", "cap_fetch": "5",
                                            "cap_breakdown": cap, "top_k": "5"})


def _two_works(refs, root):
    """作品 1(_seed)与作品 2 都自动抓过内容、有可拆的单元。"""
    _seed(refs, root)
    with refs.write() as c:
        c.execute("INSERT INTO ref_work (id, source, external_id, title, first_seen) VALUES (2, 'hongguo', '8', 'y', 't')")
        c.execute("INSERT INTO ref_content (work_id, kind, unit, path, sha256, origin, verdict, fetched_at, due_at, hard_due_at)"
                  " VALUES (2, 'transcript', 1, 'p', 's', 'o', 'ok', 't', 't', 't')")
    for w in (1, 2):
        j = jobs.enqueue(refs, "hongguo", "content", work_id=w, day=DAY, auto=True, again_after_failure=False)
        jobs.start(refs, j)
        jobs.finish(refs, j)


def _auto(refs):
    return [j for j in jobs.recent(refs, limit=99) if j["kind"] == "breakdown" and j["auto"]]


def test_a_mode_auto_breakdown_stops_at_the_cap(refs, tmp_path):
    _two_works(refs, tmp_path)
    _a_mode(refs, tmp_path, cap="1")
    did = monitor.plan(refs, DAY)
    assert [j["work_id"] for j in _auto(refs)] == [1] and "hongguo:今日自动拆解已满 1/1" in did
    monitor.plan(refs, DAY)
    assert len(_auto(refs)) == 1                                                          # 幂等
    settings.update(refs, tmp_path, "hongguo", {"cap_breakdown": "2"})                  # 调高马上生效
    monitor.plan(refs, DAY)
    assert sorted(j["work_id"] for j in _auto(refs)) == [1, 2]


def test_auto_breakdown_skips_works_with_cards_or_without_auto_content(refs, tmp_path):
    _two_works(refs, tmp_path)
    _a_mode(refs, tmp_path, cap="5")
    with refs.write() as c:
        c.execute("INSERT INTO llm_run (task, target_type, target_id, model, input_sha, params_json, llm_mode, created_at, state,"
                  " output_json) VALUES ('ref_card', 'ref_work', 1, 'm', 's', '{}', 'real', 't', 'proposed', '{}')")
        c.execute("INSERT INTO ref_work (id, source, external_id, title, first_seen) VALUES (3, 'hongguo', '9', 'z', 't')")
        c.execute("INSERT INTO ref_content (work_id, kind, unit, path, sha256, origin, verdict, fetched_at, due_at, hard_due_at)"
                  " VALUES (3, 'transcript', 1, 'p3', 's', 'o', 'ok', 't', 't', 't')")
    monitor.plan(refs, DAY)
    assert sorted(j["work_id"] for j in _auto(refs)) == [2, 3]    # 1 有卡;3 没自动抓过,但红果内容来自人点录屏,齐了也算


def test_auto_breakdown_rechecked_when_it_runs(refs, tmp_path):
    _two_works(refs, tmp_path)
    _a_mode(refs, tmp_path, cap="2")
    monitor.plan(refs, DAY)
    a1, a2 = sorted(j["id"] for j in _auto(refs))
    assert jobs.next_waiting(refs, DAY, kinds=("breakdown",))["id"] == a1
    jobs.start(refs, a1)
    jobs.finish(refs, a1, error="第 1 集标注失败")                                      # 开跑过、失败了:照样算用量
    settings.update(refs, tmp_path, "hongguo", {"cap_breakdown": "1"})
    assert jobs.next_waiting(refs, DAY, kinds=("breakdown",)) is None
    assert jobs.get(refs, a2)["error"] == "今日自动拆解已满 1/1"


def test_auto_breakdown_not_run_on_a_later_day(refs, tmp_path):
    _two_works(refs, tmp_path)
    _a_mode(refs, tmp_path, cap="2")
    monitor.plan(refs, DAY)
    assert jobs.next_waiting(refs, "2026-11-08", kinds=("breakdown",)) is None
    assert {j["error"] for j in _auto(refs)} == {"自动任务不补跑(排于 2026-11-07 日)"}


def test_auto_breakdown_dropped_after_switching_to_c(refs, tmp_path):
    _two_works(refs, tmp_path)
    _a_mode(refs, tmp_path, cap="2")
    monitor.plan(refs, DAY)
    settings.update(refs, tmp_path, "hongguo", {"mode": "B"})
    assert jobs.next_waiting(refs, DAY, kinds=("breakdown",)) is not None             # B 也自动拆(内容齐了就拆)
    settings.update(refs, tmp_path, "hongguo", {"mode": "C"})
    assert jobs.next_waiting(refs, DAY, kinds=("breakdown",)) is None
    assert {j["error"] for j in _auto(refs)} == {"来源已切到 C 全手动"}


def test_cap_zero_and_failed_auto_today_not_replanned_but_by_hand_runs(refs, tmp_path):
    _two_works(refs, tmp_path)
    _a_mode(refs, tmp_path, cap="0")
    monitor.plan(refs, DAY)
    assert _auto(refs) == []                                                              # 0 = 不自动拆
    hand = breakdown.enqueue(refs, 1, day=DAY)
    with refs.write() as c:                                                               # 来源熔断不挡拆解(不碰网站)
        c.execute("UPDATE ref_source SET state = 'tripped', state_until = ? WHERE key = 'hongguo'", (DAY,))
    assert jobs.next_waiting(refs, DAY, kinds=("breakdown",))["id"] == hand              # 人点的不受上限
    with refs.write() as c:
        c.execute("UPDATE ref_source SET state = 'ok' WHERE key = 'hongguo'")
    settings.update(refs, tmp_path, "hongguo", {"cap_breakdown": "2"})
    jobs.start(refs, hand)
    jobs.finish(refs, hand)
    monitor.plan(refs, DAY)
    auto = _auto(refs)
    assert sorted(j["work_id"] for j in auto) == [1, 2]
    for j in auto:
        jobs.start(refs, j["id"])
        jobs.finish(refs, j["id"], error="坏了")
    monitor.plan(refs, DAY)
    assert len(_auto(refs)) == 2                                                          # 今天自动排过、失败了:不再排(防空转)
    assert jobs.auto_breakdown_today(refs, "hongguo", DAY) == 2
    monitor.plan(refs, "2026-11-08")
    assert len(_auto(refs)) == 2                                                          # 失败过的隔天也不再自动排:等人点


def test_failed_auto_breakdown_is_never_replanned_but_a_done_one_is_the_next_day(refs, tmp_path):
    _two_works(refs, tmp_path)
    _a_mode(refs, tmp_path, cap="5")
    monitor.plan(refs, DAY)
    a1, a2 = sorted(j["id"] for j in _auto(refs))
    for j in (a1, a2):
        jobs.start(refs, j)
    jobs.finish(refs, a1, error="第 1 集标注失败")
    jobs.finish(refs, a2)
    for day in ("2026-11-08", "2026-11-09"):
        monitor.plan(refs, day)
    later = [j for j in _auto(refs) if j["id"] not in (a1, a2)]
    assert [j["work_id"] for j in later] == [2]               # 作品 1 失败过:不再自动排;作品 2 做完了还没卡(这里没跑 LLM)照常
    hand = breakdown.enqueue(refs, 1, day="2026-11-09")
    assert jobs.get(refs, hand)["auto"] == 0                  # 人点照样能排


def _novel(refs, wid=5, *, auto_content=True, source="fanqie"):
    """一部小说:第 1 章 ok;auto_content = 自动抓过正文(A 模式非红果要求这个)。"""
    with refs.write() as c:
        c.execute("INSERT OR IGNORE INTO ref_source (key) VALUES (?)", (source,))
        c.execute("INSERT INTO ref_work (id, source, external_id, title, first_seen) VALUES (?, ?, ?, 'n', 't')", (wid, source, f"x{wid}"))
        c.execute("INSERT INTO ref_content (work_id, kind, unit, path, sha256, origin, verdict, fetched_at, due_at, hard_due_at)"
                  " VALUES (?, 'chapter', 1, ?, 's', 'o', 'ok', 't', 't', 't')", (wid, f"c{wid}"))
    if auto_content:
        j = jobs.enqueue(refs, source, "content", work_id=wid, day=DAY, auto=True, again_after_failure=False)
        jobs.start(refs, j)
        jobs.finish(refs, j)


def _mode(refs, root, key, mode, cap="5"):
    settings.ensure(refs)
    form = {"mode": mode, "retention_days": "30", "cap_fetch": "5", "top_k": "5", "chapters_n": "3"}
    if cap is not None:
        form["cap_breakdown"] = cap
    settings.update(refs, root, key, form)


def test_auto_breakdown_never_started_failure_is_replanned_the_next_day(refs, tmp_path):
    """审查 I5a:「失败过不再自动排」只看真正开跑过的失败;没开跑就被复核挡掉的(上限调低)隔天照常排。"""
    _two_works(refs, tmp_path)
    _a_mode(refs, tmp_path, cap="2")
    monitor.plan(refs, DAY)
    settings.update(refs, tmp_path, "hongguo", {"cap_breakdown": "0"})
    assert jobs.next_waiting(refs, DAY, kinds=("breakdown",)) is None                 # 两条都没开跑就被标失败
    assert all(j["state"] == "failed" and not j["started_at"] for j in _auto(refs))
    settings.update(refs, tmp_path, "hongguo", {"cap_breakdown": "2"})
    monitor.plan(refs, "2026-11-08")
    assert sorted(j["work_id"] for j in _auto(refs) if j["day"] == "2026-11-08") == [1, 2]


def test_auto_breakdown_skips_a_work_whose_latest_card_was_rejected_by_a_person(refs, tmp_path):
    """审查 I5b:最近一张卡运行被人驳回的作品不自动重拆(等人点);被新一次拆解取代的(代码驳回)不算。"""
    _two_works(refs, tmp_path)
    _a_mode(refs, tmp_path, cap="5")
    with refs.write() as c:
        for wid, err in ((1, None), (2, breakdown.SUPERSEDED)):
            c.execute("INSERT INTO llm_run (task, target_type, target_id, model, input_sha, params_json, llm_mode, created_at, state,"
                      " output_json, error) VALUES ('ref_card', 'ref_work', ?, 'm', 's', '{}', 'real', 't', 'rejected', '{}', ?)",
                      (wid, err))
    monitor.plan(refs, DAY)
    assert [j["work_id"] for j in _auto(refs)] == [2]


def test_b_mode_breaks_down_once_content_is_complete(refs, tmp_path):
    """审查 I5c(spec 3.2):B 模式「内容齐了就自动拆」,也按 cap_breakdown 计数;不要求自动抓过。"""
    _novel(refs, 5, auto_content=False)
    _mode(refs, tmp_path, "fanqie", "B", cap="1")
    _novel(refs, 6, auto_content=False)
    did = monitor.plan(refs, DAY)
    assert [j["work_id"] for j in _auto(refs)] == [5] and "fanqie:今日自动拆解已满 1/1" in did
    j = jobs.next_waiting(refs, DAY, kinds=("breakdown",))
    assert j["work_id"] == 5                                                            # B 模式的自动拆解跑的时候复核也放行


def test_b_mode_without_cap_does_not_auto_break_down_and_does_not_complain(refs, tmp_path):
    _novel(refs, 5, auto_content=False)
    _mode(refs, tmp_path, "fanqie", "B", cap=None)
    did = monitor.plan(refs, DAY)
    assert _auto(refs) == [] and not [d for d in did if "拆解" in d]


def test_content_not_complete_while_fetching_or_a_recording_lacks_its_transcript(refs, tmp_path):
    _seed(refs, tmp_path)
    _novel(refs, 5, auto_content=False)
    _mode(refs, tmp_path, "fanqie", "B")
    live = jobs.enqueue(refs, "fanqie", "content", work_id=5, day=DAY)                 # 还在抓
    monitor.plan(refs, DAY)
    assert _auto(refs) == []
    jobs.start(refs, live)
    jobs.finish(refs, live)
    monitor.plan(refs, DAY)
    assert [j["work_id"] for j in _auto(refs)] == [5]

    # 红果:一集录屏 ok 但还没转写 → 不齐;转写排着 → 不齐;转写好了 → 齐
    _a_mode(refs, tmp_path, cap="5")
    with refs.write() as c:
        c.execute("INSERT INTO ref_content (work_id, kind, unit, path, sha256, origin, verdict, fetched_at, due_at, hard_due_at)"
                  " VALUES (1, 'recording', 4, 'content/hongguo/7/ep4-x.webm', 's', 'o', 'ok', 't', 't', 't')")
    monitor.plan(refs, DAY)
    assert 1 not in [j["work_id"] for j in _auto(refs)]
    t = jobs.enqueue(refs, "hongguo", "transcribe", work_id=1, day=DAY, params={"recording_id": 99})
    with refs.write() as c:
        c.execute("INSERT INTO ref_content (work_id, kind, unit, path, sha256, origin, verdict, fetched_at, due_at, hard_due_at)"
                  " VALUES (1, 'transcript', 4, 'content/hongguo/7/ep4-x.transcript.json', 's', 'o', 'ok', 't', 't', 't')")
    monitor.plan(refs, DAY)
    assert 1 not in [j["work_id"] for j in _auto(refs)]                                # 转写任务还在途
    jobs.start(refs, t)
    jobs.finish(refs, t)
    monitor.plan(refs, DAY)
    assert 1 in [j["work_id"] for j in _auto(refs)]


def test_content_complete_uses_the_latest_recording_and_counts_rejected_transcripts(refs, tmp_path):
    """审查:「内容齐了」与拆解同一口径 —— 每集只看最新那份录屏(旧的那份转写没出来不挡);转写判了不收也算这集处理完了。"""
    _seed(refs, tmp_path)
    ins = ("INSERT INTO ref_content (work_id, kind, unit, path, sha256, origin, verdict, fetched_at, due_at, hard_due_at)"
           " VALUES (1, ?, ?, ?, 's', 'o', ?, 't', 't', 't')")
    with refs.write() as c:
        c.execute(ins, ("recording", 1, "content/hongguo/7/ep1-old.webm", "ok"))           # 第一次录:转写一直没出来
        c.execute(ins, ("recording", 1, "content/hongguo/7/ep1-new.webm", "ok"))           # 重录
        c.execute(ins, ("transcript", 1, "content/hongguo/7/ep1-new.transcript.json", "ok"))
    assert monitor.content_complete(refs, 1)
    with refs.write() as c:
        c.execute(ins, ("recording", 4, "content/hongguo/7/ep4-z.webm", "ok"))
    assert not monitor.content_complete(refs, 1)                                            # 最新录屏还没转写
    with refs.write() as c:
        c.execute(ins, ("transcript", 4, "content/hongguo/7/ep4-z.transcript.json", "rejected"))   # 几乎没台词,判不收
    assert monitor.content_complete(refs, 1)


def test_a_mode_novel_still_needs_an_auto_fetch(refs, tmp_path):
    _novel(refs, 5, auto_content=False)
    _novel(refs, 6, auto_content=True)
    _mode(refs, tmp_path, "fanqie", "A")
    monitor.plan(refs, DAY)
    assert [j["work_id"] for j in _auto(refs)] == [6]


# ---------- 到期清理接卡 ----------

def test_only_an_approved_card_ends_the_retention_deferral(refs, tmp_path):
    """spec 1.3(审查 I3):有待审 / 刚被驳回的拆解时**顺延**;只有已批准的卡才按 due_at 清。"""
    _seed(refs, tmp_path)
    assert not retention.has_breakdown(refs, 1)
    _run_all(refs, _job(refs), Sync(refs, tmp_path, _llm()))
    rid = breakdown.progress(refs, 1)["card"]["run_id"]
    assert not retention.has_breakdown(refs, 1)                                          # 待审的卡:照常顺延
    runs.reject(refs, rid)
    assert not retention.has_breakdown(refs, 1)                                          # 刚驳回:照常顺延
    _run_all(refs, _job(refs), Sync(refs, tmp_path, _llm()))
    rid = breakdown.progress(refs, 1)["card"]["run_id"]
    service.approve(refs, rid, data_root=tmp_path, decision={"adopt": [0, 2], "reject": [1], "extra_not_borrowable": []})
    assert retention.has_breakdown(refs, 1)                                              # 已批准的卡


def test_purge_uses_due_at_once_the_work_has_an_approved_card(refs, tmp_path):
    _seed(refs, tmp_path)
    with refs.write() as c:
        c.execute("UPDATE ref_content SET due_at = '2026-01-01T00:00:00+00:00', hard_due_at = '2026-03-01T00:00:00+00:00'")
    (tmp_path / "content/hongguo/7/ep3-x.transcript.json").write_text("{}", encoding="utf-8")   # 判过不收的那集也有文件
    now = "2026-02-01T00:00:00+00:00"
    assert retention.purge(refs, tmp_path, now=now) == ([], [])                          # 没拆过:顺延到硬到期
    _run_all(refs, _job(refs), Sync(refs, tmp_path, _llm()))
    assert retention.purge(refs, tmp_path, now=now) == ([], [])                          # 卡待审:还顺延(人要对着原文审)
    rid = breakdown.progress(refs, 1)["card"]["run_id"]
    service.approve(refs, rid, data_root=tmp_path, decision={"adopt": [0, 2], "reject": [1], "extra_not_borrowable": []})
    done, errors = retention.purge(refs, tmp_path, now=now)
    assert errors == [] and len(done) == len(refs.conn.execute("SELECT id FROM ref_content").fetchall())
