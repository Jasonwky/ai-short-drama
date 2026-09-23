# -*- coding: utf-8 -*-
"""推进已交给云机的任务(queued / running / waiting_host):查进度、取产物、写回。

**只认 external_id**:这台 ComfyUI 多个项目共用,按 prompt_id 找自己的任务,不看队列长度。
这也是「接管」的全部机制 —— 控制面重启后,库里在途任务带着 external_id,下一轮照常推进,
不需要单独的恢复流程,**更不会重新提交**(重交就是白烧一次 GPU)。

判定顺序是**先看队列、再看 history**:任务跑完是先进 history、再出队列,
反过来查会在两次请求之间漏掉刚跑完的任务,把它误判成「消失」。
即便如此,闸门 0 实测 history 偶尔会滞后,所以「消失」要连续 GRACE_SEC 才判失败。

连不上云机 → `waiting_host`,不是失败;连续 24 小时连不上才放弃。

产物落在 `<data_root>/outputs/tasks/<task_id>/`,库里存相对 data_root 的路径和 sha256。
**写回只填空位**:目标字段已有产物时不覆盖 —— 新版本留在任务记录里等人挑,
否则会悄悄换掉一张首帧,而由旧首帧生成的视频还挂在那里。
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.drama import repo
from tools.drama.db import Store
from tools.drama.tasks import set_task_state
from tools.gen.comfy_client import ComfyUnreachable
from tools.serve.dispatch import Outcome, sha256_file

GRACE_SEC = 120
HOST_GIVEUP = timedelta(hours=24)
IN_FLIGHT = ("queued", "running", "waiting_host")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Collector:
    def __init__(self, data_root: Path, clock: Callable[[], datetime] = _utcnow):
        self.data_root = Path(data_root)
        self._clock = clock
        self._missing_since: dict[int, datetime] = {}

    def advance(self, store: Store, client, task: sqlite3.Row) -> Outcome:
        tid, pid, state = task["id"], task["external_id"], task["state"]
        try:
            running, pending = client.queue_ids()
            rec = client.history(pid)
        except ComfyUnreachable as e:
            return self._unreachable(store, task, e)

        if state == "waiting_host":           # 云机回来了:先回到在途,再按实际情况推进
            set_task_state(store, tid, "queued")
            state = "queued"

        if rec is not None:
            self._missing_since.pop(tid, None)
            return self._finish(store, client, task, rec)
        if pid in running or pid in pending:
            self._missing_since.pop(tid, None)
            if pid in running and state == "queued":
                set_task_state(store, tid, "running")
                return Outcome("running")
            return Outcome(state)

        since = self._missing_since.setdefault(tid, self._clock())
        if (self._clock() - since).total_seconds() < GRACE_SEC:
            return Outcome(state, "队列和 history 里都暂时找不到,观察中")
        self._missing_since.pop(tid, None)
        msg = f"云机上找不到任务 {pid}(不在队列也不在 history,已持续 {GRACE_SEC}s)—— ComfyUI 可能被重启过"
        set_task_state(store, tid, "failed", error=msg)
        return Outcome("failed", msg)

    def _unreachable(self, store: Store, task: sqlite3.Row, e: Exception) -> Outcome:
        task = repo.get(store, "task", task["id"])   # 本轮可能已迁移过状态,手里那行是旧的
        tid, state = task["id"], task["state"]
        if state != "waiting_host":
            set_task_state(store, tid, "waiting_host")
            return Outcome("waiting_host", str(e))
        waited = self._clock() - datetime.fromisoformat(task["state_since"])
        if waited > HOST_GIVEUP:
            msg = f"云机连续 {waited} 不可达,放弃。任务 {task['external_id']} 若仍在云机上,产物需人工核对"
            set_task_state(store, tid, "failed", error=msg)
            return Outcome("failed", msg)
        return Outcome("waiting_host", str(e))

    def _finish(self, store: Store, client, task: sqlite3.Row, rec: dict) -> Outcome:
        tid = task["id"]
        status = rec.get("status") or {}
        if status.get("status_str") == "error" or status.get("completed") is False:
            msg = f"ComfyUI 执行失败:{json.dumps(status.get('messages', []), ensure_ascii=False)[:800]}"
            set_task_state(store, tid, "failed", error=msg)
            return Outcome("failed", msg)

        p = json.loads(task["params_json"])
        items = [i for i in client.outputs(rec) if i["filename"].lower().endswith(p["expect_ext"])]
        if len(items) != 1:
            msg = f"期望恰好 1 个 {p['expect_ext']} 产物,实际 {len(items)} 个:{[i['filename'] for i in items]}"
            set_task_state(store, tid, "failed", error=msg)
            return Outcome("failed", msg)
        try:
            local = client.fetch(items[0], self.data_root / "outputs" / "tasks" / str(tid))
        except ComfyUnreachable as e:
            return self._unreachable(store, task, e)
        rel = local.relative_to(self.data_root).as_posix()

        target = repo.get(store, task["target_type"], task["target_id"])
        note = ""
        if target[p["writes"]]:
            note = f"{p['writes']} 已有产物,新版本留在任务记录里待人挑选"
        else:
            repo.update(store, task["target_type"], task["target_id"], **{p["writes"]: rel})
        set_task_state(store, tid, "done", result_path=rel, result_sha256=sha256_file(local))
        return Outcome("done", note or rel)
