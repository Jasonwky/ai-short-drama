# -*- coding: utf-8 -*-
"""ComfyUI API 格式工作流(节点 id → {class_type, inputs})上的纯函数。可单测,不碰网络、不碰库。

工作流编辑(设计 `specs/2026-10-08-ComfyUI工作流编辑-design.md`)的两种用法都靠这里:
- **模板预设**:画布改后的图与「标准例子」比,提取三类改动(改参数 / 加节点 / 删节点);用的时候叠到控制台拼好的图上;
- **改参重跑**:比对归控制台的字段有没有被改(锁定)。

**哪些字段归控制台**:按模板家族(qwen / h3)写死在 OWNED 里 —— 提示词、参考图节点、种子、宽高 / 帧数、锚点、输出文件名前缀。
这些每次由控制台按段落填,画布里改了也不算数(预设)或直接拒绝(改参重跑)。
"""
from __future__ import annotations

import copy

# 模板类别 → 家族(决定哪些字段归控制台)
FAMILY = {"frame": "qwen", "fourview": "qwen", "asset": "qwen", "video": "h3"}

# 整个节点归控制台(参考图槽、尺寸 latent 等)
_OWNED_NODES = {"qwen": {"18", "19", "20", "25", "26", "30"}, "h3": {"8", "9", "10", "11"}}
# 节点的部分输入归控制台
_OWNED_INPUTS = {
    "qwen": {"21": {"prompt", "image1", "image2", "image3"}, "27": {"seed", "latent_image"},
             "29": {"filename_prefix"}},
    "h3": {"12": {"prompt", "width", "height", "length", "ref_images.ref_image_0", "ref_images.ref_image_1",
                  "ref_images.ref_image_2", "ref_audios.ref_audio_0"},
           "13": {"conditioning"}, "19": {"noise_seed"}, "24": {"filename_prefix"}},
}
_H3_DYNAMIC_FROM = 40          # build_h3 的锚点节点从 40 起编号,整段归控制台

MODEL_EXT = (".safetensors", ".gguf", ".ckpt", ".pt", ".pth", ".bin", ".sft")
SEED_INPUTS = ("seed", "noise_seed")


def owned_node(family: str, node_id: str) -> bool:
    if node_id in _OWNED_NODES[family]:
        return True
    return family == "h3" and node_id.isdigit() and int(node_id) >= _H3_DYNAMIC_FROM


def owned_input(family: str, node_id: str, name: str) -> bool:
    return owned_node(family, node_id) or name in _OWNED_INPUTS[family].get(node_id, set())


def _is_link(v) -> bool:
    return isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and isinstance(v[1], int)


def _sort_key(node_id: str):
    return (0, int(node_id)) if node_id.isdigit() else (1, node_id)


def diff(base: dict, edited: dict, family: str, default_of=lambda cls, name: None) -> tuple[dict, list[str]]:
    """edited 相对 base 的改动,返回 (改动, 问题)。

    改动 = {"set": {节点: {输入: 值}}, "unset": {节点: [输入]}, "add": {u1: 节点}, "remove": [节点]}。
    - 归控制台的字段一律忽略;删了归控制台的节点 → 问题
    - 新加节点改名 u1、u2……(避开控制台动态生成的编号),连到它们的连线一并改名
    - 前端会给模板没写的可选输入补默认值(实测 `CreateVideo.color_space = sRGB`):等于 default_of 给的默认值就不算改动
    """
    problems = []
    rename = {nid: f"u{i}" for i, nid in enumerate(sorted((n for n in edited if n not in base), key=_sort_key), 1)}

    def ren(v):
        return [rename.get(v[0], v[0]), v[1]] if _is_link(v) else v

    out = {"set": {}, "unset": {}, "add": {}, "remove": []}
    for nid in sorted(base, key=_sort_key):
        if nid not in edited:
            if owned_node(family, nid) or nid in _OWNED_INPUTS[family]:
                problems.append(f"节点 {nid}({base[nid]['class_type']})归控制台,不能删")
            else:
                out["remove"].append(nid)
            continue
        if edited[nid]["class_type"] != base[nid]["class_type"]:
            problems.append(f"节点 {nid} 的类型从 {base[nid]['class_type']} 变成了 {edited[nid]['class_type']}")
            continue
        if owned_node(family, nid):
            continue
        old, new = base[nid]["inputs"], edited[nid]["inputs"]
        cls = base[nid]["class_type"]
        for name, val in new.items():
            if owned_input(family, nid, name):
                continue
            val = ren(val)
            if name in old:
                if val != old[name]:
                    out["set"].setdefault(nid, {})[name] = val
            elif val != default_of(cls, name):
                out["set"].setdefault(nid, {})[name] = val
        for name in old:
            if name not in new and not owned_input(family, nid, name):
                out["unset"].setdefault(nid, []).append(name)
    for nid, new_id in rename.items():
        node = copy.deepcopy(edited[nid])
        node["inputs"] = {k: ren(v) for k, v in node["inputs"].items()}
        node.pop("_meta", None)
        out["add"][new_id] = node
    return out, problems


def is_empty(d: dict) -> bool:
    return not (d["set"] or d["unset"] or d["add"] or d["remove"])


def apply(wf: dict, d: dict) -> dict:
    """把改动叠到控制台拼好的图上,返回新图。叠不上(改的节点这次不存在、连线断了)直接抛 ValueError,不静默跳过。"""
    wf = copy.deepcopy(wf)
    problems = []
    for nid in d["remove"]:
        if nid not in wf:
            problems.append(f"预设要删的节点 {nid} 这次不存在")
        wf.pop(nid, None)
    for nid, node in d["add"].items():
        if nid in wf:                    # diff 把新节点改名成 u1、u2……,正常撞不上;撞上就是改动坏了,不许顶掉锚点等节点
            problems.append(f"预设要加的节点 {nid} 与已有节点重号")
            continue
        wf[nid] = copy.deepcopy(node)
    for nid, inputs in d["set"].items():
        if nid not in wf:
            problems.append(f"预设要改的节点 {nid} 这次不存在")
            continue
        wf[nid]["inputs"].update(copy.deepcopy(inputs))
    for nid, names in d["unset"].items():
        if nid in wf:
            for name in names:
                wf[nid]["inputs"].pop(name, None)
    problems += [f"连线断了:{p}" for p in dangling(wf)]
    if problems:
        raise ValueError(";".join(problems))
    return wf


def dangling(wf: dict) -> list[str]:
    return [f"{nid}.{k} → {v[0]}" for nid, n in wf.items() for k, v in n["inputs"].items()
            if _is_link(v) and v[0] not in wf]


def summary(d: dict, base: dict) -> list[str]:
    """给人看的改动清单(确认页、工作流面板)。"""
    lines = []
    for nid, inputs in d["set"].items():
        cls = base.get(nid, {}).get("class_type", "?")
        for name, val in inputs.items():
            old = base.get(nid, {}).get("inputs", {}).get(name, "(无)")
            lines.append(f"节点 {nid} {cls}.{name}:{_show(old)} → {_show(val)}")
    for nid, names in d["unset"].items():
        lines += [f"节点 {nid} {base.get(nid, {}).get('class_type', '?')}.{n}:去掉" for n in names]
    lines += [f"新增节点 {nid} {n['class_type']}" for nid, n in d["add"].items()]
    lines += [f"删掉节点 {nid} {base.get(nid, {}).get('class_type', '?')}" for nid in d["remove"]]
    return lines


def _show(v) -> str:
    return f"连到 {v[0]}" if _is_link(v) else repr(v)


def lock_problems(source: dict, edited: dict, family: str) -> list[str]:
    """改参重跑:归控制台的字段(提示词、参考图、尺寸、帧数、锚点、种子以外的)必须和原任务一模一样。"""
    problems = []
    for nid in sorted(set(source) | set(edited), key=_sort_key):
        a, b = source.get(nid), edited.get(nid)
        if owned_node(family, nid):
            if (a and a["inputs"]) != (b and b["inputs"]) or (a and a["class_type"]) != (b and b["class_type"]):
                problems.append(f"节点 {nid}({(a or b)['class_type']})归控制台,不能在画布里改")
            continue
        for name in _OWNED_INPUTS[family].get(nid, set()):
            if name in SEED_INPUTS:
                continue                     # 种子允许改(改参重跑最常见的就是换种子)
            va = a["inputs"].get(name) if a else None
            vb = b["inputs"].get(name) if b else None
            if va != vb:
                problems.append(f"{nid}.{name} 归控制台,不能在画布里改 —— 提示词 / 尺寸 / 帧数请去段落或资产里改")
    return problems


def model_files(wf: dict) -> list[tuple[str, str, str]]:
    """图里用到的模型文件 (节点, 输入, 文件名):按扩展名认。"""
    return [(nid, k, v) for nid, n in wf.items() for k, v in n["inputs"].items()
            if isinstance(v, str) and v.lower().endswith(MODEL_EXT)]


def output_nodes(wf: dict, object_info: dict) -> list[str]:
    return [nid for nid, n in wf.items() if object_info.get(n["class_type"], {}).get("output_node")]


def seeds(wf: dict) -> list[tuple[str, str, int]]:
    return [(nid, k, v) for nid, n in wf.items() for k, v in n["inputs"].items()
            if k in SEED_INPUTS and isinstance(v, int) and not isinstance(v, bool)]


def load_images(wf: dict) -> dict[str, str]:
    return {nid: n["inputs"]["image"] for nid, n in wf.items()
            if n["class_type"] == "LoadImage" and isinstance(n["inputs"].get("image"), str)}


def with_images(wf: dict, names: dict[str, str]) -> dict:
    """把「加载图像」节点里的路径按 names 换掉(相对路径 ↔ 上传名)。"""
    wf = copy.deepcopy(wf)
    for nid in load_images(wf):
        wf[nid]["inputs"]["image"] = names.get(wf[nid]["inputs"]["image"], wf[nid]["inputs"]["image"])
    return wf


def steps(wf: dict) -> int | None:
    """H3 视频的采样步数(BasicScheduler.steps),估算耗时用;找不到返回 None(调用方用默认步数)。"""
    for n in wf.values():
        if n["class_type"] == "BasicScheduler" and isinstance(n["inputs"].get("steps"), int):
            return n["inputs"]["steps"]
    return None
