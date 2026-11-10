# -*- coding: utf-8 -*-
"""拆解编排(spec 2026-11-07-参考库-design.md 2.1、3.2):一部作品 = 一条 `ref_job(kind='breakdown')`。

- `progress` 纯查询:每个收进来的单元(ok、没清理的章节 / 转写)最新一条单元运行的状态、卡运行的状态、下一步开哪条;
  单元运行口径与 `ref_card.context` 一致(取最新的 running / proposed / approved;都没有才看失败);
- `advance` 推进一条 running 的 breakdown 任务:缺哪个单元就开哪个,单元齐了开卡,卡待审了任务 done;
  有运行在跑就等(同一部作品同时只有一条运行);**运行失败 → 任务失败写原因,不自动重试**(`fail_for_run`,防空转);
  例外(spec 2.1):单元**判卷超限**失败的不进汇总、不挡开卡(依据范围写明「第 n 集拆解失败,未计入」),人可点「重拆这一集」
  (`enqueue_unit`:参数带 content_id 的 breakdown,只拆这一个单元、不开卡);全部单元都判卷失败 → 不开卡,任务失败;
  开新卡运行时,旧的待审卡运行标驳回(原因 `SUPERSEDED`);
- 人点「拆解这部」排 auto = 0(`enqueue`);A 模式由 `monitor` 排 auto = 1,每日上限 `cap_breakdown` 现算、立即生效、不补跑
  (到跑的时候 `jobs.next_waiting` 再核一遍)。
- 真正开运行的是 Runtime(参考库版 `service.begin` + LLM 单线程池里 `finish`),这里只拿一个 `start(task, target_id, params)` 回调;
  卡运行的参数带 `heat_until`(今天,北京日期)与 `heat_max_id`(开卡时热度表最大行号)。
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from tools.llm import runs
from tools.refs import clock, jobs

MISSING, RUNNING, PROPOSED, APPROVED, FAILED = "缺", "跑中", "待审", "已批", "失败"
SUPERSEDED = "已被新一次拆解取代"
_STATE = {"running": RUNNING, "proposed": PROPOSED, "approved": APPROVED}


def _latest(refs, task: str, target_type: str, target_id: int) -> tuple[str, int | None, str, bool]:
    """(状态, 运行号, 失败原因, 是不是判卷失败)。活着的(跑中 / 待审 / 已批)优先;没有才看最近一条失败;都没有 = 缺。驳回的当缺。
    判卷失败 = 失败且有闸门留痕(`service.finish` 只在 GateFailed 时记 journal_path;LLM 连不上 / 截断 / 前提变了都不记)。"""
    row = refs.conn.execute("SELECT id, state FROM llm_run WHERE task = ? AND target_type = ? AND target_id = ?"
                            " AND state IN ('running', 'proposed', 'approved') ORDER BY id DESC LIMIT 1",
                            (task, target_type, target_id)).fetchone()
    if row is not None:
        return _STATE[row["state"]], row["id"], "", False
    row = refs.conn.execute("SELECT id, error, journal_path FROM llm_run WHERE task = ? AND target_type = ? AND target_id = ?"
                            " AND state = 'failed' ORDER BY id DESC LIMIT 1", (task, target_type, target_id)).fetchone()
    if row is None:
        return MISSING, None, "", False
    return FAILED, row["id"], row["error"], bool(row["journal_path"])


def unit_judge_failed(refs, content_id: int) -> bool:
    """这个单元现在的状态是「判卷超限失败」:不进汇总、不自动重拆(spec 2.1),人点「重拆这一集」。"""
    state, _, _, judge = _latest(refs, "ref_unit", "ref_content", content_id)
    return state == FAILED and judge


def ranges(nums: list[int]) -> str:
    """[1, 2, 3, 5] → 「1–3、5」。"""
    out, start = [], None
    for i, n in enumerate(nums):
        if start is None:
            start = n
        if i + 1 == len(nums) or nums[i + 1] != n + 1:
            out.append(f"{start}" if start == n else f"{start}–{n}")
            start = None
    return "、".join(out)


# 每集 / 章的「当前」内容:没清理的里最新那条(重录 / 重抓后旧的那份不再进拆解;真机第 1 集录两次、两份都收,卡里算了两次)。
# 最新那条判过不收,这一集就算不收,不退回用旧的。用法:`AND id IN (CURRENT)`,参数 (work_id,)。
CURRENT = ("SELECT MAX(id) FROM ref_content WHERE work_id = ? AND kind IN ('chapter', 'transcript') AND purged_at = ''"
           " GROUP BY kind, unit")


def progress(refs, work_id: int, data_root: Path | None = None) -> dict:
    """`data_root` 给了才查待审的卡是否过期(要读原文重建上下文;编排推进时给,列表页不给)。"""
    rows = refs.conn.execute(f"SELECT id, kind, unit FROM ref_content WHERE work_id = ? AND id IN ({CURRENT})"
                             " AND verdict = 'ok' ORDER BY unit, id", (work_id, work_id)).fetchall()
    units = []
    for r in rows:
        state, rid, err, judge = _latest(refs, "ref_unit", "ref_content", r["id"])
        units.append({"content_id": r["id"], "unit": r["unit"], "kind": r["kind"], "state": state, "run_id": rid, "error": err,
                      "judge_failed": judge})
    state, rid, err, _ = _latest(refs, "ref_card", "ref_work", work_id)
    ver = refs.conn.execute("SELECT MAX(version) FROM ref_card WHERE work_id = ? AND state = 'approved'", (work_id,)).fetchone()[0]
    card = {"state": state, "run_id": rid, "error": err, "version": ver}
    if not units:
        return {"units": [], "card": card, "next": None, "blocked": "没有可拆的集 / 章(没抓到、判过不收或已清理)"}
    usable = [u for u in units if not u["judge_failed"]]       # 判卷超限失败的单元不进汇总(spec 2.1)
    if not usable:
        w = _word(units)
        return {"units": units, "card": card, "next": None,
                "blocked": f"第 {ranges([u['unit'] for u in units])} {w}都拆解失败了(判卷没过),没有能汇总的"
                           f" —— 在拆解卡页逐{w}点「重拆这一{w}」"}
    nxt = None
    todo = [u for u in usable if u["state"] in (MISSING, FAILED)]   # 缺的、LLM 本身失败的(连不上 / 截断):重拆
    if any(u["state"] == RUNNING for u in units) or state == RUNNING:
        nxt = None                                      # 有运行在跑:等它结束(同一部作品同时只跑一条)
    elif todo:
        nxt = ("ref_unit", todo[0]["content_id"])
    else:
        newest_unit = max(u["run_id"] for u in usable)
        # 卡没有 / 失败了 / 已批准(要不要重拆由 begin 按输入判:没变就复用锁定版)/ 待审但单元在它之后又重拆过 → 开卡
        stale = state == PROPOSED and (rid < newest_unit or (data_root is not None and _card_stale(refs, rid, data_root)))
        if state in (MISSING, FAILED, APPROVED) or stale:
            nxt = ("ref_card", work_id)
    return {"units": units, "card": card, "next": nxt, "blocked": None}


def _card_stale(refs, rid: int, data_root: Path) -> bool:
    """待审的卡按现在的数据重建,输入摘要对不上(或重建不了)= 过期:批准会被拒,要重开(真机:审阅页让人「点拆解这部重跑」,
    点了却当场完成)。和审阅页、批准用同一套 `service.prepare`。"""
    from tools.llm import service                 # 函数内导入:service 登记 ref_card,ref_card 引用本模块
    row = refs.conn.execute("SELECT * FROM llm_run WHERE id = ?", (rid,)).fetchone()
    try:
        return service.prepare(refs, "ref_card", row["target_id"], runs.params(row), data_root).sha != row["input_sha"]
    except Exception:                               # 重建不了也批不了:同样重开,开卡时的错误如实进任务
        return True


def _cards_since(refs, work_id: int, job: dict) -> int:
    started = job.get("started_at") or jobs.get(refs, job["id"])["started_at"]
    return refs.conn.execute("SELECT COUNT(*) FROM llm_run WHERE task = 'ref_card' AND target_type = 'ref_work' AND target_id = ?"
                             " AND created_at >= ?", (work_id, started)).fetchone()[0]


def enqueue(refs, work_id: int, *, auto: bool = False, day: str | None = None) -> int:
    """人点「拆解这部」(auto=False)/ A、B 模式自动排(auto=True)。这部作品已有排着或在跑的 breakdown(含单集重拆)就返回它。"""
    src = refs.conn.execute("SELECT source FROM ref_work WHERE id = ?", (work_id,)).fetchone()
    if src is None:
        raise ValueError(f"参考库里没有作品 {work_id}")
    live = refs.conn.execute("SELECT id FROM ref_job WHERE kind = 'breakdown' AND work_id = ? AND state IN ('waiting', 'running')"
                             " ORDER BY id LIMIT 1", (work_id,)).fetchone()
    if live is not None:
        return live[0]
    # 只和排着 / 在跑的去重:人点的做完 / 失败了还能再点(重拆);自动排的「今天排过就不再排」由 monitor 判
    return jobs.enqueue(refs, src[0], "breakdown", work_id=work_id, day=day or clock.today(), auto=auto,
                        dedupe_states=("waiting", "running"))


def enqueue_unit(refs, content_id: int, *, day: str | None = None) -> int:
    """人点「重拆这一集 / 章」:排一条只拆这个单元的 breakdown(参数 content_id,人点的不受上限)。
    只给失败了的单元;这部作品有整部拆解排着 / 在跑就拒(等它结束);同一单元已排着就返回它。"""
    row = refs.conn.execute("SELECT c.id, c.work_id, c.kind, c.unit, w.source FROM ref_content c JOIN ref_work w ON w.id = c.work_id"
                            " WHERE c.id = ? AND c.kind IN ('chapter', 'transcript') AND c.verdict = 'ok' AND c.purged_at = ''",
                            (content_id,)).fetchone()
    if row is None:
        raise ValueError(f"内容 {content_id} 不是收进来的章节 / 转写(没有、判过不收或已清理),不能重拆")
    what = f"第 {row['unit']} {'集' if row['kind'] == 'transcript' else '章'}"
    live = refs.conn.execute("SELECT * FROM ref_job WHERE kind = 'breakdown' AND work_id = ? AND state IN ('waiting', 'running')"
                             " ORDER BY id LIMIT 1", (row["work_id"],)).fetchone()
    if live is not None and jobs.params(dict(live)).get("content_id") == content_id:
        return live["id"]
    state = _latest(refs, "ref_unit", "ref_content", content_id)[0]
    if state != FAILED:
        raise ValueError(f"{what}现在是「{state}」,不用重拆 —— 只有拆解失败的单元能单独重拆")
    if live is not None:
        raise ValueError(f"这部作品正在拆解(任务 {live['id']}),等它结束再重拆{what}")
    return jobs.enqueue(refs, row["source"], "breakdown", work_id=row["work_id"], day=day or clock.today(),
                        params={"content_id": content_id}, dedupe_states=("waiting", "running"))


def running_job(refs) -> dict | None:
    row = refs.conn.execute("SELECT * FROM ref_job WHERE kind = 'breakdown' AND state = 'running' ORDER BY id LIMIT 1").fetchone()
    return dict(row) if row is not None else None


def _word(units: list[dict]) -> str:
    return "集" if any(u["kind"] == "transcript" for u in units) else "章"


def advance(refs, job: dict, start: Callable[[str, int, dict], tuple[int, bool]], data_root: Path | None = None) -> str | None:
    """推进一条 running 的 breakdown 任务一步,返回留痕的一行(什么都没做返回 None,调用方把这一行写进 journal)。
    `start` 当场抛任何异常(前提不满足、文件缺失、LLM 不可用)→ 任务失败写「类型: 信息」;命中锁定版(不调 LLM)就接着看下一步。"""
    wid = job["work_id"]
    if "content_id" in jobs.params(job):
        return _advance_unit(refs, job, jobs.params(job)["content_id"], start)
    for _ in range(len(progress(refs, wid)["units"]) + 2):        # 每次复用锁定版都让状态前进一格,不会转圈
        p = progress(refs, wid, data_root)
        if p["blocked"]:
            jobs.finish(refs, job["id"], error=p["blocked"])
            return f"拆解 {job['id']} 失败:{p['blocked']}"
        if p["next"] is None:
            if any(u["state"] == RUNNING for u in p["units"]) or p["card"]["state"] == RUNNING:
                return None
            jobs.finish(refs, job["id"])                    # 单元都有运行、卡待审(或已批准且输入没变)
            return f"拆解 {job['id']} 完成:作品 {wid} 的卡待审"
        task, tid = p["next"]
        if task == "ref_card" and p["card"]["state"] == PROPOSED and _cards_since(refs, wid, job) >= 2:
            # 护栏:任务开始后已开过的卡一出来又「过期」= 输入摘要不稳定,再开只会无限烧 LLM(真机接错素材根时出过)。
            # 时间只到秒,上一个任务同一秒开的卡也会算进来,所以门槛是 2:一个任务最多重开两次
            msg = "这次拆解刚开的卡按现在的数据重建仍对不上 —— 不再自动重开,请看审阅页的提示"
            jobs.finish(refs, job["id"], error=msg)
            return f"拆解 {job['id']} 失败:{msg}"
        # 卡的热度只算到开卡这一刻(北京日期 + 当时热度表的最大行号,记进运行参数、不可改):
        # 之后新拉的热度(隔天的、同一天开卡后的)都不改卡的输入(审查 I1)
        params = ({"heat_until": clock.today(),
                   "heat_max_id": refs.conn.execute("SELECT COALESCE(MAX(id), 0) FROM ref_heat").fetchone()[0]}
                  if task == "ref_card" else {})
        try:
            rid, reused = start(task, tid, params)
        except Exception as e:      # 前提不满足(ValueError)、文件缺失 / 磁盘错误(OSError)、LLM 不可用都一样:任务失败写原因,不往外冒
            msg = f"{type(e).__name__}: {e}"
            jobs.finish(refs, job["id"], error=msg)
            return f"拆解 {job['id']} 失败:{msg}"
        if task == "ref_card" and reused:
            jobs.finish(refs, job["id"])
            return f"拆解 {job['id']} 完成:输入没变,沿用已批准的卡(运行 {rid})"
        if task == "ref_card":              # 新一次汇总开了:旧的待审卡作废、写原因,待办不多算(审查 M6)
            for (old,) in refs.conn.execute("SELECT id FROM llm_run WHERE task = 'ref_card' AND target_type = 'ref_work'"
                                            " AND target_id = ? AND state = 'proposed' AND id != ?", (wid, rid)).fetchall():
                try:
                    runs.reject(refs, old, reason=SUPERSEDED)
                except ValueError:          # 网页线程刚好把它批准了(状态已不是 proposed):留着,不算这次推进出错
                    pass
        if not reused:
            if task == "ref_unit":
                n = next(u["unit"] for u in p["units"] if u["content_id"] == tid)
                what = f"第 {n} {_word(p['units'])}"
            else:
                what = "整部汇总"
            jobs.set_progress(refs, job["id"], f"{what}标注中" if task == "ref_unit" else f"{what}中")
            return f"拆解 {job['id']}:开 {task} 运行 {rid}({what})"
    raise RuntimeError(f"拆解 {job['id']} 推进不动:复用锁定版后状态没有前进")


def _advance_unit(refs, job: dict, cid: int, start) -> str | None:
    """单集重拆:开这一个单元的运行(进度写「第 n 集重拆中」,也当「已经开过」的记号),结束了任务就收尾,不开卡。"""
    row = refs.conn.execute("SELECT kind, unit FROM ref_content WHERE id = ?", (cid,)).fetchone()
    what = f"第 {row['unit']} {'集' if row['kind'] == 'transcript' else '章'}"
    if not jobs.get(refs, job["id"])["progress"]:
        try:
            rid, reused = start("ref_unit", cid, {})
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            jobs.finish(refs, job["id"], error=msg)
            return f"拆解 {job['id']} 失败:{msg}"
        jobs.set_progress(refs, job["id"], f"{what}重拆中")
        if not reused:
            return f"拆解 {job['id']}:开 ref_unit 运行 {rid}({what}重拆)"
    state, rid, err, _ = _latest(refs, "ref_unit", "ref_content", cid)
    if state == RUNNING:
        return None
    if state == FAILED:
        if jobs.get(refs, job["id"])["state"] == "running":      # fail_for_run 通常已经先标了
            jobs.finish(refs, job["id"], error=f"{what}标注失败(运行 {rid}):{err or '原因没记下'}")
        return f"拆解 {job['id']} 失败:{what}重拆失败(运行 {rid})"
    jobs.finish(refs, job["id"])
    return f"拆解 {job['id']} 完成:{what}重拆好了(运行 {rid}),卡要重出就点「拆解这部」"


def fail_for_run(refs, run_id: int) -> str | None:
    """一条参考库运行失败了:它所在作品那条 running 的 breakdown 任务跟着失败(不自动重试,人可以再点)。"""
    run = refs.conn.execute("SELECT task, target_type, target_id, error FROM llm_run WHERE id = ?", (run_id,)).fetchone()
    if run is None or run["task"] not in ("ref_unit", "ref_card"):
        return None
    if run["target_type"] == "ref_content":
        c = refs.conn.execute("SELECT work_id, kind, unit FROM ref_content WHERE id = ?", (run["target_id"],)).fetchone()
        wid, what = c["work_id"], f"第 {c['unit']} {'集' if c['kind'] == 'transcript' else '章'}标注"
    else:
        wid, what = run["target_id"], "整部汇总"
    job = refs.conn.execute("SELECT id, params_json FROM ref_job WHERE kind = 'breakdown' AND work_id = ? AND state = 'running'",
                            (wid,)).fetchone()
    if job is None:
        return None
    if run["task"] == "ref_unit" and unit_judge_failed(refs, run["target_id"]) and "content_id" not in jobs.params(dict(job)):
        return None                       # 整部拆解里单元判卷失败:不进汇总,接着拆下一个单元 / 开卡(spec 2.1)
    jobs.finish(refs, job[0], error=f"{what}失败(运行 {run_id}):{run['error'] or '原因没记下'}")
    return f"拆解 {job[0]} 失败:{what}失败(运行 {run_id})"
