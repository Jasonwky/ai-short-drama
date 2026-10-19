# -*- coding: utf-8 -*-
"""参考帧的「第二步」:两遍合成的第二遍、框选局部替换 —— 提示词与框坐标,纯函数。

两遍合成(参考图超过 3 张):第二遍拿第一遍的图当 Picture 1,把只写了名字的素材换成定妆图的样子。
局部替换:人在图上框一块,裁出来按素材参考图改,再贴回原图(框外像素不动)。
依据:`spike-results/2026-10-19-首帧多参考图实测.md`、`2026-10-19-多角色同框常规做法调研.md`。
"""
from __future__ import annotations

import math

from tools.drama.refs import Ref

PAD = 32            # 框四周外扩:羽化带落在外扩的部分,人物本身完整在框里
ALIGN = 16          # Qwen latent 要求 16 的倍数
MIN_SIDE = 128      # 框再小,放大到 1 MP 再缩回会糊
WORK_PIXELS = 1024 * 1024


def _change(ref: Ref, picture: int, subject: str) -> str:
    if ref.kind == "character":
        return f"把{subject}换成 Picture {picture}({ref.name}) 的长相、发型发饰和服装"
    return f"把{subject}换成 Picture {picture}({ref.name}) 的样子"


def second_pass_prompt(refs: list[Ref]) -> str:
    changes = ";".join(_change(r, k, f"画面中的{r.name}") for k, r in enumerate(refs, 2))
    return f"保持 Picture 1 的画面、构图、光线、动作和其他人物完全不变,只做以下修改:{changes}。"


def region_prompt(ref: Ref, note: str) -> str:
    subject = "其中的人物" if ref.kind == "character" else f"其中的{ref.name}"
    return (f"保持 Picture 1 的构图、姿态、动作、光线和背景不变,{_change(ref, 2, subject)}。"
            + note.strip())


def region_box(box: list[int], size: tuple[int, int]) -> tuple[int, int, int, int]:
    """人画的框 [x, y, w, h](原图像素)→ 外扩 PAD、对齐 ALIGN、夹在图内的框。"""
    x, y, w, h = (int(v) for v in box)
    width, height = size
    if w < MIN_SIDE or h < MIN_SIDE:
        raise ValueError(f"框太小({w}×{h}),宽高都要至少 {MIN_SIDE} 像素 —— 框住整个人")
    if x < 0 or y < 0 or x + w > width or y + h > height:
        raise ValueError(f"框 {x},{y} {w}×{h} 超出图片 {width}×{height}")
    x0, y0 = max(0, x - PAD) // ALIGN * ALIGN, max(0, y - PAD) // ALIGN * ALIGN
    x1 = min(width, math.ceil((x + w + PAD) / ALIGN) * ALIGN)
    y1 = min(height, math.ceil((y + h + PAD) / ALIGN) * ALIGN)
    return x0, y0, x1 - x0, y1 - y0


def work_size(w: int, h: int) -> tuple[int, int]:
    """裁出的块放大 / 缩小到约 1 MP 再编辑(Qwen 的原生分辨率),宽高对齐 ALIGN、保持比例。"""
    k = math.sqrt(WORK_PIXELS / (w * h))
    return max(ALIGN, round(w * k / ALIGN) * ALIGN), max(ALIGN, round(h * k / ALIGN) * ALIGN)
