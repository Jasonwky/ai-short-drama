# -*- coding: utf-8 -*-
"""把任务参数拼成 ComfyUI API 格式工作流 —— 纯函数,可单测。

两个模板都来自闸门 0 已在云机跑通的工作流(`tools/gen/workflows/*.json`),
节点 id 与模板绑定:改模板要同步改这里。

规矩:
- **种子由调用方给**,不许随机;
- **提示词先做标签双向校验再拼**,不通过直接抛错 —— 不把错位的标签交给 ComfyUI;
- 未使用的可选输入**整个删掉**,不传 null(ComfyUI 会把 null 当类型错误);
- H3 只出 **20 步档**,并把 LoRA 与开关节点从图里**剪掉**:PDD 加速 LoRA 与云机的剪枝版底模
  不兼容(闸门 0-6),ComfyUI 遇到不兼容只打日志、照样出片,所以图里根本不该出现它。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from tools.drama.tags import check_tags

WORKFLOW_DIR = Path(__file__).parent / "workflows"
QWEN_TEMPLATE = WORKFLOW_DIR / "qwen_image_edit_2511_gguf_api.json"
H3_TEMPLATE = WORKFLOW_DIR / "minimax_h3_ref2va_api.json"

QWEN_MODEL = "qwen-image-edit-2511-Q4_K_M.gguf"
H3_MODEL = "minimax_h3_ref2va_pruned_fp8_scaled.safetensors"

QWEN_PRESETS = {"fast": (4, 1.0), "baseline": (20, 4.0)}   # (步数, CFG);fast 是闸门 0 定的默认档
H3_STEPS = 20
# 草稿档:每步耗时不变、总时长按步数线性降,画质肉眼无差,但 10 / 14 步都出过道具重复、20 步没有
# (spike-results/2026-10-14-开卡调研批次-运镜关键帧步数.md,1 个种子)。用来快速看构图 / 动作,定稿用 H3_STEPS
DRAFT_STEPS = 10
H3_IMAGE_SLOTS = 3                                          # 模板预留的参考图槽(节点 8/9/10)

FRAME_SIZE = {"9:16": (928, 1664), "16:9": (1664, 928)}    # Qwen 原生档
VIDEO_SIZE = {"9:16": (768, 1344), "16:9": (1344, 768)}    # H3 竖屏已在云机跑通


def _load(path: Path) -> dict:
    return copy.deepcopy(json.loads(path.read_text(encoding="utf-8")))


def _raise_if(problems: list[str]) -> None:
    if problems:
        raise ValueError(";".join(problems))


def build_qwen(prompt: str, image_names: list[str], seed: int, *, preset: str = "fast",
               size: tuple[int, int] | None = None, angles_lora: bool = False) -> dict:
    """Qwen-Image-Edit-2511(GGUF)。size 为 None 时输出画幅跟随参考图 1(改背景、出四视图);
    合成分镜首帧**必须给 size** —— 闸门 0-3 实测提示词压不过 latent 尺寸。"""
    if not 1 <= len(image_names) <= 3:
        raise ValueError(f"Qwen-Image-Edit 需要 1–3 张参考图,给了 {len(image_names)} 张")
    _raise_if(check_tags(prompt, len(image_names), dialect="qwen"))
    steps, cfg = QWEN_PRESETS[preset]
    wf = _load(QWEN_TEMPLATE)

    for idx, node in enumerate(("18", "19", "20")):
        if idx < len(image_names):
            wf[node]["inputs"]["image"] = image_names[idx]
        else:
            wf.pop(node)
            wf["21"]["inputs"].pop(f"image{idx + 1}")

    wf["4"]["inputs"]["value"] = angles_lora
    wf["7"]["inputs"]["value"] = preset == "fast"
    wf["10"]["inputs"]["value"] = wf["11"]["inputs"]["value"] = steps
    wf["13"]["inputs"]["value"] = wf["14"]["inputs"]["value"] = cfg
    wf["21"]["inputs"]["prompt"] = prompt
    wf["27"]["inputs"]["seed"] = seed

    if size is None:
        wf.pop("30")
    else:
        w, h = size
        if w % 16 or h % 16:
            raise ValueError(f"尺寸 {w}x{h} 不是 16 的倍数")
        wf["30"]["inputs"]["width"], wf["30"]["inputs"]["height"] = w, h
        wf["27"]["inputs"]["latent_image"] = ["30", 0]
        wf.pop("25")
        wf.pop("26")
    return wf


def build_h3(prompt: str, image_names: list[str], audio_name: str | None, frames: int, seed: int,
             *, size: tuple[int, int], anchors: list[tuple[str, int]] = ()) -> dict:
    """MiniMax H3 ref2va,20 步。frames 须已由 Capability 换算并校验过。

    anchors:[(图名, 帧号)],把图**钉**在那一帧(-1 = 最后一帧)。参考图只是「软」参考 ——
    2026-09-26 云机实测:参考图 ≥ 2 张时,只当参考图喂的首帧第一帧差 74.2(比两张不同图的 35.9 还大),
    串一个 `MiniMaxH3AddGuide` 钉住后第一帧差 11.9、尾帧差 6.5。锚定走条件里的 `minimax_keyframes`,
    ref2va 权重就能用,不需要 I2V 权重(`spike-results/2026-09-26-参考锚定与四视图云机实测.md`)。"""
    if not 1 <= len(image_names) <= H3_IMAGE_SLOTS:
        raise ValueError(f"工作流预留 1–{H3_IMAGE_SLOTS} 个图槽,给了 {len(image_names)} 张")
    _raise_if(check_tags(prompt, len(image_names), 1 if audio_name else 0, dialect="h3"))
    wf = _load(H3_TEMPLATE)

    # 剪掉 LoRA 与步数开关:2 LoRA · 3/4 开关 · 15/16/17 步数分支
    for node in ("2", "3", "4", "15", "16", "17"):
        wf.pop(node)
    wf["13"]["inputs"]["model"] = ["1", 0]
    wf["18"]["inputs"]["model"] = ["1", 0]
    wf["18"]["inputs"]["steps"] = H3_STEPS

    r2v = wf["12"]["inputs"]
    for idx, node in enumerate(("8", "9", "10")):
        if idx < len(image_names):
            wf[node]["inputs"]["image"] = image_names[idx]
        else:
            wf.pop(node)
            r2v.pop(f"ref_images.ref_image_{idx}")
    if audio_name:
        wf["11"]["inputs"]["audio"] = audio_name
    else:
        wf.pop("11")
        r2v.pop("ref_audios.ref_audio_0")

    r2v["prompt"] = prompt
    r2v["length"] = frames
    r2v["width"], r2v["height"] = size
    wf["19"]["inputs"]["noise_seed"] = seed

    positive = ["12", 0]
    for k, (name, frame_idx) in enumerate(anchors):
        load, guide = str(40 + 2 * k), str(41 + 2 * k)
        wf[load] = {"class_type": "LoadImage", "inputs": {"image": name}, "_meta": {"title": f"锚点图 {k + 1}"}}
        wf[guide] = {"class_type": "MiniMaxH3AddGuide",
                     "inputs": {"positive": positive, "vae": ["6", 0], "latent": ["12", 1], "image": [load, 0],
                                "frame_idx": frame_idx},
                     "_meta": {"title": f"锚定到第 {frame_idx} 帧(-1 = 最后一帧)"}}
        positive = [guide, 0]
    wf["13"]["inputs"]["conditioning"] = positive
    return wf
