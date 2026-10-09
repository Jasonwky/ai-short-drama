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


# ──────────────────────────────────────────────────────────────
# 字幕对齐后端(云机 GPU;spike-results/2026-09-26-字幕对齐云机实测.md)
#   whisper-large-v3-turbo(MIT)的 token 级时间戳;借 ComfyUI 的 venv 跑,云机上不装任何东西。
#   路径是这台云机的布局 —— 换机器只改这里。
# ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AlignBackend:
    name: str
    python: str          # 云机上带 transformers / torch 的解释器
    model_dir: str       # whisper 权重目录
    input_dir: str       # ComfyUI 的 input 目录:视频经 ComfyUI 上传接口放到这里
    sec_per_item: float  # 实测:GPU 每条 8 秒片 5–6 s,加模型加载约 20 s
    vram_gb: float       # 跑一次要的显存:实测 whisper 进程占 3.81 GiB(2026-09-29),留余量


WHISPER_ALIGN = AlignBackend(
    name="whisper-large-v3-turbo",
    python="/root/autodl-tmp/ComfyUI/venv/bin/python",
    model_dir="/root/autodl-tmp/shortdrama-eval/whisper-large-v3-turbo",
    input_dir="/root/autodl-tmp/ComfyUI/input",
    sec_per_item=30.0,
    vram_gb=6.0,
)


# ──────────────────────────────────────────────────────────────
# 模型白名单(工作流编辑的提交把关 #1,设计 specs/2026-10-08-ComfyUI工作流编辑-design.md)
#   共用云机上装着别的项目的模型(例:flux1-dev,非商用)。画布里能选到它们,但出的片不能商用 ——
#   所以工作流里出现的每个模型文件都要在这张表里,并写明许可证结论与出处。要用新模型:加一行,许可证一栏必填。
# ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ModelDecl:
    name: str       # 云机上的文件名(ComfyUI 认的名字)
    use: str
    license: str    # 许可证结论 + 出处


_QWEN = "Apache-2.0(原文已核:huggingface.co/Qwen/Qwen-Image 与 github.com/QwenLM/Qwen-Image 的 LICENSE,2026-10-08)"
_H3 = ("MiniMax H3 Community License(原文已核:spike-results/2026-09-22-设计自查3-H3许可证地域限制与合规标识规格.md)"
       " —— 输出不得在欧盟、英国、韩国、美国使用")
_CARD_ONLY = "模型卡声明 apache-2.0,仓库无 LICENSE 文件(2026-10-08 核对;用户定:直接列入白名单)"

ALLOWED_MODELS: tuple[ModelDecl, ...] = (
    ModelDecl("qwen-image-edit-2511-Q4_K_M.gguf", "Qwen-Image-Edit-2511 主模型(GGUF 量化)", _QWEN),
    ModelDecl("qwen_2.5_vl_7b_fp8_scaled.safetensors", "Qwen 文本编码器",
              "Apache-2.0(原文已核:github.com/QwenLM/Qwen2.5-VL 的 LICENSE,2026-10-08)"),
    ModelDecl("qwen_image_vae.safetensors", "Qwen 图像 VAE", _QWEN),
    ModelDecl("qwen-image-edit-2511-multiple-angles-lora.safetensors", "多角度 LoRA(fal)", _CARD_ONLY),
    ModelDecl("Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors", "4 步加速 LoRA(lightx2v)", _CARD_ONLY),
    ModelDecl("minimax_h3_ref2va_pruned_fp8_scaled.safetensors", "H3 ref2va 主模型", _H3),
    ModelDecl("qwen3vl_32b_minimax_h3_int8_convrot.safetensors", "H3 文本编码器", _H3),
    ModelDecl("minimax_h3_video_vae_fp16.safetensors", "H3 视频 VAE", _H3),
    ModelDecl("minimax_h3_audio_vae_fp32.safetensors", "H3 音频 VAE", _H3),
)
