# -*- coding: utf-8 -*-
"""闸门 0 的视频侧执行器 —— 跑 MiniMax H3 ref2va(视频+音频联合生成)。

覆盖闸门 0-4(参考音频锁音色)与 0-6(PDD 8 步加速档)。

与图片侧执行器同一套规矩:
- **种子必须显式**;
- **每次运行落溯源 JSON,参考素材复制进运行目录**;
- **标签双向校验**,H3 有三类标签 `<Picture i>` / `<Video k>` / `<Audio j>`,各自 1 起编号。
  云机上 ai-ecommerce 那个已出片的工作流就是提示词写了 `<Audio 1>` 却没接音频。

片长由 `tools.gen.capability.MINIMAX_H3` 换算并校验,超出训练区间直接拒绝,不静默截断。

用法:
    python -m tools.gate0.run_video --gate 0-6 --seconds 8 --seed 1 \
        --image char.png --prompt "<Picture 1> ..." [--audio voice.wav] [--preset fast]
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.gen.capability import MINIMAX_H3  # noqa: E402
from tools.gen.comfy_client import ComfyClient, ComfyError  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / "tools" / "gen" / "workflows" / "minimax_h3_ref2va_api.json"
OUT_ROOT = ROOT / "outputs" / "gate0"

# 节点 id 与工作流模板绑定
N_IMAGES = ("8", "9", "10")
N_AUDIO = "11"
N_R2V = "12"
N_FAST = "3"
N_NOISE = "19"
N_SAVE = "24"

PRESETS = {
    # 名称       步数  说明
    "baseline": (20, "20 步基线(ai-ecommerce 实测 36.4 分钟 / 13.7 秒片)"),
    "fast":     (8,  "PDD Ref2VA 8 步加速,CFG 已蒸馏进权重"),
}

# 经 tools/gen/lora_compat.py 在云机实测不兼容的档位 —— 拒绝提交,不让它静默跑出"假加速"。
# ComfyUI 遇到形状不符只打 ERROR 日志、照样出片,所以这层拦截必须在我们这边。
BLOCKED_PRESETS = {
    "fast": ("PDD 8 步 LoRA 是给未剪枝 H3 训的:云机底模 minimax_h3_ref2va_pruned_fp8_scaled 的 "
             "adaln_proj 输入维被剪到 8,LoRA 增量是 2688 —— 208 层匹配、50 层 adaln 全部打不上。"
             "见 docs/superpowers/spike-results/2026-09-23-闸门0-视频侧实测.md"),
}

TAG_RE = re.compile(r"<(Picture|Video|Audio)\s+(\d+)>")


def check_tags(prompt: str, n_images: int, n_audios: int) -> list[str]:
    """H3 标签双向校验。规则与图片侧一致:越界恒报;同类 ≥2 个时每个都要被引用。

    音频单独一条更严:**接了参考音频就必须在提示词里引用 `<Audio 1>`** ——
    H3 需要文字告诉它这段音频拿来做什么(音色参考),否则只是悬空的条件。
    """
    problems: list[str] = []
    used: dict[str, set[int]] = {"Picture": set(), "Video": set(), "Audio": set()}
    for kind, idx in TAG_RE.findall(prompt):
        used[kind].add(int(idx))
    limits = {"Picture": n_images, "Video": 0, "Audio": n_audios}
    for kind, idxs in used.items():
        for i in sorted(idxs):
            if i < 1 or i > limits[kind]:
                problems.append(f"提示词引用了 <{kind} {i}>,但只接入了 {limits[kind]} 个{kind}")
    if n_images >= 2:
        for i in range(1, n_images + 1):
            if i not in used["Picture"]:
                problems.append(f"接了 {n_images} 张参考图,但提示词从未引用 <Picture {i}>")
    for i in range(1, n_audios + 1):
        if i not in used["Audio"]:
            problems.append(f"接了参考音频,但提示词从未引用 <Audio {i}> —— 模型不知道这段音频拿来做什么")
    return problems


def build(prompt: str, image_names: list[str], audio_name: str | None,
          frames: int, seed: int, preset: str, prefix: str) -> dict:
    wf = copy.deepcopy(json.loads(WORKFLOW.read_text(encoding="utf-8")))
    r2v = wf[N_R2V]["inputs"]

    # 未用的 Autogrow 槽位必须从 ref2va 节点上删掉,对应的加载节点也删掉
    for idx, node_id in enumerate(N_IMAGES):
        key = f"ref_images.ref_image_{idx}"
        if idx < len(image_names):
            wf[node_id]["inputs"]["image"] = image_names[idx]
        else:
            wf.pop(node_id, None)
            r2v.pop(key, None)
    if audio_name:
        wf[N_AUDIO]["inputs"]["audio"] = audio_name
    else:
        wf.pop(N_AUDIO, None)
        r2v.pop("ref_audios.ref_audio_0", None)

    r2v["prompt"] = prompt
    r2v["length"] = frames
    wf[N_FAST]["inputs"]["value"] = (preset == "fast")
    wf[N_NOISE]["inputs"]["noise_seed"] = seed
    wf[N_SAVE]["inputs"]["filename_prefix"] = prefix
    return wf


def main() -> int:
    ap = argparse.ArgumentParser(description="闸门 0 视频侧执行器(MiniMax H3 ref2va)")
    ap.add_argument("--gate", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--image", action="append", default=[], help="参考图,可重复,最多 3(工作流槽位)")
    ap.add_argument("--audio", default=None, help="参考音频(wav/mp3/mp4 均可,LoadAudio 解码)")
    ap.add_argument("--seconds", type=float, required=True)
    ap.add_argument("--seed", type=int, required=True, help="必须显式给,不许随机")
    ap.add_argument("--preset", choices=sorted(PRESETS), default="baseline")
    ap.add_argument("--server", default="127.0.0.1:8188")
    ap.add_argument("--timeout", type=int, default=7200)
    args = ap.parse_args()

    if args.preset in BLOCKED_PRESETS:
        print(f"[拒绝] 档位 {args.preset} 已判不兼容:{BLOCKED_PRESETS[args.preset]}", file=sys.stderr)
        return 2

    problems = MINIMAX_H3.check_duration(args.seconds)
    problems += MINIMAX_H3.check_refs(len(args.image), 0, 1 if args.audio else 0)
    if len(args.image) > len(N_IMAGES):
        problems.append(f"工作流只预留了 {len(N_IMAGES)} 个图槽,给了 {len(args.image)} 张")
    problems += check_tags(args.prompt, len(args.image), 1 if args.audio else 0)
    if problems:
        print("[拒绝]", file=sys.stderr)
        for p in problems:
            print(f"   · {p}", file=sys.stderr)
        return 2

    frames = MINIMAX_H3.frames_for(args.seconds)
    steps, note = PRESETS[args.preset]

    client = ComfyClient(args.server, timeout=30)
    try:
        client.system_stats()
    except ComfyError as e:
        print(f"[失败] {e}", file=sys.stderr)
        return 1

    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
    run_dir = OUT_ROOT / f"{args.gate}_{args.preset}_{stamp}"
    refs_dir = run_dir / "refs"
    refs_dir.mkdir(parents=True, exist_ok=True)
    kept = []
    for r in args.image + ([args.audio] if args.audio else []):
        dst = refs_dir / Path(r).name
        shutil.copy2(r, dst)
        kept.append(str(dst.relative_to(ROOT)).replace("\\", "/"))

    # /upload/image 只是把文件落到 ComfyUI 的 input 目录,并不校验是图片 —— 音频同样走它
    images_up = [client.upload_image(p) for p in args.image]
    audio_up = client.upload_image(args.audio) if args.audio else None

    wf = build(args.prompt, images_up, audio_up, frames, args.seed, args.preset,
               f"shortdrama/{args.gate}")

    t0 = time.time()
    pid = client.submit(wf)
    print(f"[提交] prompt_id={pid} · {args.preset}({steps} 步 —— {note}) · "
          f"{args.seconds}s = {frames} 帧 · seed={args.seed} · 图 {len(images_up)} · 音 {1 if audio_up else 0}",
          flush=True)
    try:
        rec = client.wait(pid, timeout_sec=args.timeout, poll_sec=15,
                          on_tick=lambda s, r, p: print(f"  … {s // 60} 分钟 running={r} pending={p}", flush=True)
                          if s % 300 < 15 else None)
    except ComfyError as e:
        print(f"[失败] {e}", file=sys.stderr)
        (run_dir / "error.txt").write_text(str(e), encoding="utf-8")
        return 1
    elapsed = time.time() - t0

    files = [client.fetch(item, run_dir).name for item in client.outputs(rec)]
    (run_dir / "provenance.json").write_text(json.dumps({
        "gate": args.gate, "at": stamp,
        "model": "minimax_h3_ref2va_pruned_fp8_scaled.safetensors",
        "text_encoder": "qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
        "vae": ["minimax_h3_video_vae_fp16.safetensors", "minimax_h3_audio_vae_fp32.safetensors"],
        "loras": ["minimax_h3_ref2va_pdd_acc_8step_comfyui"] if args.preset == "fast" else [],
        "preset": args.preset, "steps": steps,
        "seconds": args.seconds, "frames": frames, "size": [768, 1344], "ref_image_size": "match",
        "seed": args.seed, "prompt": args.prompt,
        "refs_local": kept, "images_uploaded": images_up, "audio_uploaded": audio_up,
        "prompt_id": pid, "elapsed_sec": round(elapsed, 1), "outputs": files,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[完成] 耗时 {elapsed / 60:.1f} 分钟 · 产物 {files} · 溯源 {run_dir / 'provenance.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
