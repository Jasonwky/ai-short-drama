# -*- coding: utf-8 -*-
"""框选局部替换:人在参考帧上框一块,裁出来按素材参考图改,再贴回原图(框外像素不动)—— 提示词与框坐标,纯函数。

依据:`spike-results/2026-10-19-多角色同框常规做法调研.md`、`2026-10-20-P15两遍合成与局部替换云机实测.md`
(只说「换成 Picture 2 的长相、发型发饰和服装」0/3:发饰压不过裁块里原有的、神情被换掉;
保持项加「神情」+ 写出角色的造型文字后 3/3 认对人、发饰回来)。
"""
from __future__ import annotations

import math
import struct

from tools.drama.refs import Ref

PAD = 32            # 框四周外扩:羽化带落在外扩的部分,人物本身完整在框里
ALIGN = 16          # Qwen latent 要求 16 的倍数
MIN_SIDE = 128      # 框再小,放大到 1 MP 再缩回会糊
WORK_PIXELS = 1024 * 1024


def region_prompt(ref: Ref, note: str, styling: str = "") -> str:
    """styling:角色表的「造型」字段(只有角色有)。具体神情代码推不出,靠 note 由人写。"""
    if ref.kind == "character":
        change = f"把其中的人物换成 Picture 2({ref.name}) 的长相、发型发饰和服装" + (f"({styling})" if styling else "")
    else:
        change = f"把其中的{ref.name}换成 Picture 2({ref.name}) 的样子"
    return f"保持 Picture 1 的构图、姿态、动作、神情、光线和背景不变,{change}。" + note.strip()


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


def png_size(data: bytes) -> tuple[int, int]:
    """PNG 的宽高(读文件头,不解码)。参考帧都是 PNG;局部替换按原图像素换算框,要知道真实尺寸。"""
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("不是 PNG 图片")
    return struct.unpack(">II", data[16:24])
