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
    # 以下是 hk90 起才有的可选字段(spec 2026-10-31-港片画风-design.md);老预设不填 = 拼法逐字节不变
    asset_prefix: dict[str, str] | None = None   # 资产图按用途分:character / scene / prop
    frame_look: str = ""          # 首帧质感词(不含光线)
    light_day: str = ""           # 白天戏的光线
    light_night: str = ""         # 夜 / 黄昏 / 室内火光戏的光线
    video_en: dict[str, str] | None = None       # 视频英文开场句:{"day": …, "night": …}(H3 指南:风格句用英文放最前)
    negative: str = ""            # 负向提示词(只对 CFG > 1 的资产正面图生效)
    grade: str = ""               # 导出调色预设名(cut/grade.py)


_HK_FILM = "35mm film transferred to video, faded print, natural film grain, telephoto lens, shallow depth of field"
_HK_NEG = ("低分辨率,低画质,肢体畸形,手指畸形,画面过饱和,蜡像感,人脸无细节,过度光滑,画面具有AI感。构图混乱。文字模糊,扭曲。"
           "网红脸,磨皮,塑料皮肤,动画,卡通,3D渲染,插画。")

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
    # 实测依据:spike-results/2026-10-31-降低AI感调研.md §6、§7(港片定妆图 / 搭景场景图;逆光写重像头发着火)
    Style("hk90", "90 年代港产武侠片",
          "film still photograph of an actor in costume from a 1990s Hong Kong wuxia film",
          "anime, illustration, cartoon, 3D render, painting",
          "90 年代港产武侠片实拍质感",
          asset_prefix={
              "character": ("film still photograph of an actor in costume from a 1990s Hong Kong wuxia film, "
                            "an ordinary face with slightly uneven features, weathered skin with visible pores, real dirt and soot, "
                            "thick handmade worn and patched period costume, 1990s Hong Kong film makeup and styling"),
              "scene": ("film still from a 1990s Hong Kong wuxia film, a built studio set with a layered background "
                        "fading into haze, weathered real materials, " + _HK_FILM),
              "prop": "prop photographed for a 1990s Hong Kong wuxia film, handmade, worn, real materials",
          },
          frame_look=("still frame from a 1990s Hong Kong wuxia film, built studio set, " + _HK_FILM
                      + ", ordinary imperfect faces, real soot and sweat, worn costumes"),
          light_day="hard daylight through dust haze, warm faded colors, a subtle thin rim light",
          light_night=("dry-ice haze with cold blue-cyan backlight, warm orange firelight or lamplight on the faces, "
                       "a subtle thin rim light, soft diffusion bloom around the highlights"),
          video_en={
              "day": ("The target video is in realistic photographic style, like a still from a 1990s Hong Kong wuxia film: "
                      "built studio set, hard daylight through dust haze, warm faded colors, telephoto lens, 35mm film grain."),
              "night": ("The target video is in realistic photographic style, like a still from a 1990s Hong Kong wuxia film: "
                        "built studio set, dry-ice haze, cold blue-cyan backlight against warm firelight on the faces, "
                        "soft diffusion bloom, telephoto lens, 35mm film grain."),
          },
          negative=_HK_NEG,
          grade="hk90"),
)


def get_style(key: str) -> Style:
    for s in STYLES:
        if s.key == key:
            return s
    raise KeyError(f"未知风格预设 {key!r},可选:{[s.key for s in STYLES]}")
