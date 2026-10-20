# -*- coding: utf-8 -*-
"""留痕:倒查树、LLM 来源、同目标的其它尝试、任务详情、成片快照对比(设计 specs/2026-10-09-操作留痕查看-design.md)。**只读**。

- 倒查树复用溯源报告的 `provenance.node`(导出与控制台同一棵树),每个节点再标:内容核对、目标、LLM 来源、其它尝试;
- **能用数据证明的不推断**:视频提示词里逐字写着「<角色>的声音:<描述>」,批准时写回的是 `voices.compose(输出)` ——
  对上 = 「确证」;其余按时间推断(只取任务创建之前批准的),页面上标明;
- 成片不重新追:展示导出时的溯源报告(快照、备案材料),再标出之后换过的。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tools.cut import provenance
from tools.drama import targets
from tools.drama.db import Store
from tools.llm import voices
from tools.serve import journal, trace
from tools.serve.dispatch import SUBMITTED

_SEGMENT_SOURCES = (("episode", ("storyboard", "script_synopsis", "script_adapt")), ("drama", ("outline",)))


def _inside(root: Path, rel: str) -> None:
    base = Path(root).resolve()
    if base not in (base / rel).resolve().parents:
        raise KeyError(f"{rel} 不在素材根里")


def _latest_approved(store: Store, task: str, target_type: str, target_id: int, before: str):
    return store.conn.execute(
        "SELECT * FROM llm_run WHERE task = ? AND target_type = ? AND target_id = ? AND state = 'approved'"
        " AND decided_at <= ? ORDER BY decided_at DESC, id DESC LIMIT 1",
        (task, target_type, target_id, before)).fetchone()


def _source(run: sqlite3.Row, how: str) -> dict:
    return {"run_id": run["id"], "task": run["task"], "how": how, "decided_at": run["decided_at"],
            "href": f"/llm/run/{run['id']}"}


def llm_sources(store: Store, task: sqlite3.Row) -> list[dict]:
    """这条任务用到的文字出自哪次 LLM 运行。只取任务创建之前批准的;驳回 / 失败的不算。"""
    before, out = task["created_at"], []
    if task["target_type"] == "segment":
        drama_id, episode_id = trace.attribute(store, "task", task["id"])
        for level, names in _SEGMENT_SOURCES:
            target = episode_id if level == "episode" else drama_id
            if target is None:
                continue                                   # 段落被删:集定不了,不猜
            for name in names:
                run = _latest_approved(store, name, level, target, before)
                if run:
                    out.append(_source(run, "按时间推断"))
        if task["kind"] == "video" and drama_id is not None:
            for ch in store.conn.execute("SELECT id, name FROM character WHERE drama_id = ?", (drama_id,)):
                if f"{ch['name']}的声音:" not in task["prompt"]:      # 提示词里写着谁的声音,就是谁(不看当前绑定)
                    continue
                run = _latest_approved(store, "voice", "character", ch["id"], before)
                if run:
                    proven = voices.compose(json.loads(run["output_json"])) in task["prompt"]
                    out.append(_source(run, "确证" if proven else "按时间推断"))
    else:                                                  # 图片:自己的反推描述(声音只进视频提示词,与图片无关)
        run = _latest_approved(store, f"describe_{task['target_type']}", task["target_type"], task["target_id"], before)
        if run:
            out.append(_source(run, "按时间推断"))
    return out


def siblings(store: Store, task: sqlite3.Row) -> list[dict]:
    """同一写回位置的其它尝试:失败的、被换下的旧版本、在途的。关键帧按「哪一张」分(targets.slot),不按段落混在一起。"""
    writes = json.loads(task["params_json"]).get("writes")
    mine = targets.slot(store, task) if writes else None
    current = targets.current(store, task) if writes else None
    out = []
    for t in store.conn.execute("SELECT * FROM task WHERE target_type = ? AND target_id = ? AND id != ? ORDER BY id DESC",
                                (task["target_type"], task["target_id"], task["id"])):
        theirs = json.loads(t["params_json"]).get("writes")
        if theirs != writes or (writes and targets.slot(store, t) != mine):
            continue
        if t["state"] == "failed":
            note = "失败"
        elif t["state"] != "done":
            note = "在途"
        elif current is not None and t["result_path"] == current:
            note = "当前版"
        else:
            note = "旧版本"
        out.append({"id": t["id"], "note": note, "error": t["error"] or "", "result_path": t["result_path"],
                    "created_at": t["created_at"]})
    return out


def _annotate(store: Store, node: dict) -> dict:
    t = node["task"]
    if node["sha256"] is None:
        node["status"] = "missing"
    elif t is None:
        node["status"] = "manual"
    elif t["result_sha256"] and node["sha256"] != t["result_sha256"]:
        node["status"] = "mismatch"
    else:
        node["status"] = "ok"
    if t is not None:
        row = store.conn.execute("SELECT * FROM task WHERE id = ?", (t["id"],)).fetchone()
        node["target"] = trace._target_label(store, row["target_type"], row["target_id"])
        node["started_at"] = row["started_at"]
        node["llm"] = llm_sources(store, row)
        node["siblings"] = siblings(store, row)
    node["inputs"] = [_annotate(store, i) for i in node["inputs"]]
    return node


def product(store: Store, root: Path, path: str) -> dict:
    """一个产物往回追的树,每个节点标:ok / mismatch(文件被替换过)/ missing(不在本机)/ manual(没有生成记录)。"""
    _inside(root, path)
    return _annotate(store, provenance.node(store, Path(root), path))


def export_for_path(store: Store, path: str) -> int | None:
    row = store.conn.execute("SELECT id FROM export WHERE labeled_path = ? OR plain_path = ?", (path, path)).fetchone()
    return row[0] if row else None


def task_view(store: Store, root: Path, task_id: int) -> dict:
    row = store.conn.execute("SELECT * FROM task WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise KeyError(f"task {task_id} 不存在")
    sub = Path(root) / "outputs" / "tasks" / str(task_id) / SUBMITTED
    return {"row": row, "params": json.loads(row["params_json"]),
            "refs": [{"path": r, "exists": (Path(root) / r).is_file()} for r in json.loads(row["refs_json"])],
            "submitted": sub.read_text(encoding="utf-8") if sub.is_file() else None,
            "target": trace._target_label(store, row["target_type"], row["target_id"]),
            "llm": llm_sources(store, row), "siblings": siblings(store, row),
            "journal": journal.read(root, mentions=("task", task_id))}


def _walk(node: dict):
    yield node
    for i in node["inputs"]:
        yield from _walk(i)


def export_view(store: Store, root: Path, export_id: int) -> dict:
    """导出时的溯源报告 + 之后换过的:①逐段比报告里的视频 / 字幕路径与段落当前的;②每个节点比报告记的 sha256 与现在的文件;
    ③配乐(人工提供)的文件也比 sha256。"""
    row = store.conn.execute("SELECT * FROM export WHERE id = ?", (export_id,)).fetchone()
    if row is None:
        raise KeyError(f"export {export_id} 不存在")
    rep = Path(root) / row["report_path"] if row["report_path"] else None
    if rep is None or not rep.is_file():
        return {"row": row, "report": None, "changed": []}
    report = json.loads(rep.read_text(encoding="utf-8"))
    changed: list[str] = []
    for s in report["segments"]:
        seg = store.conn.execute("SELECT s.* FROM segment s JOIN export x ON x.episode_id = s.episode_id"
                                 " WHERE x.id = ? AND s.number = ?", (export_id, s["number"])).fetchone()
        for field, key, what in (("video_path", "video", "视频"), ("subtitle_path", "subtitles", "字幕")):
            then = s[key]["path"] if s.get(key) else None
            if seg is None:
                changed.append(f"段落 {s['number']} 已删")
                break
            if then and seg[field] != then:
                changed.append(f"段落 {s['number']} 的{what}之后换成了 {seg[field] or '(空)'}")
        for n in (n for k in ("video", "subtitles") if s.get(k) for n in _walk(s[k])):
            f = Path(root) / n["path"]
            if n["sha256"] and (not f.is_file() or provenance.cached_sha256(f) != n["sha256"]):
                changed.append(f"{n['path']} " + ("不在本机了" if not f.is_file() else "的内容与导出时不同"))
    for m in report.get("music", []):        # 早于配乐(v11)的报告没有这一项
        f = Path(root) / m["path"]
        if not f.is_file():
            changed.append(f"配乐 {m['path']} 不在本机了")
        elif provenance.cached_sha256(f) != m["sha256"]:
            changed.append(f"配乐 {m['path']} 的内容与导出时不同")
    return {"row": row, "report": report, "changed": list(dict.fromkeys(changed))}
