# -*- coding: utf-8 -*-
"""LoRA 与底模的形状兼容性预检 —— 只读 safetensors 文件头,不加载权重,纯标准库。

**为什么需要**:LoRA 和底模对不上时,ComfyUI **只在日志里打 ERROR,不中断,照样出片**。
闸门 0-6 实测:PDD 8 步 LoRA 是给未剪枝 H3 训的(adaln_proj 输入维 2688),
云机上的底模是剪枝版(adaln_proj 输入维被压到 8),50 个块的 adaln 全部打不上,
而任务正常跑完、产物正常落盘 —— 「8 步加速档」其实是在蒸馏只打了一半的模型上跑的。

这是静默降级,必须在提交前确定性地拦住,不能靠人去翻日志。

safetensors 格式:前 8 字节是小端 uint64 的头长度,随后是 JSON 头,
每个张量给出 dtype / shape / data_offsets。读头即可拿到全部形状。

用法(在模型所在机器上跑):
    python -m tools.gen.lora_compat <底模.safetensors> <lora.safetensors>
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

# 不同导出工具给键加的前缀不一样,比较前统一去掉
_PREFIXES = ("model.diffusion_model.", "diffusion_model.", "transformer.", "model.")


def read_shapes(path: str | Path) -> dict[str, list[int]]:
    with open(path, "rb") as f:
        (n,) = struct.unpack("<Q", f.read(8))
        header = json.loads(f.read(n))
    return {k: v["shape"] for k, v in header.items() if k != "__metadata__"}


def _norm(key: str) -> str:
    for p in _PREFIXES:
        if key.startswith(p):
            return key[len(p):]
    return key


def check(base_path: str | Path, lora_path: str | Path) -> dict:
    """返回 {matched, mismatched: [(目标键, 底模形状, LoRA 增量形状)], missing: [目标键]}。

    只核对 `lora_A`/`lora_B`(peft 命名)与 `lora_down`/`lora_up`(kohya 命名)两种低秩对;
    增量形状 = (B 的输出维, A 的输入维),必须与底模对应 weight 的形状一致。
    """
    base = {_norm(k): s for k, s in read_shapes(base_path).items()}
    lora = read_shapes(lora_path)

    pairs: dict[str, dict[str, list[int]]] = {}
    for k, s in lora.items():
        nk = _norm(k)
        for a, b in ((".lora_A.weight", ".lora_B.weight"), (".lora_down.weight", ".lora_up.weight")):
            if nk.endswith(a):
                pairs.setdefault(nk[: -len(a)], {})["A"] = s
            elif nk.endswith(b):
                pairs.setdefault(nk[: -len(b)], {})["B"] = s

    matched, mismatched, missing = 0, [], []
    for target, ab in sorted(pairs.items()):
        if "A" not in ab or "B" not in ab:
            continue
        delta = [ab["B"][0], ab["A"][1]]
        want = base.get(target + ".weight")
        if want is None:
            missing.append(target)
        elif list(want[:2]) != delta:
            mismatched.append((target, want, delta))
        else:
            matched += 1
    return {"matched": matched, "mismatched": mismatched, "missing": missing}


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    r = check(sys.argv[1], sys.argv[2])
    print(f"匹配 {r['matched']} · 形状不符 {len(r['mismatched'])} · 底模无此层 {len(r['missing'])}")
    for t, want, got in r["mismatched"][:5]:
        print(f"   ✗ {t}: 底模 {want} vs LoRA 增量 {got}")
    if len(r["mismatched"]) > 5:
        print(f"   … 另有 {len(r['mismatched']) - 5} 处")
    for t in r["missing"][:3]:
        print(f"   ? 底模没有 {t}")
    # 任何形状不符都判不兼容 —— 部分打上的 LoRA 比不打更糟,因为它会被当成"已加速"
    return 1 if r["mismatched"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
