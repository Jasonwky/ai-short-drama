# -*- coding: utf-8 -*-
"""后端能力声明 —— 每个生成后端把自己的硬约束报上来。

**为什么这些不是业务规则**:「段落 8–15 秒」看着像产品决定,其实是
MiniMax H3 的训练区间(124–362 帧 @24fps)逼出来的 —— ComfyUI 节点
`length` 的 tooltip 原文写着「trained range is ~124-362, longer is untested」。
行业里 huobao 定 8–15 秒、Toonflow 定 ≤15 秒,不是商量好的,是被同一个区间逼到一起。

所以这些数值必须由**后端声明**,换模型时跟着变;
上层(分镜拆解、readiness 校验)只读 Capability,不硬编。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Capability:
    name: str
    # ── 时长 ─────────────────────────────
    fps: int
    frame_grid_step: int          # 帧数必须落在 base + k*step 的网格上
    frame_grid_base: int
    frame_min_trained: int        # 训练区间下界(低于它没测过)
    frame_max_trained: int        # 训练区间上界(高于它没测过)
    # ── 参考素材上限 ─────────────────────
    max_ref_images: int
    max_ref_videos: int = 0
    max_ref_audios: int = 0
    max_prompt_chars: int = 0     # 0 = 无已知上限
    # ── 其它 ────────────────────────────
    supports_audio: bool = False
    aspect_ratios: tuple[str, ...] = field(default_factory=tuple)
    # ── 实测耗时(用来给「重跑要多久」报估算,不是承诺)──
    timing_points: tuple[tuple[int, float], ...] = ()   # (帧数, 秒/步),视频后端
    default_steps: int = 0
    sec_per_item: float = 0.0                              # 单张耗时,图片后端

    # ---------- 时长 ↔ 帧数 ----------

    def frames_for(self, seconds: float) -> int:
        """把秒数吸附到模型的合法帧数网格上(向上取到最近的合法值)。

        H3 的网格是 17k+5:5, 22, 39, … 落不到网格上的帧数模型不接受。
        """
        raw = max(self.frame_grid_base, round(seconds * self.fps))
        step, base = self.frame_grid_step, self.frame_grid_base
        remainder = (raw - base) % step
        return raw if remainder == 0 else raw + (step - remainder)

    def est_seconds(self, frames: int, steps: int | None = None) -> float:
        """按实测点估算一条视频的生成秒数。

        每步耗时随帧数**超线性**增长(注意力随序列长度),所以两点之间按幂律
        s = s0 · (f/f0)^k 插值/外推,k 由两个实测点反解。只有一个点时按线性。
        这是估算 —— 界面上必须标「估算」。
        """
        if not self.timing_points:
            raise ValueError(f"{self.name} 没有实测耗时点,不能估算")
        pts = sorted(self.timing_points)
        steps = steps or self.default_steps
        f0, s0 = pts[0]
        if len(pts) == 1:
            k = 1.0
        else:
            f1, s1 = pts[-1]
            k = math.log(s1 / s0) / math.log(f1 / f0)
        return steps * s0 * (frames / f0) ** k

    def seconds_for(self, frames: int) -> float:
        return frames / self.fps

    def duration_range_sec(self) -> tuple[float, float]:
        """训练区间换算成秒 —— 这就是「段落时长」的真实来源。"""
        return (self.frame_min_trained / self.fps, self.frame_max_trained / self.fps)

    def check_duration(self, seconds: float) -> list[str]:
        """返回问题清单,空表示通过。**不静默截断**,超了就报出来让人决定。"""
        lo, hi = self.duration_range_sec()
        frames = self.frames_for(seconds)
        problems: list[str] = []
        if frames < self.frame_min_trained:
            problems.append(
                f"{seconds:.1f} 秒({frames} 帧)低于 {self.name} 训练区间下界 "
                f"{self.frame_min_trained} 帧({lo:.1f} 秒)")
        if frames > self.frame_max_trained:
            problems.append(
                f"{seconds:.1f} 秒({frames} 帧)超出 {self.name} 训练区间上界 "
                f"{self.frame_max_trained} 帧({hi:.1f} 秒),该区间外未经测试")
        return problems

    # ---------- 参考素材 ----------

    def check_refs(self, n_images: int, n_videos: int = 0, n_audios: int = 0) -> list[str]:
        problems: list[str] = []
        if n_images > self.max_ref_images:
            problems.append(f"{self.name} 最多 {self.max_ref_images} 张参考图,给了 {n_images} 张")
        if n_videos > self.max_ref_videos:
            problems.append(f"{self.name} 最多 {self.max_ref_videos} 段参考视频,给了 {n_videos} 段")
        if n_audios > self.max_ref_audios:
            problems.append(f"{self.name} 最多 {self.max_ref_audios} 段参考音频,给了 {n_audios} 段")
        return problems


# ──────────────────────────────────────────────────────────────
# 实测/源码核实过的后端声明
#   H3:   ComfyUI comfy_extras/nodes_minimax_h3.py(0.34.0)源码
#   Qwen: ComfyUI comfy_extras/nodes_qwen.py,image1/2/3 固定三槽
# ──────────────────────────────────────────────────────────────

MINIMAX_H3 = Capability(
    name="MiniMax H3 (ref2va)",
    fps=24,
    frame_grid_step=17,
    frame_grid_base=5,
    frame_min_trained=124,        # ≈5.2 秒
    frame_max_trained=362,        # ≈15.1 秒
    max_ref_images=9,
    max_ref_videos=3,
    max_ref_audios=3,
    supports_audio=True,
    aspect_ratios=("16:9", "9:16", "1:1", "4:3", "3:4", "21:9"),
    # 192 帧:闸门 0 实测 2026-09-23(4090,20 步 44.4 s/it)
    # 328 帧:ai-ecommerce 实测 2026-09-07(4090,20 步 109.3 s/it)
    timing_points=((192, 44.4), (328, 109.3)),
    default_steps=20,
)

QWEN_IMAGE_EDIT_2511 = Capability(
    name="Qwen-Image-Edit-2511",
    fps=0, frame_grid_step=1, frame_grid_base=0,
    frame_min_trained=0, frame_max_trained=0,
    max_ref_images=3,             # 🔴 image1/2/3,固定槽位不是 Autogrow
    supports_audio=False,
    sec_per_item=25.3,            # 闸门 0 实测:4 步档、3 张参考图、928x1664
)

BACKENDS = {"minimax_h3": MINIMAX_H3, "qwen_image_edit_2511": QWEN_IMAGE_EDIT_2511}
