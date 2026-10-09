# -*- coding: utf-8 -*-
"""工作流模板预设(设计 §三)。

- 每类模板(首帧 / 尾帧、四视图、资产图、视频)各有一串版本;预设存**相对内置模板的改动**(`graphs.diff` 的三类)。
- 编辑模板:用「标准例子」拼出完整工作流(叠上当前预设)放进画布;保存时与内置的标准例子比,提取改动。
- 保存前把关(两态):列出差异;用各种典型情况(参考图张数 × 锚点个数 × 画幅)**逐一试拼**并过提交把关,
  任一种拼不出或过不了 → 拒绝保存。确认后才落盘(不可改、不可删,再改 = 新版本)。
- 用:**规划建任务时绑定** —— 参数里记预设编号与**改动内容的副本**(任务自带全部信息,离开本库也能复现);
  切换「当前」不影响已经排着的任务。dispatch 时照常拼,再叠上参数里那份改动。
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from tools.drama.db import Store, now
from tools.gen import graph_checks, graphs, nodecache
from tools.gen.workflows import FRAME_SIZE, VIDEO_SIZE, build_h3, build_qwen

CATEGORIES = {"frame": "首帧 / 尾帧", "fourview": "四视图", "asset": "资产图", "video": "视频"}
EXPECT_EXT = {"frame": ".png", "fourview": ".png", "asset": ".png", "video": ".mp4"}
_PH = "placeholder/"                 # 标准例子里参考图的占位名(画布里会标红 —— 参考图归控制台,用时才填)


def _qwen(n: int, size, preset: str = "fast") -> dict:
    prompt = " ".join(f"Picture {i + 1}" for i in range(n)) if n > 1 else "Picture 1"
    return build_qwen(prompt, [f"{_PH}ref{i + 1}.png" for i in range(n)], 1, preset=preset, size=size)


def _h3(n: int, anchors: int, size) -> dict:
    prompt = "".join(f"<Picture {i + 1}> 参考图 {i + 1}。" for i in range(n))
    pins = [(f"{_PH}first.png", 0), (f"{_PH}last.png", -1)][:anchors]
    return build_h3(prompt, [f"{_PH}ref{i + 1}.png" for i in range(n)], None, 192, 1, size=size, anchors=pins)


def _sizes():
    from tools.gen.assets import FOURVIEW_SIZE       # assets 依赖本模块(规划时绑定预设),这里延迟导入免成环
    return FOURVIEW_SIZE


def example(category: str) -> tuple[dict, dict]:
    """编辑模板时放进画布的标准例子(内置模板)与它的把关参数。"""
    if category == "frame":
        return _qwen(2, FRAME_SIZE["9:16"]), {"size": FRAME_SIZE["9:16"]}
    if category == "fourview":
        return _qwen(1, _sizes()), {"size": _sizes()}
    if category == "asset":
        return _qwen(1, (928, 1664), "baseline"), {"size": (928, 1664)}
    if category == "video":
        return _h3(2, 1, VIDEO_SIZE["9:16"]), {"size": VIDEO_SIZE["9:16"], "frames": 192}
    raise ValueError(f"未知的模板类别 {category},可选:{sorted(CATEGORIES)}")


def trials(category: str) -> list[tuple[str, dict, dict]]:
    """保存前要逐一试拼的典型情况:(说明, 内置拼法, 把关参数)。"""
    out = []
    if category == "frame":
        for aspect, size in FRAME_SIZE.items():
            out += [(f"{n} 张参考图 · {aspect}", _qwen(n, size), {"size": size}) for n in (1, 2, 3)]
    elif category == "fourview":
        out.append(("正面图 → 四视图", _qwen(1, _sizes()), {"size": _sizes()}))
    elif category == "asset":
        for label, size in (("角色正面", (928, 1664)), ("场景 9:16", FRAME_SIZE["9:16"]), ("场景 16:9", FRAME_SIZE["16:9"]),
                            ("道具", (1024, 1024))):
            out.append((label, _qwen(1, size, "baseline"), {"size": size}))
    elif category == "video":
        for aspect, size in VIDEO_SIZE.items():
            out += [(f"{n} 张参考图 · {a} 个锚点 · {aspect}", _h3(n, a, size), {"size": size, "frames": 192})
                    for n in (1, 2, 3) for a in (0, 1, 2)]
    else:
        raise ValueError(f"未知的模板类别 {category}")
    return out


def get(store: Store, preset_id: int):
    row = store.conn.execute("SELECT * FROM workflow_preset WHERE id = ?", (preset_id,)).fetchone()
    if row is None:
        raise KeyError(f"预设 {preset_id} 不存在")
    return row


def versions(store: Store, category: str) -> list:
    return store.conn.execute("SELECT * FROM workflow_preset WHERE category = ? ORDER BY version",
                              (category,)).fetchall()


def active(store: Store, category: str):
    row = store.conn.execute("SELECT p.* FROM workflow_preset_active a JOIN workflow_preset p ON p.id = a.preset_id"
                             " WHERE a.category = ?", (category,)).fetchone()
    return row


def diff_of(row) -> dict:
    return json.loads(row["diff_json"])


def editing_graph(store: Store, category: str) -> dict:
    """打开模板编辑时放进画布的图:标准例子叠上当前预设(在当前版的基础上接着改)。"""
    base, _ = example(category)
    cur = active(store, category)
    return graphs.apply(base, diff_of(cur)) if cur else base


@dataclass(frozen=True)
class SavePlan:
    category: str
    diff: dict
    lines: tuple[str, ...]
    problems: tuple[str, ...]


def plan_save(store: Store, category: str, edited: dict, object_info: dict) -> SavePlan:
    """只算不改:改动清单 + 问题。问题非空就不许保存。"""
    base, _ = example(category)
    family = graphs.FAMILY[category]
    d, problems = graphs.diff(base, edited, family, default_of=nodecache.default_of(object_info))
    cur = active(store, category)
    # 已经有更具体的问题(如删了归控制台的节点)时,不再叠「没有不同」
    if not problems and graphs.is_empty(d):
        problems.append("和内置模板没有任何不同 —— 想回到内置,在「工作流」面板把当前设为「内置」")
    elif not problems and cur is not None and d == diff_of(cur):
        problems.append(f"和当前版本 v{cur['version']} 一样,不用另存")
    if not problems:
        for label, wf, ctx in trials(category):
            try:
                built = graphs.apply(wf, d)
            except ValueError as e:
                problems.append(f"[{label}] 拼不出:{e}")
                continue
            problems += [f"[{label}] {p}" for p in graph_checks.check(
                built, object_info=object_info, family=family, expect_ext=EXPECT_EXT[category], **ctx)]
    return SavePlan(category, d, tuple(graphs.summary(d, base)), tuple(problems))


def confirm_save(store: Store, plan: SavePlan, note: str = "") -> int:
    if plan.problems:
        raise ValueError(";".join(plan.problems))
    cur = active(store, plan.category)
    with store.write() as c:
        version = c.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM workflow_preset WHERE category = ?",
                            (plan.category,)).fetchone()[0]
        return c.execute("INSERT INTO workflow_preset (category, version, diff_json, based_on, note, created_at)"
                         " VALUES (?, ?, ?, ?, ?, ?)",
                         (plan.category, version, json.dumps(plan.diff, ensure_ascii=False, sort_keys=True),
                          cur["id"] if cur else None, note.strip(), now())).lastrowid


def activate(store: Store, category: str, preset_id: int | None) -> None:
    """设为当前(None = 回到内置)。只影响之后新规划的任务。"""
    if category not in CATEGORIES:
        raise ValueError(f"未知的模板类别 {category}")
    if preset_id is not None and get(store, preset_id)["category"] != category:
        raise ValueError(f"预设 {preset_id} 不是「{CATEGORIES[category]}」类的")
    with store.write() as c:
        c.execute("INSERT INTO workflow_preset_active (category, preset_id) VALUES (?, ?)"
                  " ON CONFLICT(category) DO UPDATE SET preset_id = excluded.preset_id", (category, preset_id))


def stamp(store: Store, category: str, params: dict) -> dict:
    """规划建任务时绑定当前预设:参数里记编号与改动副本。没有当前预设 = 内置,参数不变。"""
    cur = active(store, category)
    if cur is None:
        return params
    return {**params, "template": category, "preset_id": cur["id"], "preset_version": cur["version"],
            "preset_diff": diff_of(cur)}


def apply_params(wf: dict, params: dict) -> dict:
    """dispatch 时:内置拼法叠上任务参数里那份改动(没有就原样)。"""
    return graphs.apply(wf, params["preset_diff"]) if "preset_diff" in params else wf


def video_steps(store: Store) -> int | None:
    """当前视频预设改过的采样步数(估算耗时用);没改过 = None(用默认步数)。"""
    cur = active(store, "video")
    if cur is None:
        return None
    base, _ = example("video")
    return graphs.steps(graphs.apply(base, diff_of(cur)))
