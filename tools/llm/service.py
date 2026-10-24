# -*- coding: utf-8 -*-
"""LLM 任务的统一调度:准备(先验前提)→ 查锁定版 → 开一条 llm_run → 闸门 → 提案;人批准 → 写回。

- `begin` 同步完成:前提不满足当场报错(`ValueError`,界面上是「做不了」页),有锁定版就直接复用、不问 LLM;
- `finish` 是真正调 LLM 的部分,控制台放在后台线程里跑(一次 30–80 秒);失败**不吞**:落成 failed 带原因;
- `approve` 用 run 里保存的输入参数**重建上下文**(同时重新核对前提),再调任务模块的 `apply` 写回。
- 会让本集分镜作废的任务(拆分镜、写剧本)对**已有段落**的集也能跑;批准时必须 `confirm_reset=True`
  (人在确认页看过代价)才先删旧段落再写回 —— 生成历史留在 task 表,段落编号永不复用。
- 看图的任务(模块有 `images(ctx)`)要素材根 `data_root` 找图;图片的 sha256 进输入指纹,换了图锁定版就不再命中。
  任务可以指定自己的模型(`Spec.model`):默认模型看不见图(实测),从图反推描述只能用视觉模型。
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tools.drama import invalidation, repo
from tools.drama.db import Store
from tools.drama.invalidation import Impact
from tools.gen.capability import MINIMAX_H3, QWEN_IMAGE_EDIT_2511
from tools.llm import beats, critique, describe, expand, outline, outline_variants, premise, rewrite, runs, script_revise, script_writer, shot_options, skeleton, storyboard, storyboard_revise, vision_check, voices
from tools.llm.client import LLMError
from tools.llm.gate import GateFailed, input_sha, run_gated

# 2026-10-23 起 LLM 走本机 Claude Code 命令行(`claude_cli.py`;SynScale 连续两天拒绝连接,用户定换掉)。
# 模型用别名,命令行解析成当时的 Sonnet;llm_run 里记的是它实际回报的模型全名。Sonnet 看得见图(实测数对了人数),
# 看图任务不再另配模型 —— 两个常量都留着,任务登记表照旧按「要看图」标注,以后要分开时只改这里
DEFAULT_MODEL = "sonnet"
VISION_MODEL = "sonnet"


@dataclass(frozen=True)
class Spec:
    module: object
    target: str
    context: Callable
    max_tokens: int
    label: str
    max_rounds: int = 3
    model: str | None = None        # 任务专用模型;None = 用后端的默认模型
    resets_segments: bool = False   # 批准会让本集已有段落作废(删掉重来)
    needs_root: bool = False        # context 要不要 data_root(看图任务要找图)


TASKS: dict[str, Spec] = {
    "outline": Spec(outline, "drama", outline.context, 12000, "小说 → 分集大纲"),
    "script_synopsis": Spec(script_writer, "episode", script_writer.context_synopsis, 12000, "梗概 → 剧本",
                            resets_segments=True),
    "script_adapt": Spec(script_writer, "episode", script_writer.context_adapt, 12000, "大纲 + 原文 → 剧本",
                         resets_segments=True),
    # 大纲 → 剧本(spec 2026-10-24-逐集批量出剧本分镜-design.md 第 3 节):只有大纲的集;带前后集大纲
    "script_outline": Spec(script_writer, "episode", script_writer.context_outline, 12000, "大纲 → 剧本",
                           resets_segments=True),
    # 分镜约束最多(段数 / 子镜头 / 台词逐字 / @绑定),实测默认模型 3 轮内约 1/12 收敛不了,多给一轮
    "storyboard": Spec(storyboard, "episode", storyboard.context, 16000, "剧本 → 分镜", max_rounds=4,
                       resets_segments=True),
    "voice": Spec(voices, "character", voices.context, 2000, "角色 → 声音描述"),
    **{f"describe_{t}": Spec(describe, t, lambda store, rid, data_root, t=t: describe.context(store, t, rid, data_root),
                             2000, f"从图反推{describe.KIND[t]}描述", model=VISION_MODEL, needs_root=True)
       for t in describe.FIELDS},
    # 视觉抽检(spec 2026-10-22-连续性保障-design.md 第 5 项):只提示,不写回
    "vision_check": Spec(vision_check, "segment", vision_check.context, 3000, "视觉抽检:首帧和场记表对不对",
                         model=VISION_MODEL, needs_root=True),
    # 评审团(spec 2026-10-23-剧集脑爆系统-design.md 第 3a 节):一个视角一次,只出问题清单,人逐条表态
    "critique": Spec(critique, "episode", critique.context, 4000, "评审团:挑这集剧本的毛病"),
    # 局部改写(第 3b 节):只替换选中的一段;已有段落不删(用户 2026-10-23 定),对得上的段落换新台词、视频保留
    "rewrite": Spec(rewrite, "episode", rewrite.context, 4000, "局部改写:给选中的一段出几个写法"),
    # 立项发散(spec 2026-10-23-剧集脑爆系统-design.md 第 1 节):N 个维度上真不一样的方案,人逐字段挑 / 拼
    "premise": Spec(premise, "drama", premise.context, 6000, "立项发散:出几个真不一样的方案"),
    # 大纲节拍标注(spec 2026-10-23-剧集脑爆系统-design.md 第 2a 节):逐集闭集字段 + 逐字依据;节奏由代码统计、只提示
    "beats": Spec(beats, "drama", beats.context, 8000, "大纲节拍:逐集标注钩子、反转、情绪、卡点"),
    # 单集大纲变体(第 2b 节):本集已有剧本时拒绝
    "outline_variants": Spec(outline_variants, "episode", outline_variants.context, 8000, "单集变体:把这集大纲换几种写法"),
    # 分镜方案(第 4 节):景别 / 机位至少差一项;批准不作废已生成的(用户 2026-10-23 原则)
    "shot_options": Spec(shot_options, "segment", shot_options.context, 6000, "分镜方案:给这段出几个镜头方案"),
    # 原创分集(spec 2026-10-24-原创分集-design.md):骨架 2–3 个方案人挑一个;逐段展开每批 ≤ 30 集,逐批批准
    "skeleton": Spec(skeleton, "drama", skeleton.context, 8000, "原创骨架:出几个整部剧的分幕方案"),
    "expand": Spec(expand, "drama", expand.context, 16000, "逐段展开:按骨架写这一批的分集大纲"),
    # 剧本修改回路(spec 2026-10-24-剧本分镜修改回路-design.md 第 1 节):人提意见,LLM 交逐处替换,按意见整组批准
    "script_revise": Spec(script_revise, "episode", script_revise.context, 8000, "剧本修改:按你的意见逐处改"),
    # 分镜修改回路(第 2 节):逐段逐字段补丁,只拦新引入的问题;已生成的首帧 / 视频不作废,只标过期
    "storyboard_revise": Spec(storyboard_revise, "episode", storyboard_revise.context, 12000,
                              "分镜修改:按你的意见逐段改", max_rounds=4),
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
    images: list


def _model(sp: Spec, llm: LLMBackend) -> str:
    return sp.model or llm.model        # 任务指定了模型就用它(假 LLM 也记它:界面上不许出现「用看不见图的模型反推」)


def spec(task: str) -> Spec:
    if task not in TASKS:
        raise ValueError(f"未知的 LLM 任务 {task},可选:{sorted(TASKS)}")
    return TASKS[task]


def prepare(store: Store, task: str, target_id: int, params: dict, data_root: Path | None = None) -> Prepared:
    sp = spec(task)
    if sp.needs_root and data_root is None:
        raise ValueError(f"任务 {task} 要看图,需要素材根 data_root")
    ctx = sp.context(store, target_id, **params, **({"data_root": data_root} if sp.needs_root else {}))
    m = sp.module
    system, user, schema = m.SYSTEM, m.user_message(ctx), m.schema(ctx)
    images = m.images(ctx) if hasattr(m, "images") else []
    parts = (task, system, user, schema) + ((sorted(hashlib.sha256(b).hexdigest() for b in images),) if images else ())
    return Prepared(task, target_id, params, ctx, system, user, schema, input_sha(*parts), images)


def begin(store: Store, task: str, target_id: int, params: dict, llm: LLMBackend,
          data_root: Path | None = None) -> tuple[int, bool]:
    """返回 (run_id, 是否复用了锁定版)。复用时不会调 LLM。"""
    p = prepare(store, task, target_id, params, data_root)
    hit = runs.locked(store, task=task, target_type=spec(task).target, target_id=target_id, input_sha=p.sha)
    if hit is not None:
        return hit["id"], True
    rid = runs.start(store, task=task, target_type=spec(task).target, target_id=target_id, model=_model(spec(task), llm),
                     input_sha=p.sha, llm_mode=llm.mode, params=params)
    return rid, False


def finish(store: Store, run_id: int, llm: LLMBackend, journal_dir: Path, max_rounds: int | None = None,
           data_root: Path | None = None) -> str | None:
    """真正调 LLM。返回失败类别:None(产出待审)/ "judge"(判卷没过)/ "llm"(LLM 本身:不可用 / 连不上 / 截断 / 拒答)/
    "input"(前提变了)。逐集队列的断路器只数 "llm"(spec 2026-10-24-逐集批量出剧本分镜-design.md 第 4 节)。"""
    row = runs.get(store, run_id)
    try:
        p = prepare(store, row["task"], row["target_id"], runs.params(row), data_root)
        sp = spec(row["task"])
        res = run_gated(client=llm.client, model=row["model"], task=row["task"], system=p.system, user=p.user,
                        tool_name=sp.module.TOOL, description=sp.label, schema=p.schema,
                        judge=sp.module.judge_factory(p.ctx), journal_dir=journal_dir,
                        max_rounds=max_rounds or sp.max_rounds,
                        max_tokens=sp.max_tokens, images=p.images)
    except GateFailed as e:                    # GateFailed 是 LLMError 的子类,必须先接
        runs.fail(store, run_id, str(e), str(e.journal_path))
        return "judge"
    except LLMError as e:                      # 不吞:连不上 / 截断 / 拒答,落成 failed 带原因
        runs.fail(store, run_id, f"{type(e).__name__}: {e}")
        return "llm"
    except ValueError as e:                    # 前提变了
        runs.fail(store, run_id, f"{type(e).__name__}: {e}")
        return "input"
    runs.propose(store, run_id, res.args, str(res.journal_path))
    return None


def reset_cost(store: Store, run_id: int) -> tuple[int, Impact] | None:
    """批准这条会删掉几个段落、作废多少产物;不会删段落的返回 None。"""
    row = runs.get(store, run_id)
    if not spec(row["task"]).resets_segments:
        return None
    n = store.conn.execute("SELECT COUNT(*) FROM segment WHERE episode_id = ?", (row["target_id"],)).fetchone()[0]
    if not n:
        return None
    return n, invalidation.impact(store, "script", row["target_id"], video_cap=MINIMAX_H3, image_cap=QWEN_IMAGE_EDIT_2511)


def approve(store: Store, run_id: int, data_root: Path | None = None, confirm_reset: bool = False,
            decision: dict | None = None):
    row = runs.get(store, run_id)
    if row["state"] != "proposed":
        raise ValueError(f"这条 LLM 产物是 {row['state']} 状态,只有待审的能批准")
    if row["llm_mode"] == "fake":
        raise ValueError("假 LLM 的产物不许通过审批闸门(只用于测试控制台本身)")
    p = prepare(store, row["task"], row["target_id"], runs.params(row), data_root)   # 重新核对前提
    if p.sha != row["input_sha"]:
        raise ValueError("生成之后输入已经变了(剧本 / 资产名单 / 参考图 / 参数有改动)—— 这一版对不上现在的数据,请重跑")
    module = spec(row["task"]).module
    args = runs.output(row)
    needs_decision = hasattr(module, "decision_problems")    # 要人挑的任务(评审团 / 局部改写)
    if needs_decision:
        if decision is None:
            raise ValueError("这条 LLM 产物要你先做选择(采纳哪些问题 / 挑哪个候选)才能批准")
        bad = module.decision_problems(args, decision)
        if bad:
            raise ValueError(";".join(bad))
    elif decision is not None:
        raise ValueError(f"任务 {row['task']} 不需要选择,批准时不该带 decision")
    cost = reset_cost(store, run_id)
    if cost is not None:
        if not confirm_reset:
            raise ValueError(f"批准会删除本集已有的 {cost[0]} 个段落({cost[1].summary()})—— 需要人确认代价")
        repo.delete_segments(store, row["target_id"])
    extra = (decision,) if needs_decision else ()
    result = module.apply(store, row["target_id"], args, p.ctx, *extra)
    # 修改回路的 apply 返回 {"applied": 写回前后, ...}:快照随批准一起落库(不可改),回滚按它恢复
    applied = result.pop("applied", None) if isinstance(result, dict) else None
    runs.approve(store, run_id, decision, applied)
    return result
