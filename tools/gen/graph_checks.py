# -*- coding: utf-8 -*-
"""工作流的提交把关(设计 §四):画布改出来的图和 LLM 输出一样,是要被审的输入。

`check()` 只做确定性检查,返回问题清单(空 = 通过),不改任何东西:
连线、节点类型与模型在节点清单里、模型白名单、恰好一个输出、尺寸 / 帧数、种子唯一、参考图来自素材库。
控制台字段锁定(改参重跑)见 `graphs.lock_problems`;种子锁定见 `rerun`。
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath

from tools.gen import graphs
from tools.gen.capability import ALLOWED_MODELS

OUTPUT_CLASSES = {".png": {"SaveImage"}, ".mp4": {"SaveVideo"}}
_SIZE_NODE = {"qwen": "30", "h3": "12"}       # 宽高在哪个节点(与 graphs.OWNED 一致)


def check(wf: dict, *, object_info: dict, family: str, expect_ext: str, size: tuple[int, int] | None,
          frames: int | None = None, data_root: Path | None = None) -> list[str]:
    problems = [f"连线断了:{d}" for d in graphs.dangling(wf)]

    unknown = sorted({n["class_type"] for n in wf.values() if n["class_type"] not in object_info})
    problems += [f"节点类型 {c} 不在节点清单里(云机没装?)" for c in unknown]

    allowed = {m.name for m in ALLOWED_MODELS}
    for nid, name, value in graphs.model_files(wf):
        if value not in allowed:
            problems.append(f"模型 {value}(节点 {nid}.{name})没有登记许可证 —— 白名单见 capability.ALLOWED_MODELS")

    for nid, n in wf.items():
        if n["class_type"] == "LoadImage" or n["class_type"] not in object_info:
            continue
        spec = object_info[n["class_type"]]["input"]
        spec = {**spec.get("required", {}), **spec.get("optional", {})}
        for k, v in n["inputs"].items():
            options = spec.get(k, [None])[0]
            if isinstance(options, list) and isinstance(v, str) and v not in options:
                problems.append(f"{nid}.{k} = {v!r} 不在云机的可选项里")

    outs = graphs.output_nodes(wf, object_info)
    want = OUTPUT_CLASSES[expect_ext]
    if len(outs) != 1 or wf[outs[0]]["class_type"] not in want:
        found = [f"{o}({wf[o]['class_type']})" for o in outs]
        problems.append(f"要恰好 1 个输出节点({' / '.join(sorted(want))}),实际 {found or '没有'}")

    node = wf.get(_SIZE_NODE[family], {}).get("inputs", {})
    if size is not None and (node.get("width"), node.get("height")) != tuple(size):
        problems.append(f"尺寸应为 {size[0]}x{size[1]}(剧的画幅),实际 {node.get('width')}x{node.get('height')}")
    if frames is not None and node.get("length") != frames:
        problems.append(f"帧数应为 {frames}(由段落时长换算),实际 {node.get('length')}")

    if len(graphs.seeds(wf)) != 1:
        problems.append(f"要恰好 1 个种子,实际 {[f'{n}.{k}' for n, k, _ in graphs.seeds(wf)] or '没有'} —— 溯源记不下来")

    if data_root is not None:
        for nid, rel in graphs.load_images(wf).items():
            p = PurePosixPath(rel)
            if p.is_absolute() or ".." in p.parts or not (Path(data_root) / rel).is_file():
                problems.append(f"参考图 {rel}(节点 {nid})不在素材库里 —— 只认素材库的相对路径,新图请去资产面板上传")
    return problems
