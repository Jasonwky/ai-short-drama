# -*- coding: utf-8 -*-
"""闸门 0 的图片侧执行器 —— 跑 Qwen-Image-Edit-2511(GGUF)。

覆盖闸门 0-1(装得下/多快)、0-2(四视图)、0-3(多图参考合成首帧)。

**设计原则(与正式实现一致,不是临时脚本)**:
- **种子必须显式**,不许随机 —— 不能复现的实验等于没做。
- **每次运行落一份溯源 JSON**:模型、量化档、步数、CFG、种子、完整提示词、
  参考图清单、耗时。这不是"以后再补",是实验结论能不能被复核的前提。
- **标签双向校验**:提示词引用的 `Picture N` 必须都有对应参考图,
  接进来的每张图也必须被引用过。云机上那个已出片的工作流就栽在这 ——
  提示词写了 `<Audio 1>` 却没接音频,ComfyUI 不报错。

用法:
    python -m tools.gate0.run_image --gate 0-2 \
        --prompt "..." --ref 角色正面.png --seed 12345 --preset baseline
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.gen.comfy_client import ComfyClient, ComfyError  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / "tools" / "gen" / "workflows" / "qwen_image_edit_2511_gguf_api.json"
OUT_ROOT = ROOT / "outputs" / "gate0"

# 节点 id 与工作流模板绑定,改模板要同步改这里
N_IMAGES = ("18", "19", "20")      # LoadImage ×3
N_ENCODE_POS = "21"                # TextEncodeQwenImageEditPlus(正)
N_LORA_ANGLES = "4"               # 四视图 LoRA 开关
N_FAST = "7"                       # 加速档开关
N_STEPS_BASE, N_STEPS_FAST = "10", "11"
N_CFG_BASE, N_CFG_FAST = "13", "14"
N_SAMPLER = "27"
N_SAVE = "29"

PRESETS = {
    # 名称        步数  CFG   说明
    "baseline":  (20, 4.0, "Comfy 建议档"),
    "quality":   (40, 4.0, "Qwen 官方档"),
    "fast":      (4,  1.0, "Lightning 4 步,CFG 必须为 1"),
}


def check_tags(prompt: str, n_refs: int) -> list[str]:
    """标签双向校验。返回问题清单,空表示通过。

    Qwen 节点会自动在提示词前拼 `Picture 1: <vision…>`,正文里用 "Picture N" 指代。

    **两条规则强度不同**:

    1. **引用不能越界** —— 任何情况下都成立。提示词写 `Picture 3` 却只接了 2 张图,
       模型收到一个不存在的引用,ComfyUI 不报错(云机上那个已出片的工作流就栽在这)。
    2. **每张图都要被引用** —— **只在 ≥2 张图时成立**。单图编辑没有歧义,
       图已由节点呈现给模型,不需要文字指代;
       多视角 LoRA 的提示词格式(`<sks> front view …`)就完全不提 Picture N。
    """
    problems: list[str] = []
    used = {int(m) for m in re.findall(r"[Pp]icture\s*(\d+)", prompt)}
    for i in sorted(used):
        if i < 1 or i > n_refs:
            problems.append(f"提示词引用了 Picture {i},但只接入了 {n_refs} 张参考图")
    if n_refs >= 2:
        for i in range(1, n_refs + 1):
            if i not in used:
                problems.append(
                    f"接了 {n_refs} 张参考图,但提示词从未引用 Picture {i} —— "
                    f"多图必须逐一指代,否则模型不知道哪张管什么")
    return problems


def build(prompt: str, ref_names: list[str], seed: int, preset: str,
          use_angles_lora: bool, prefix: str) -> dict:
    wf = copy.deepcopy(json.loads(WORKFLOW.read_text(encoding="utf-8")))
    steps, cfg, _ = PRESETS[preset]

    # 未用到的图槽必须整个删掉 —— image2/image3 是 optional,
    # 传 null 会被 ComfyUI 当成类型错误而不是"没接"。
    for idx, node_id in enumerate(N_IMAGES):
        key = f"image{idx + 1}"
        if idx < len(ref_names):
            wf[node_id]["inputs"]["image"] = ref_names[idx]
        else:
            wf.pop(node_id, None)
            wf[N_ENCODE_POS]["inputs"].pop(key, None)

    wf[N_LORA_ANGLES]["inputs"]["value"] = bool(use_angles_lora)
    wf[N_FAST]["inputs"]["value"] = (preset == "fast")
    wf[N_STEPS_BASE]["inputs"]["value"] = steps
    wf[N_STEPS_FAST]["inputs"]["value"] = steps
    wf[N_CFG_BASE]["inputs"]["value"] = cfg
    wf[N_CFG_FAST]["inputs"]["value"] = cfg
    wf[N_ENCODE_POS]["inputs"]["prompt"] = prompt
    wf[N_SAMPLER]["inputs"]["seed"] = seed
    wf[N_SAVE]["inputs"]["filename_prefix"] = prefix
    return wf


def main() -> int:
    ap = argparse.ArgumentParser(description="闸门 0 图片侧执行器")
    ap.add_argument("--gate", required=True, help="闸门编号,如 0-1 / 0-2 / 0-3")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--ref", action="append", default=[],
                    help="参考图本地路径,可重复,最多 3 张(模型槽位上限)")
    ap.add_argument("--seed", type=int, required=True, help="必须显式给,不许随机")
    ap.add_argument("--preset", choices=sorted(PRESETS), default="baseline")
    ap.add_argument("--angles-lora", action="store_true", help="启用四视图 LoRA")
    ap.add_argument("--server", default="127.0.0.1:8188")
    ap.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args()

    if len(args.ref) > 3:
        print(f"[拒绝] 给了 {len(args.ref)} 张参考图,"
              f"Qwen-Image-Edit 只有 image1/2/3 三个槽位。", file=sys.stderr)
        return 2

    problems = check_tags(args.prompt, len(args.ref))
    if problems:
        print("[拒绝] 标签双向校验未通过:", file=sys.stderr)
        for p in problems:
            print(f"   · {p}", file=sys.stderr)
        return 2

    client = ComfyClient(args.server, timeout=30)
    try:
        stats = client.system_stats()
    except ComfyError as e:
        print(f"[失败] {e}", file=sys.stderr)
        return 1
    vram = (stats.get("devices") or [{}])[0]
    print(f"[环境] ComfyUI {stats.get('system', {}).get('comfyui_version')} · "
          f"{vram.get('name', '?')} · VRAM total {vram.get('vram_total', 0) // 2**20} MiB")

    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
    run_dir = OUT_ROOT / f"{args.gate}_{args.preset}_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    uploaded = []
    for r in args.ref:
        name = client.upload_image(r)
        uploaded.append(name)
        print(f"[上传] {r} -> {name}")

    steps, cfg, note = PRESETS[args.preset]
    wf = build(args.prompt, uploaded, args.seed, args.preset,
               args.angles_lora, f"shortdrama/{args.gate}")

    t0 = time.time()
    pid = client.submit(wf)
    print(f"[提交] prompt_id={pid} · {args.preset}({steps} 步 CFG {cfg} —— {note}) "
          f"· seed={args.seed} · 参考图 {len(uploaded)} 张")

    try:
        rec = client.wait(pid, timeout_sec=args.timeout,
                          on_tick=lambda s, r, p: print(f"  … {s}s running={r} pending={p}")
                          if s % 30 == 0 else None)
    except ComfyError as e:
        print(f"[失败] {e}", file=sys.stderr)
        (run_dir / "error.txt").write_text(str(e), encoding="utf-8")
        return 1
    elapsed = time.time() - t0

    files = []
    for item in client.outputs(rec):
        p = client.fetch(item, run_dir)
        files.append(p.name)
        print(f"[产物] {p}")

    # 溯源:从第一次实验就存全量参数,不是以后再补
    (run_dir / "provenance.json").write_text(json.dumps({
        "gate": args.gate,
        "at": stamp,
        "model": "qwen-image-edit-2511-Q4_K_M.gguf",
        "text_encoder": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
        "vae": "qwen_image_vae.safetensors",
        "loras": ([ "qwen-image-edit-2511-multiple-angles-lora" ] if args.angles_lora else [])
                 + ([ "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16" ] if args.preset == "fast" else []),
        "preset": args.preset, "steps": steps, "cfg": cfg,
        "seed": args.seed,
        "prompt": args.prompt,
        "refs_local": args.ref, "refs_uploaded": uploaded,
        "prompt_id": pid,
        "elapsed_sec": round(elapsed, 1),
        "outputs": files,
        "device": vram.get("name"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[完成] 耗时 {elapsed:.1f}s · 产物 {len(files)} 个 · 溯源已写 {run_dir / 'provenance.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
