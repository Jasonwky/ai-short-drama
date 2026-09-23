# -*- coding: utf-8 -*-
"""提交前的 LoRA/底模形状预检 —— 在模型所在的云机上跑。

闸门 0-6:PDD LoRA 与剪枝版底模不兼容,ComfyUI 只打日志、照样出片。
所以每条要提交的工作流,凡用到 LoRA,都先核对形状;不兼容就判任务失败,不交给 ComfyUI。

实现:把 `tools/gen/lora_compat.py`(纯标准库)的源码经 ssh 的 stdin 喂给云机上的 `python3 -`,
不往云机传文件。

两种「过不了」要分清:
- 形状不兼容 → 失败,并给出哪一层不符;
- **预检本身没跑成**(ssh 断了、云机没有 python3)→ 也按失败处理。**不能核验就不放行。**
GGUF 底模不是 safetensors,读不了文件头 —— 如实给出「无法核验」的告警,不假装核验通过。
"""
from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tools.gen.lora_compat import EXIT_INCOMPATIBLE

# 远端执行器:(参数列表, 喂给 stdin 的程序源码) -> (退出码, 输出)
Runner = Callable[[list[str], bytes], tuple[int, str]]

COMPAT_SOURCE = (Path(__file__).parent / "lora_compat.py").read_bytes()


@dataclass(frozen=True)
class Preflight:
    ok: bool
    problems: tuple[str, ...]
    warnings: tuple[str, ...]


def lora_pairs(workflow: dict) -> tuple[str | None, list[str]]:
    """从工作流里找出底模文件名和用到的全部 LoRA 文件名。"""
    base, loras = None, []
    for node in workflow.values():
        kind, ins = node["class_type"], node["inputs"]
        if kind in ("UNETLoader", "UnetLoaderGGUF"):
            base = ins["unet_name"]
        elif kind == "LoraLoaderModelOnly":
            loras.append(ins["lora_name"])
    return base, loras


def check(workflow: dict, models_dir: str, run: Runner) -> Preflight:
    base, loras = lora_pairs(workflow)
    if not loras:
        return Preflight(True, (), ())
    if base is None:
        return Preflight(False, ("工作流用了 LoRA,却找不到底模节点",), ())
    if base.endswith(".gguf"):
        return Preflight(True, (), tuple(
            f"LoRA {name} 叠在 GGUF 底模 {base} 上,GGUF 读不了 safetensors 文件头,形状无法核验"
            for name in loras))
    problems = []
    for name in loras:
        rc, out = run([f"{models_dir}/diffusion_models/{base}", f"{models_dir}/loras/{name}"], COMPAT_SOURCE)
        if rc == EXIT_INCOMPATIBLE:
            problems.append(f"LoRA {name} 与底模 {base} 形状不兼容:{out.strip()}")
        elif rc != 0:
            problems.append(f"LoRA 预检没跑成(退出码 {rc}),不能核验就不放行:{out.strip()[:300]}")
    return Preflight(not problems, tuple(problems), ())


def ssh_runner(ssh_host: str, ssh_port: int, ssh_user: str, key_ref: str, timeout: int = 120) -> Runner:
    """在云机上执行 `python3 - <args>`,程序源码从 stdin 送过去。"""
    def run(args: list[str], stdin: bytes) -> tuple[int, str]:
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "-i", key_ref, "-p", str(ssh_port),
               f"{ssh_user}@{ssh_host}", "PYTHONIOENCODING=utf-8", "python3", "-",
               *(shlex.quote(a) for a in args)]
        try:
            p = subprocess.run(cmd, input=stdin, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, f"ssh 执行超时 {timeout}s"
        return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")
    return run
