# -*- coding: utf-8 -*-
"""留痕:时间线、事件归属、失败归类(设计 specs/2026-10-09-操作留痕查看-design.md §三)。**只读**。

时间线以库里四张表为主(task / llm_run / export / workflow_preset,都带时间戳与目标):
- **只用表里已有的时间戳,不编时间** —— 开跑时刻为空就不出「开跑」,LLM 失败没有单独时间戳就记在发起时刻;
- 本机生成(尾帧衔接、上传)创建即完成,三个时刻相同 → 合并成一条;
- 归属顺着目标往上找;段落被删(重拆分镜)的,顺着参考图追到角色 / 场景 / 道具定剧,追不到就「归属不明」,不硬塞。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from tools.drama.db import Store

KINDS = {"task": "生成任务", "llm": "LLM", "export": "成片导出", "preset": "模板预设"}
TASK_KIND = {"image": "图片", "video": "视频", "align": "字幕对齐"}
PER_PAGE = 100


@dataclass(frozen=True)
class Event:
    at: str                  # UTC ISO,与库里一致
    kind: str                # task / llm / export / preset
    ref: int                 # 该表里的 id
    action: str
    label: str
    href: str
    drama_id: int | None = None
    episode_id: int | None = None
    host_id: int | None = None
    error: str = ""
    is_global: bool = False
    unattributed: bool = False

    @property
    def failed(self) -> bool:
        return self.action == "失败"


@dataclass(frozen=True)
class Filter:
    drama_id: int | None = None
    episode_id: int | None = None
    host_id: int | None = None
    kinds: tuple[str, ...] = ()      # 空 = 全部
    since: str = ""                  # UTC ISO
    until: str = ""
    failed_only: bool = False


# ── 失败归类 ─────────────────────────────────────────────────────────

_WIN_PATH = re.compile(r"[A-Za-z]:[\\/][^\s,;)」]*")
_PATH = re.compile(r"(?:[\w.-]*/)+[\w.-]+")
_NUM = re.compile(r"\d+(?:\.\d+)?")


def failure_key(error: str | None) -> str:
    """**先**换掉路径(含 Windows 盘符路径,否则会在盘符的冒号处截断)、数字,**再**在第一个冒号或「——」处截断。"""
    text = (error or "").strip()
    if not text:
        return "(没有写原因)"
    text = _WIN_PATH.sub("<路径>", text)
    text = _PATH.sub("<路径>", text)
    text = _NUM.sub("#", text)
    return re.split(r"[:：]|——", text, maxsplit=1)[0].strip()


@dataclass(frozen=True)
class FailureGroup:
    key: str
    events: list[Event] = field(default_factory=list)     # 新的在前

    @property
    def count(self) -> int:
        return len(self.events)

    @property
    def latest(self) -> Event:
        return self.events[0]


def failures(store: Store, flt: Filter) -> list[FailureGroup]:
    """条数多的在前;同样多的,最近出过的在前。"""
    groups: dict[str, list[Event]] = {}
    for e in _events(store, Filter(**{**flt.__dict__, "failed_only": True})):
        groups.setdefault(failure_key(e.error), []).append(e)
    out = [FailureGroup(k, v) for k, v in groups.items()]
    out.sort(key=lambda g: g.key)
    out.sort(key=lambda g: g.latest.at, reverse=True)
    out.sort(key=lambda g: g.count, reverse=True)
    return out


# ── 归属 ───────────────────────────────────────────────────────────

_ASSET_PATHS = (("character", ("turnaround_path", "fourview_path")), ("scene", ("image_path",)), ("prop", ("image_path",)))


def _target_owner(store: Store, target_type: str, target_id: int) -> tuple[int | None, int | None] | None:
    """(剧, 集);目标不在了返回 None。"""
    if target_type == "drama":
        return (target_id, None) if store.conn.execute("SELECT 1 FROM drama WHERE id = ?", (target_id,)).fetchone() \
            else None
    if target_type == "episode":
        row = store.conn.execute("SELECT drama_id FROM episode WHERE id = ?", (target_id,)).fetchone()
        return (row[0], target_id) if row else None
    if target_type == "segment":
        row = store.conn.execute("SELECT e.drama_id, e.id FROM segment s JOIN episode e ON e.id = s.episode_id"
                                 " WHERE s.id = ?", (target_id,)).fetchone()
        return (row[0], row[1]) if row else None
    row = store.conn.execute(f"SELECT drama_id FROM {target_type} WHERE id = ?", (target_id,)).fetchone()
    return (row[0], None) if row else None


def _drama_of_path(store: Store, path: str, seen: set[str]) -> int | None:
    """一个参考素材属于哪部剧:是某个资产当前 / 历史的图,或由某条能定剧的任务产出。"""
    if path in seen:
        return None
    seen.add(path)
    for table, cols in _ASSET_PATHS:
        for col in cols:
            row = store.conn.execute(f"SELECT drama_id FROM {table} WHERE {col} = ?", (path,)).fetchone()
            if row:
                return row[0]
    for t in store.conn.execute("SELECT * FROM task WHERE result_path = ? ORDER BY id DESC", (path,)):
        owner = _target_owner(store, t["target_type"], t["target_id"])
        if owner:
            return owner[0]
        for ref in json.loads(t["refs_json"]):
            d = _drama_of_path(store, ref, seen)
            if d is not None:
                return d
    return None


def attribute(store: Store, kind: str, ref: int) -> tuple[int | None, int | None]:
    """事件属于哪部剧 / 哪一集。段落被删的任务顺着参考图定剧,集定不了;追不到 = (None, None)。"""
    if kind == "task":
        t = store.conn.execute("SELECT * FROM task WHERE id = ?", (ref,)).fetchone()
        owner = _target_owner(store, t["target_type"], t["target_id"])
        if owner:
            return owner
        seen: set[str] = set()
        for path in json.loads(t["refs_json"]):
            d = _drama_of_path(store, path, seen)
            if d is not None:
                return d, None
        return None, None
    if kind == "llm":
        r = store.conn.execute("SELECT target_type, target_id FROM llm_run WHERE id = ?", (ref,)).fetchone()
        return _target_owner(store, r["target_type"], r["target_id"]) or (None, None)
    if kind == "export":
        r = store.conn.execute("SELECT episode_id FROM export WHERE id = ?", (ref,)).fetchone()
        return _target_owner(store, "episode", r["episode_id"]) or (None, None)
    return None, None


# ── 时间线 ─────────────────────────────────────────────────────────

def _target_label(store: Store, target_type: str, target_id: int) -> str:
    if target_type == "segment":
        row = store.conn.execute("SELECT s.number, e.number FROM segment s JOIN episode e ON e.id = s.episode_id"
                                 " WHERE s.id = ?", (target_id,)).fetchone()
        return f"第 {row[1]} 集段落 {row[0]}" if row else f"段落 #{target_id}(已删)"
    if target_type == "episode":
        row = store.conn.execute("SELECT number FROM episode WHERE id = ?", (target_id,)).fetchone()
        return f"第 {row[0]} 集" if row else f"集 #{target_id}"
    if target_type == "drama":
        row = store.conn.execute("SELECT title FROM drama WHERE id = ?", (target_id,)).fetchone()
        return f"《{row[0]}》" if row else f"剧 #{target_id}"
    names = {"character": "角色", "scene": "场景", "prop": "道具"}
    row = store.conn.execute(f"SELECT name FROM {target_type} WHERE id = ?", (target_id,)).fetchone()
    return f"{names[target_type]}「{row[0]}」" if row else f"{names[target_type]} #{target_id}"


def _events(store: Store, flt: Filter) -> list[Event]:
    out: list[Event] = []
    kinds = set(flt.kinds) or set(KINDS)
    if flt.host_id is not None:
        kinds &= {"task"}                     # 只有任务经云机
    owners: dict[tuple[str, int], tuple[int | None, int | None]] = {}

    def owner(kind: str, ref: int):
        if (kind, ref) not in owners:
            owners[(kind, ref)] = attribute(store, kind, ref)
        return owners[(kind, ref)]

    def add(at, kind, ref, action, label, href, error="", host_id=None, is_global=False):
        if not at:
            return
        d, ep = (None, None) if is_global else owner(kind, ref)
        out.append(Event(at, kind, ref, action, label, href, d, ep, host_id, error, is_global,
                         unattributed=not is_global and d is None))

    if "task" in kinds:
        for t in store.conn.execute("SELECT * FROM task"):
            label = f"task {t['id']} · {TASK_KIND[t['kind']]} · {_target_label(store, t['target_type'], t['target_id'])}"
            href, host = f"/task/{t['id']}", t["host_id"]
            if t["state"] == "done" and t["external_id"] is None and t["created_at"] == t["finished_at"]:
                upload = json.loads(t["params_json"]).get("workflow") == "upload"
                add(t["created_at"], "task", t["id"], "上传" if upload else "本机生成", label, href, host_id=host)
                continue
            add(t["created_at"], "task", t["id"], "创建", label, href, host_id=host)
            add(t["started_at"], "task", t["id"], "开跑", label, href, host_id=host)
            if t["state"] in ("done", "failed"):
                add(t["finished_at"], "task", t["id"], "完成" if t["state"] == "done" else "失败", label, href,
                    error=t["error"] or "", host_id=host)
    if "llm" in kinds:
        for r in store.conn.execute("SELECT * FROM llm_run"):
            label = f"llm_run {r['id']} · {r['task']} · {_target_label(store, r['target_type'], r['target_id'])}"
            href = f"/llm/run/{r['id']}"
            if r["state"] == "failed":       # 失败没有单独的时间戳:记在发起时刻,附原因
                add(r["created_at"], "llm", r["id"], "失败", label, href, error=r["error"] or "")
                continue
            add(r["created_at"], "llm", r["id"], "发起", label, href)
            if r["state"] in ("approved", "rejected"):
                add(r["decided_at"], "llm", r["id"], "批准" if r["state"] == "approved" else "驳回", label, href)
    if "export" in kinds:
        for x in store.conn.execute("SELECT * FROM export"):
            label = f"export {x['id']} · {_target_label(store, 'episode', x['episode_id'])} · {x['produce_id']}"
            href = f"/trace/export/{x['id']}"
            add(x["created_at"], "export", x["id"], "开始", label, href)
            if x["state"] in ("done", "failed"):
                add(x["finished_at"], "export", x["id"], "完成" if x["state"] == "done" else "失败", label, href,
                    error=x["error"] or "")
    if "preset" in kinds:
        for p in store.conn.execute("SELECT * FROM workflow_preset"):
            add(p["created_at"], "preset", p["id"], "保存", f"模板预设 {p['category']} v{p['version']}",
                "/workflows", is_global=True)

    def keep(e: Event) -> bool:
        if flt.failed_only and not e.failed:
            return False
        if flt.since and e.at < flt.since or flt.until and e.at > flt.until:
            return False
        if flt.host_id is not None and e.host_id != flt.host_id:
            return False
        if flt.episode_id is not None:
            return e.episode_id == flt.episode_id
        if flt.drama_id is not None:
            return e.is_global or e.drama_id == flt.drama_id
        return True

    out = [e for e in out if keep(e)]
    out.sort(key=lambda e: (e.at, e.kind, e.ref), reverse=True)
    return out


def timeline(store: Store, flt: Filter, page: int = 1) -> tuple[list[Event], int]:
    """按时间倒序,每页 100 条;返回 (这一页, 总条数)。"""
    events = _events(store, flt)
    start = (max(page, 1) - 1) * PER_PAGE
    return events[start:start + PER_PAGE], len(events)
