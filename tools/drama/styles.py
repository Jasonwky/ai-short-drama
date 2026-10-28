# -*- coding: utf-8 -*-
"""风格预设 —— 设计 §七:「是数据不是代码」。

`drama.style` 存预设的 key;生成资产参考图时,预设给出提示词前缀与 avoid 项。
写实真人排第一:用户 2026-09-23 定「以后用生成的写实真人出片」(角色一律是虚构人物)。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Style:
    key: str
    label: str
    prefix: str   # 拼在资产提示词最前面
    avoid: str    # 拼在最后:不要什么
    zh: str       # 视频提示词里的画风(H3 吃中文;spec 2026-10-28-成片质量闸门-第一期-design.md 第 2 节)


STYLES: tuple[Style, ...] = (
    Style("realistic", "写实真人",
          "photorealistic photograph, real human, realistic skin texture, natural lighting, 50mm lens",
          "anime, illustration, cartoon, 3D render, painting",
          "写实真人实拍,电影质感,不要动画、卡通、3D 渲染"),
    Style("anime", "二次元",
          "high quality anime illustration, clean line art, cel shading",
          "photograph, photorealistic, 3D render",
          "二次元动画,干净线稿,赛璐璐上色,不要真人实拍、3D 渲染"),
    Style("semi3d", "半写实 3D",
          "semi-realistic 3D character render, soft global illumination",
          "flat illustration, photograph",
          "半写实 3D 角色渲染,柔和全局光照,不要平面插画、真人实拍"),
)


def get_style(key: str) -> Style:
    for s in STYLES:
        if s.key == key:
            return s
    raise KeyError(f"未知风格预设 {key!r},可选:{[s.key for s in STYLES]}")
