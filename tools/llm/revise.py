# -*- coding: utf-8 -*-
"""修改回路的共用部分:人写的意见、LLM 回应的结构与共同判卷、按意见整组的接受 / 驳回、修改历史
(spec 2026-10-24-剧本分镜修改回路-design.md 第 0 节)。剧本修改与分镜修改各自的任务模块只管自己的「修改」长什么样。

- 意见一行一条,1–10 条;每条可选「针对」(剧本:一段原文;分镜:段号)与「必须改」;
- LLM 对每条意见恰好回应一次:「改」至少给一处修改,「不改」必须写理由,「必须改」的不许「不改」;
- 人按意见整组接受 / 驳回:同一条意见下的修改要么全写回、要么全不写(有依赖的修改不会被拆散);
- 下一轮把最近一轮被驳回的修改带给 LLM(「不要再这样改」)。
"""
from __future__ import annotations

from collections.abc import Callable

from tools.drama.db import Store
from tools.llm import runs

MAX_NOTES = 10
VERDICTS = ("改", "不改")


def notes_from_form(form) -> list[dict]:
    """表单的 note-i / target-i / must-i → 意见列表;没写意见的行跳过。"""
    out = []
    for i in range(MAX_NOTES):
        text = str(form.get(f"note-{i}", "")).strip()
        if text:
            out.append({"text": text, "target": str(form.get(f"target-{i}", "")).strip(),
                        "must": str(form.get(f"must-{i}", "")) == "1"})
    return out


def check_notes(notes: list[dict]) -> None:
    if not 1 <= len(notes) <= MAX_NOTES:
        raise ValueError(f"修改意见要 1–{MAX_NOTES} 条,现在 {len(notes)} 条")
    for i, n in enumerate(notes, 1):
        if not isinstance(n.get("text"), str) or not n["text"].strip():
            raise ValueError(f"第 {i} 条意见是空的")
        if not isinstance(n.get("target"), str) or not isinstance(n.get("must"), bool):
            raise ValueError(f"第 {i} 条意见格式不对(要有 text / target / must)")


def render_notes(notes: list[dict], target_fmt: str = "针对:{}") -> str:
    return "\n".join(f"{k}. {n['text']}" + (f"({target_fmt.format(n['target'])})" if n["target"] else "")
                     + ("【必须改】" if n["must"] else "") for k, n in enumerate(notes, 1))


def render_rejected(rejected: list[dict], render_change: Callable[[dict], str]) -> str:
    if not rejected:
        return ""
    body = "\n".join(f"- 意见「{r['note']}」被驳回的改法:" + ";".join(render_change(c) for c in r["changes"])
                     for r in rejected)
    return f"<rejected>\n{body}\n</rejected>\n"


def response_schema(n_notes: int, change_key: str, change_item: dict) -> dict:
    item = {"type": "object", "additionalProperties": False, "required": ["note", "verdict", "reason", change_key],
            "properties": {"note": {"type": "integer", "enum": list(range(1, n_notes + 1))},
                           "verdict": {"type": "string", "enum": list(VERDICTS)},
                           "reason": {"type": "string"},
                           change_key: {"type": "array", "items": change_item}}}
    return {"type": "object", "additionalProperties": False, "required": ["responses"],
            "properties": {"responses": {"type": "array", "items": item}}}


def response_problems(responses: list[dict], notes: list[dict], change_key: str) -> list[str]:
    seen = [r["note"] for r in responses]
    problems = [f"意见 {k} 要恰好回应一次,现在 {seen.count(k)} 次"
                for k in range(1, len(notes) + 1) if seen.count(k) != 1]
    for r in responses:
        k = r["note"]
        if r["verdict"] == "改" and not r[change_key]:
            problems.append(f"意见 {k} 回答「改」却没有给出修改")
        if r["verdict"] == "不改":
            if r[change_key]:
                problems.append(f"意见 {k} 回答「不改」就不要附修改")
            if not r["reason"].strip():
                problems.append(f"意见 {k} 回答「不改」要写理由")
            if 1 <= k <= len(notes) and notes[k - 1]["must"]:
                problems.append(f"意见 {k} 标了「必须改」,不能回答「不改」")
    return problems


def changed_notes(args: dict) -> list[int]:
    return sorted(r["note"] for r in args["responses"] if r["verdict"] == "改")


def decision_problems(args: dict, decision: dict) -> list[str]:
    acc, rej = decision.get("accept"), decision.get("reject")
    if not isinstance(acc, list) or not isinstance(rej, list):
        return ["要对每条改了的意见选「接受」或「驳回」"]
    if any(isinstance(x, bool) or not isinstance(x, int) for x in acc + rej):
        return ["意见序号要是整数"]
    want, got = set(changed_notes(args)), set(acc) | set(rej)
    out = []
    if set(acc) & set(rej):
        out.append(f"意见 {sorted(set(acc) & set(rej))} 既接受又驳回")
    if want - got:
        out.append(f"意见 {sorted(want - got)} 还没表态")
    if got - want:
        out.append(f"意见 {sorted(got - want)} 没有修改,不用表态")
    return out


def accepted(args: dict, decision: dict) -> list[dict]:
    """被接受的意见的回应(按意见序号)。"""
    return [r for r in sorted(args["responses"], key=lambda r: r["note"]) if r["note"] in decision["accept"]]


def last_rejected(store: Store, task: str, target_type: str, target_id: int, change_key: str) -> list[dict]:
    """最近一轮批准时被驳回的意见及其修改:[{"note": 意见原文, "changes": [...]}]。"""
    row = store.conn.execute("SELECT * FROM llm_run WHERE task = ? AND target_type = ? AND target_id = ?"
                             " AND state = 'approved' ORDER BY id DESC LIMIT 1", (task, target_type, target_id)).fetchone()
    if row is None:
        return []
    dec, notes = runs.decision(row), runs.params(row)["notes"]
    return [{"note": notes[r["note"] - 1]["text"], "changes": r[change_key]}
            for r in runs.output(row)["responses"] if r["note"] in dec["reject"]]


def groups(row, change_key: str, render_change: Callable[[dict], dict]) -> list[dict]:
    """审阅页:按意见分组的回应,附批准后的表态。render_change:一处修改 → {label, old, new, why}。"""
    out, params = runs.output(row), runs.params(row)
    dec = runs.decision(row) if row["state"] == "approved" else None
    stance = {**{k: "accept" for k in dec["accept"]}, **{k: "reject" for k in dec["reject"]}} if dec else {}
    res = []
    for r in sorted(out["responses"], key=lambda r: r["note"]):
        n = params["notes"][r["note"] - 1]
        res.append({"note": r["note"], "text": n["text"], "target": n["target"], "must": n["must"],
                    "verdict": r["verdict"], "reason": r["reason"], "stance": stance.get(r["note"]),
                    "items": [render_change(c) for c in r[change_key]]})
    return res


def rounds(store: Store, task: str, episode_id: int, rollback_problems: Callable) -> list[dict]:
    """修改历史:这一集批准过的各轮,附「现在还能不能回滚」。"""
    out = []
    for row in runs.history(store, "episode", episode_id, task):
        if row["state"] != "approved":
            continue
        dec = runs.decision(row)
        out.append({"id": row["id"], "decided_at": row["decided_at"], "notes": runs.params(row)["notes"],
                    "accept": dec["accept"], "reject": dec["reject"],
                    "can_rollback": not rollback_problems(store, row)})
    return out
