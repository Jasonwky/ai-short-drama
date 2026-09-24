# -*- coding: utf-8 -*-
"""LLM 任务的统一调度:准备(先验前提)→ 查锁定版 → 开一条 llm_run → 闸门 → 提案;人批准 → 写回。

- `begin` 同步完成:前提不满足当场报错(`ValueError`,界面上是「做不了」页),有锁定版就直接复用、不问 LLM;
- `finish` 是真正调 LLM 的部分,控制台放在后台线程里跑(一次 30–80 秒);失败**不吞**:落成 failed 带原因;
- `approve` 用 run 里保存的输入参数**重建上下文**(同时重新核对前提),再调任务模块的 `apply` 写回。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tools.drama.db import Store
from tools.llm import outline, runs, script_writer, storyboard, voices
from tools.llm.client import LLMError
from tools.llm.gate import GateFailed, input_sha, run_gated

# 默认模型由实测选出(spike-results/2026-09-24-SynScale结构化输出实测.md)
DEFAULT_MODEL = "deepseek-v4-flash-0731-new"


@dataclass(frozen=True)
class Spec:
    module: object
    target: str
    context: Callable
    max_tokens: int
    label: str
    max_rounds: int = 3


TASKS: dict[str, Spec] = {
    "outline": Spec(outline, "drama", outline.context, 12000, "小说 → 分集大纲"),
    "script_synopsis": Spec(script_writer, "episode", script_writer.context_synopsis, 12000, "梗概 → 剧本"),
    "script_adapt": Spec(script_writer, "episode", script_writer.context_adapt, 12000, "大纲 + 原文 → 剧本"),
    # 分镜约束最多(段数 / 子镜头 / 台词逐字 / @绑定),实测默认模型 3 轮内约 1/12 收敛不了,多给一轮
    "storyboard": Spec(storyboard, "episode", storyboard.context, 16000, "剧本 → 分镜", max_rounds=4),
    "voice": Spec(voices, "character", voices.context, 2000, "角色 → 声音描述"),
}


@dataclass
class LLMBackend:
    client: object
    model: str
    mode: str            # real / fake


@dataclass(frozen=True)
class Prepared:
    task: str
    target_id: int
    params: dict
    ctx: dict
    system: str
    user: str
    schema: dict
    sha: str


def spec(task: str) -> Spec:
    if task not in TASKS:
        raise ValueError(f"未知的 LLM 任务 {task},可选:{sorted(TASKS)}")
    return TASKS[task]


def prepare(store: Store, task: str, target_id: int, params: dict) -> Prepared:
    sp = spec(task)
    ctx = sp.context(store, target_id, **params)
    m = sp.module
    system, user, schema = m.SYSTEM, m.user_message(ctx), m.schema(ctx)
    return Prepared(task, target_id, params, ctx, system, user, schema, input_sha(task, system, user, schema))


def begin(store: Store, task: str, target_id: int, params: dict, llm: LLMBackend) -> tuple[int, bool]:
    """返回 (run_id, 是否复用了锁定版)。复用时不会调 LLM。"""
    p = prepare(store, task, target_id, params)
    hit = runs.locked(store, task=task, target_type=spec(task).target, target_id=target_id, input_sha=p.sha)
    if hit is not None:
        return hit["id"], True
    rid = runs.start(store, task=task, target_type=spec(task).target, target_id=target_id, model=llm.model,
                     input_sha=p.sha, llm_mode=llm.mode, params=params)
    return rid, False


def finish(store: Store, run_id: int, llm: LLMBackend, journal_dir: Path, max_rounds: int | None = None) -> None:
    row = runs.get(store, run_id)
    try:
        p = prepare(store, row["task"], row["target_id"], runs.params(row))
        sp = spec(row["task"])
        res = run_gated(client=llm.client, model=row["model"], task=row["task"], system=p.system, user=p.user,
                        tool_name=sp.module.TOOL, description=sp.label, schema=p.schema,
                        judge=sp.module.judge_factory(p.ctx), journal_dir=journal_dir,
                        max_rounds=max_rounds or sp.max_rounds,
                        max_tokens=sp.max_tokens)
    except GateFailed as e:
        runs.fail(store, run_id, str(e), str(e.journal_path))
    except (LLMError, ValueError) as e:        # 不吞:连不上 / 截断 / 拒答 / 前提变了,都落成 failed 带原因
        runs.fail(store, run_id, f"{type(e).__name__}: {e}")
    else:
        runs.propose(store, run_id, res.args, str(res.journal_path))


def approve(store: Store, run_id: int):
    row = runs.get(store, run_id)
    if row["state"] != "proposed":
        raise ValueError(f"这条 LLM 产物是 {row['state']} 状态,只有待审的能批准")
    if row["llm_mode"] == "fake":
        raise ValueError("假 LLM 的产物不许通过审批闸门(只用于测试控制台本身)")
    p = prepare(store, row["task"], row["target_id"], runs.params(row))   # 重新核对前提
    if p.sha != row["input_sha"]:
        raise ValueError("生成之后输入已经变了(剧本 / 资产名单 / 参数有改动)—— 这一版对不上现在的数据,请重跑")
    result = spec(row["task"]).module.apply(store, row["target_id"], runs.output(row), p.ctx)
    runs.approve(store, run_id)
    return result
