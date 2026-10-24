# -*- coding: utf-8 -*-
"""逐集批量出剧本 / 分镜(spec 2026-10-24-逐集批量出剧本分镜-design.md 第 2、5、8、9 节)。

- 每集用哪个入口由代码定:小说集(原文 + 大纲)→ `script_adapt`;只有大纲 → `script_outline`;都没有 → blocked;
- 两态(照搬 `gen/fill.py`):`plan` 只算不改;`confirm` 只认确认页列出的集,逐集再判一次,状态变了的跳过;
- 去重:队列里已有、正在生成、有待审的都算在途,不重复排;
- 剧本批准后:这集没段落、剧本里的角色都在资产里 → 排「拆分镜」;缺角色就不排,面板写明缺什么
  (场景只在一个都没有时拦;地名与资产名对不上只提示 —— 拆分镜会从已有场景里挑最接近的)。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from tools.drama import repo, rules
from tools.drama.db import Store
from tools.llm import llm_queue, runs, script_writer, service

SCRIPT_TASKS = ("script_synopsis", "script_adapt", "script_outline")
EST_SEC = (30, 80)                 # 每集一次 LLM 运行的大致耗时(秒),确认页按区间报
_PLACE = re.compile(r"^【(.+?) · .+?】\s*$", re.M)


@dataclass(frozen=True)
class Item:
    episode_id: int
    number: int
    status: str            # todo / has / in_flight / blocked
    task: str | None
    reason: str


def script_task(ep) -> str | None:
    if ep["source_type"] == "novel":
        return "script_adapt" if ep["source_text"].strip() and ep["outline"].strip() else None
    return "script_outline" if ep["outline"].strip() else None


def busy(store: Store, episode_id: int, tasks: tuple[str, ...]) -> str:
    """在途的原因:队列里已有 / 正在生成 / 有待审的;不在途为空串。"""
    marks = ",".join("?" * len(tasks))
    if store.conn.execute(f"SELECT 1 FROM llm_queue WHERE state = 'waiting' AND target_type = 'episode'"
                          f" AND target_id = ? AND task IN ({marks})", (episode_id, *tasks)).fetchone():
        return "已在队列里"
    r = store.conn.execute(f"SELECT state FROM llm_run WHERE target_type = 'episode' AND target_id = ?"
                           f" AND task IN ({marks}) AND state IN ('running', 'proposed') ORDER BY id DESC LIMIT 1",
                           (episode_id, *tasks)).fetchone()
    return {"running": "正在生成", "proposed": "有待你审的"}[r[0]] if r else ""


def classify(store: Store, ep) -> Item:
    if ep["script"].strip():
        return Item(ep["id"], ep["number"], "has", None, "已有剧本")
    why = busy(store, ep["id"], SCRIPT_TASKS)
    if why:
        return Item(ep["id"], ep["number"], "in_flight", None, why)
    task = script_task(ep)
    if task is None:
        return Item(ep["id"], ep["number"], "blocked", None, "没有大纲(小说集还要有原文)—— 先去「大纲」面板写")
    return Item(ep["id"], ep["number"], "todo", task,
                "按大纲改写原文" if task == "script_adapt" else "按大纲写(带前后集大纲)")


def _episodes(store: Store, drama_id: int):
    return store.conn.execute("SELECT * FROM episode WHERE drama_id = ? ORDER BY number", (drama_id,)).fetchall()


def plan(store: Store, drama_id: int) -> list[Item]:
    repo.get(store, "drama", drama_id)
    return [classify(store, ep) for ep in _episodes(store, drama_id)]


def estimate_minutes(n: int) -> tuple[int, int]:
    return math.ceil(n * EST_SEC[0] / 60), math.ceil(n * EST_SEC[1] / 60)


def confirm(store: Store, drama_id: int, episode_ids: list[int], minutes: int) -> list[int]:
    """只认确认页列出的集;逐集再判一次,还是 todo 的才入队。返回新入队的队列项 id。"""
    if minutes not in script_writer.MINUTES:
        raise ValueError(f"目标时长只能选 {script_writer.MINUTES} 分钟")
    out = []
    for eid in episode_ids:
        ep = store.conn.execute("SELECT * FROM episode WHERE id = ? AND drama_id = ?", (eid, drama_id)).fetchone()
        if ep is None:
            continue
        it = classify(store, ep)
        if it.status == "todo":
            out.append(llm_queue.enqueue(store, it.task, "episode", eid, {"minutes": minutes}))
    return out


def _names(store: Store, table: str, drama_id: int) -> list[str]:
    return [r[0] for r in store.conn.execute(f"SELECT name FROM {table} WHERE drama_id = ? ORDER BY id", (drama_id,))]


def storyboard_gaps(store: Store, episode_id: int) -> list[str]:
    """拆分镜前缺的资产(拦):没有场景 / 没有角色 / 剧本里的说话人不在角色表。"""
    ep = repo.get(store, "episode", episode_id)
    chars, scenes = _names(store, "character", ep["drama_id"]), _names(store, "scene", ep["drama_id"])
    out = [] if scenes else ["这部剧还没有场景"]
    out += [] if chars else ["这部剧还没有角色"]
    return out + [f"角色「{w}」" for w in sorted({w for w, _ in rules.dialogue_lines(ep["script"], chars)} - set(chars))]


def place_hints(store: Store, episode_id: int) -> list[str]:
    """剧本里的地点没有同名场景(只提示:拆分镜会从已有场景里挑最接近的)。"""
    ep = repo.get(store, "episode", episode_id)
    scenes = set(_names(store, "scene", ep["drama_id"]))
    return sorted({p.strip() for p in _PLACE.findall(ep["script"])} - scenes)


def after_script_approved(store: Store, episode_id: int) -> str:
    """整集剧本批准后:没段落、不在途、资产够 → 排「拆分镜」。返回给人看的一句话(不需要说明时为空串)。"""
    if store.conn.execute("SELECT COUNT(*) FROM segment WHERE episode_id = ?", (episode_id,)).fetchone()[0]:
        return ""
    if busy(store, episode_id, ("storyboard",)):
        return ""
    gaps = storyboard_gaps(store, episode_id)
    if gaps:
        return "没排分镜 —— 缺资产:" + "、".join(gaps)
    llm_queue.enqueue(store, "storyboard", "episode", episode_id, {})
    return "已排分镜"


def _cell(store: Store, episode_id: int, tasks: tuple[str, ...], have: str, blocked: str = "") -> dict:
    marks = ",".join("?" * len(tasks))
    q = store.conn.execute(f"SELECT id FROM llm_queue WHERE state = 'waiting' AND target_type = 'episode'"
                           f" AND target_id = ? AND task IN ({marks}) ORDER BY id LIMIT 1", (episode_id, *tasks)).fetchone()
    if q:
        return {"state": "queued", "label": f"排队中(第 {llm_queue.position(store, q[0])} 位)"}
    r = store.conn.execute(f"SELECT * FROM llm_run WHERE target_type = 'episode' AND target_id = ? AND task IN ({marks})"
                           f" ORDER BY id DESC LIMIT 1", (episode_id, *tasks)).fetchone()
    if r is not None and r["state"] == "running":
        return {"state": "running", "label": "生成中", "run": r["id"]}
    if r is not None and r["state"] == "proposed":
        try:
            stale = service.prepare(store, r["task"], episode_id, runs.params(r)).sha != r["input_sha"]
        except ValueError as e:              # 前提没了(如大纲被清空):这一格写原因,不让整页 500
            return {"state": "stale", "label": f"前提变了:{e}", "run": r["id"]}
        if stale:
            return {"state": "stale", "label": "输入已变,要重跑", "run": r["id"]}
        return {"state": "proposed", "label": "待你审", "run": r["id"]}
    if have:
        return {"state": "has", "label": have}
    if r is not None and r["state"] == "failed":
        return {"state": "failed", "label": f"失败:{(r['error'] or '')[:60]}", "run": r["id"]}
    if blocked:
        return {"state": "blocked", "label": blocked}
    return {"state": "none", "label": "—"}


def episode_rows(store: Store, drama_id: int) -> list[dict]:
    rows = []
    for ep in _episodes(store, drama_id):
        n_seg = store.conn.execute("SELECT COUNT(*) FROM segment WHERE episode_id = ?", (ep["id"],)).fetchone()[0]
        has_script = bool(ep["script"].strip())
        gaps = storyboard_gaps(store, ep["id"]) if has_script and not n_seg else []
        rows.append({
            "id": ep["id"], "number": ep["number"], "outline": bool(ep["outline"].strip()),
            "script": _cell(store, ep["id"], SCRIPT_TASKS, f"{rules.count_chars(ep['script'])} 字" if has_script else ""),
            "storyboard": _cell(store, ep["id"], ("storyboard",), f"{n_seg} 段" if n_seg else "",
                                f"缺资产:{'、'.join(gaps)}—— 先去资产里建" if gaps else ""),
        })
    return rows
