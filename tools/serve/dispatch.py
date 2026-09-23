# -*- coding: utf-8 -*-
"""把一条 pending 任务交给云机:上传参考图 → 拼工作流 → LoRA 预检 → 提交。

任务的提示词、参数、种子在规划时已定死;这里**只做搬运**,不改任何生成参数。

结局只有三种,每种都落到任务状态上,不存在「提交了但库里没记」:
- 提交成功 → `queued`,记下 `external_id`(prompt_id)与 `host_id` —— 控制面重启后靠它接管;
- 连不上云机 → **留在 `pending`**,下一轮再试。还没交出去,不算失败;
- 其余一切(参考图丢了、标签错位、预检不过、ComfyUI 拒收)→ `failed` 并写明原因。
  这些重试也不会好,要人看。

参考图按**内容哈希分子目录**上传(`ai-short-drama/<sha256 前 16 位>/文件名`):
不同任务的产物常常同名(都叫 ComfyUI_00001_.png),按文件名传,后一张会在云机上覆盖前一张;
这台 ComfyUI 还是多个项目共用的 input 目录。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from tools.drama.db import Store
from tools.drama.tasks import set_task_state
from tools.gen import preflight
from tools.gen.comfy_client import ComfyError, ComfyUnreachable
from tools.gen.workflows import build_h3, build_qwen
from tools.serve.hosts import Host


@dataclass(frozen=True)
class Outcome:
    state: str                    # 任务派发后的状态:queued / pending / failed
    message: str = ""
    warnings: tuple[str, ...] = ()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pick_next(store: Store, host_id: int) -> sqlite3.Row | None:
    """挑下一条要派的 pending 任务。

    **按模型分组**:优先挑和这台机器上一条派发同模型的 —— 换模型要重新加载几十 GB 权重。
    同模型内、以及没有同模型任务时,都按创建先后。
    """
    pending = store.conn.execute("SELECT * FROM task WHERE state = 'pending' ORDER BY id").fetchall()
    if not pending:
        return None
    last = store.conn.execute("SELECT model FROM task WHERE host_id = ? AND external_id IS NOT NULL"
                              " ORDER BY id DESC LIMIT 1", (host_id,)).fetchone()
    if last is not None:
        for row in pending:
            if row["model"] == last["model"]:
                return row
    return pending[0]


def build_workflow(task: sqlite3.Row, image_names: list[str]) -> dict:
    p = json.loads(task["params_json"])
    if p["workflow"] == "qwen_image":
        return build_qwen(task["prompt"], image_names, task["seed"], preset=p["preset"], size=tuple(p["size"]))
    if p["workflow"] == "minimax_h3":
        if p["audio_ref"] is not None:
            raise ValueError("参考音频尚未接入(闸门 0-4:喂参考音频会复读内容)")
        return build_h3(task["prompt"], image_names, None, p["frames"], task["seed"], size=tuple(p["size"]))
    raise ValueError(f"未知工作流 {p['workflow']!r}")


def dispatch(store: Store, client, host: Host, task: sqlite3.Row, *, data_root: Path,
             run: preflight.Runner) -> Outcome:
    task_id = task["id"]

    def fail(msg: str) -> Outcome:
        set_task_state(store, task_id, "failed", error=msg)
        return Outcome("failed", msg)

    paths = [Path(data_root) / p for p in json.loads(task["params_json"])["image_refs"]]
    missing = [p.as_posix() for p in paths if not p.is_file()]
    if missing:
        return fail(f"参考图不存在:{', '.join(missing)}")
    try:
        names = [client.upload_image(p, subfolder=f"ai-short-drama/{sha256_file(p)[:16]}") for p in paths]
        wf = build_workflow(task, names)
    except ComfyUnreachable as e:
        return Outcome("pending", f"连不上云机,下轮再试:{e}")
    except ValueError as e:
        return fail(f"拼工作流失败:{e}")

    pf = preflight.check(wf, host.models_dir, run)
    if not pf.ok:
        return fail(";".join(pf.problems))

    try:
        pid = client.submit(wf)
    except ComfyUnreachable as e:
        return Outcome("pending", f"连不上云机,下轮再试:{e}", pf.warnings)
    except ComfyError as e:
        return fail(f"ComfyUI 拒收:{e}")
    set_task_state(store, task_id, "queued", external_id=pid, host_id=host.id)
    return Outcome("queued", pid, pf.warnings)
