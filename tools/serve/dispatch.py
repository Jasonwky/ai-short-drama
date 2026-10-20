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
from tools.gen import graphs, preflight, presets
from tools.gen.comfy_client import ComfyError, ComfyUnreachable
from tools.gen.workflows import build_h3, build_qwen, build_qwen_region
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


def _uploads(p: dict) -> list[str]:
    """要传到云机的图:参考图 + 锚点图,去重、保持顺序(首帧常常既是参考图又是锚点)。"""
    return list(dict.fromkeys([*p["image_refs"], *(path for path, _ in p.get("anchors", [])),
                               *p.get("second_pass", {}).get("image_refs", []),
                               *(p[k] for k in ("source", "ref") if k in p)]))      # source / ref:局部替换


SUBMITTED = "workflow.json"     # outputs/tasks/<id>/ 下:实际提交给云机的工作流(改参重跑从它打开)


def build_workflow(task: sqlite3.Row, names: dict[str, str]) -> dict:
    """names:库里的相对路径 → 上传后 ComfyUI 认的文件名。"""
    p = json.loads(task["params_json"])
    if p["workflow"] == "custom":           # 改参重跑:画布改后的整份工作流,只把参考图换成上传名
        return graphs.with_images(p["graph"], names)
    image_names = [names[path] for path in p["image_refs"]]
    if p["workflow"] == "qwen_image":
        sp = p.get("second_pass")
        wf = build_qwen(task["prompt"], image_names, task["seed"], preset=p["preset"], size=tuple(p["size"]),
                        second_pass=(sp["prompt"], [names[r] for r in sp["image_refs"]]) if sp else None)
    elif p["workflow"] == "qwen_region":
        # 不吃首帧模板预设:预设按首帧模板的节点号写,对不上这份图
        return build_qwen_region(task["prompt"], names[p["source"]], names[p["ref"]], tuple(p["box"]),
                                 tuple(p["size"]), task["seed"], preset=p["preset"])
    elif p["workflow"] == "minimax_h3":
        if p["audio_ref"] is not None:
            raise ValueError("参考音频尚未接入(闸门 0-4:喂参考音频会复读内容)")
        wf = build_h3(task["prompt"], image_names, None, p["frames"], task["seed"], size=tuple(p["size"]),
                      anchors=[(names[path], idx) for path, idx in p.get("anchors", [])])
    else:
        raise ValueError(f"未知工作流 {p['workflow']!r}")
    return presets.apply_params(wf, p)      # 规划时绑定的模板预设(没有就原样)


# 少于定稿步数的视频:视频卡片、导出页都提醒(spike-results/2026-10-14-开卡调研批次-运镜关键帧步数.md)
# 2026-10-18 多种子 + 2026-10-19 烧字幕对照:道具重复、烧字幕 20 步一样会出 —— 不暗示「20 步能避开」
FEW_STEPS_NOTE = "约省一半时间,画质与定稿肉眼相近;道具重复、烧字幕这类问题两档都会出,哪档都要逐段看片"


def steps_of(task: sqlite3.Row) -> int | None:
    """这条视频任务交给云机的工作流是几步:拼出那份图再读 BasicScheduler.steps。改参重跑的自定义图、
    预设改过的步数、草稿档三种来源一视同仁。不走 ComfyUI 视频工作流的任务(没有步数)返回 None。"""
    p = json.loads(task["params_json"])
    if p.get("workflow") not in ("minimax_h3", "custom"):
        return None
    return graphs.steps(build_workflow(task, {r: r for r in _uploads(p)}))


def dispatch(store: Store, client, host: Host, task: sqlite3.Row, *, data_root: Path,
             run: preflight.Runner) -> Outcome:
    task_id = task["id"]

    def fail(msg: str) -> Outcome:
        set_task_state(store, task_id, "failed", error=msg)
        return Outcome("failed", msg)

    rels = _uploads(json.loads(task["params_json"]))
    missing = [(Path(data_root) / r).as_posix() for r in rels if not (Path(data_root) / r).is_file()]
    if missing:
        return fail(f"参考图不存在:{', '.join(missing)}")
    try:
        names = {r: client.upload_image(Path(data_root) / r, subfolder=f"ai-short-drama/{sha256_file(Path(data_root) / r)[:16]}")
                 for r in rels}
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
    _save_submitted(Path(data_root), task_id, wf, names)
    return Outcome("queued", pid, pf.warnings)


def _save_submitted(root: Path, task_id: int, wf: dict, names: dict[str, str]) -> None:
    """存下实际提交给云机的工作流(上传名 ↔ 素材库路径的对照一并存):「在 ComfyUI 中编辑」打开的就是它。
    另存一份 sha256,文件被改过就认得出来。"""
    out = root / "outputs" / "tasks" / str(task_id)
    out.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"submitted": wf, "names": names}, ensure_ascii=False, indent=1, sort_keys=True)
    (out / SUBMITTED).write_text(body, encoding="utf-8")
    (out / (SUBMITTED + ".sha256")).write_text(sha256_file(out / SUBMITTED), encoding="utf-8")
