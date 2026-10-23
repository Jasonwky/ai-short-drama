# -*- coding: utf-8 -*-
"""改参重跑(设计 §2.4 / §四):在 ComfyUI 画布里打开某条任务**当时实际提交的工作流**,改完作为同一目标的新版本提交。

- 打开:dispatch 时存下的 `outputs/tasks/<id>/workflow.json`(上传名换回素材库路径);
  老任务没有这个文件 → 按当前代码重建,并标明「可能与当时不同」;文件与它的 sha256 对不上 → 拒绝(被改过);
- 提交前把关:`graph_checks.check`(模型白名单、节点清单、单一输出、尺寸 / 帧数、种子唯一、参考图来自素材库)
  + `graphs.lock_problems`(提示词 / 参考图 / 尺寸 / 帧数 / 锚点归控制台,不许在画布里改)+ 角色种子锁定;
- 任务表的种子从改后的工作流里**提取**(不信表单);提示词与参考图就是原任务的(锁定保证了它们没变)。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tools.drama import repo, targets
from tools.drama.db import Store
from tools.drama.tasks import create_task
from tools.gen import graph_checks, graphs
from tools.gen.assets import locked_seed
from tools.serve.dispatch import SUBMITTED, _uploads, build_workflow, sha256_file

_FAMILY = {"qwen_image": "qwen", "minimax_h3": "h3"}


def editable(task) -> bool:
    """走 ComfyUI 的生成任务才有工作流可编辑;字幕对齐、本机抽尾帧没有。"""
    if task["kind"] not in ("image", "video"):
        return False
    return json.loads(task["params_json"]).get("workflow") in ("qwen_image", "minimax_h3", "custom")


def _params(task) -> dict:
    return json.loads(task["params_json"])


def family(task) -> str:
    p = _params(task)
    return p["family"] if p["workflow"] == "custom" else _FAMILY[p["workflow"]]


def context(task) -> dict:
    """把关参数:输出类型、尺寸、帧数 —— 都取原任务的(改参重跑不许改它们)。"""
    p = _params(task)
    return {"expect_ext": p["expect_ext"], "size": tuple(p["size"]) if p.get("size") else None, "frames": p.get("frames")}


def source_graph(data_root: Path, task) -> tuple[dict, bool]:
    """(这条任务的工作流,参考图写成素材库相对路径;是否按当前代码重建的)。"""
    if not editable(task):
        raise ValueError(f"task {task['id']} 不是走 ComfyUI 的生成任务,没有工作流可编辑")
    f = Path(data_root) / "outputs" / "tasks" / str(task["id"]) / SUBMITTED
    if f.is_file():
        sha = f.with_name(SUBMITTED + ".sha256")
        if not sha.is_file() or sha.read_text(encoding="utf-8").strip() != sha256_file(f):
            raise ValueError(f"{f.name} 和它记下的 sha256 对不上 —— 文件被改过,不能当作「当时提交的工作流」")
        data = json.loads(f.read_text(encoding="utf-8"))
        return graphs.with_images(data["submitted"], {name: rel for rel, name in data["names"].items()}), False
    p = _params(task)
    if p["workflow"] == "custom":
        return p["graph"], False
    return build_workflow(task, {r: r for r in _uploads(p)}), True


def plan_rerun(store: Store, data_root: Path, task_id: int, graph: dict, object_info: dict) -> tuple[int | None, list[str]]:
    """把关通过就建一条同目标的新任务,返回 (新任务 id, []);不过返回 (None, 问题)。"""
    src = repo.get(store, "task", task_id)
    base, _ = source_graph(data_root, src)
    fam = family(src)
    problems = graph_checks.check(graph, object_info=object_info, family=fam, data_root=Path(data_root), **context(src))
    problems += graphs.lock_problems(base, graph, fam)
    found = graphs.seeds(graph)
    p = _params(src)
    key = {}
    if p["writes"] == targets.KEY_WRITES:      # 关键帧:新版本写回同一张(P12 时代的旧任务也解析成具体行号带上)
        key["keyframe_id"] = targets.keyframe_id(store, src)
        if key["keyframe_id"] is None:
            problems.append("这张关键帧已经删除,不能再为它出新版本")
    if src["target_type"] == "character" and found:
        locked = locked_seed(store, "character", src["target_id"])
        if locked is not None and found[0][2] != locked:
            problems.append(f"种子已锁定为 {locked} —— 要换种子先在资产面板解锁")
    if problems:
        return None, problems
    refs = list(dict.fromkeys(graphs.load_images(graph).values()))
    # 台本快照原样带上:重跑用的是原任务的提示词(prompt=src["prompt"]),不重读段落当前内容,
    # 所以「它被要求说什么」仍是原任务那份快照;现取的话反而会记成它没被要求说的台词
    snap = {k: p[k] for k in ("script", "frame") if k in p}      # 参考帧同理:带上当时的图提示词拼句
    params = {**snap, "workflow": "custom", "family": fam, "graph": graph, "from_task": task_id, "image_refs": refs,
              "writes": p["writes"], **key, "expect_ext": p["expect_ext"], "size": p.get("size"), "frames": p.get("frames")}
    tid = create_task(store, kind=src["kind"], target_type=src["target_type"], target_id=src["target_id"],
                      model=src["model"], params=params, seed=found[0][2], prompt=src["prompt"], refs=refs)
    return tid, []


def history_link(task: sqlite3.Row) -> str | None:
    return f"/comfy-edit/task/{task['id']}" if editable(task) else None
