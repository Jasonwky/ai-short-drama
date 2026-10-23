# -*- coding: utf-8 -*-
"""验收入口:一条命令跑完三层(设计 §十一、CLAUDE.md 控制台纪律)。

    1 单测                 python -m pytest -q
    2 真 LLM 契约           python -m pytest -m real_llm -q(黄金样本打真 LLM;没有 key 就 SKIP)
    3 Playwright 有头自测   遍历注册表每个面板 + 点按钮链路,逐个截图存档
    4 功能整链(真云机)     一个段落出首帧(非内置模板预设)+ 四视图 + 视频(首帧锚定)+ 字幕,溯源齐全(标识 / 拼接走本机 ffmpeg,由单测与自测覆盖)

判决四态 PASS / FAIL / PARTIAL / SKIP —— **SKIP 永不计为 PASS**(M-6)。任一层没跑,总判决就不是 PASS。
**入口拒绝无头模式**:Playwright 测试一律有头(CLAUDE.md 硬约束);无头的验收是「人看不见的验收」。

    python -X utf8 -m tools.serve.accept                                 # 第 4 层 SKIP → 总判决 PARTIAL
    python -X utf8 -m tools.serve.accept --real-ssh-host H --real-ssh-port P --key-ref K \\
        --models-dir /root/autodl-tmp/comfyui-models --character C --scene S --prop R
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from tools.llm.claude_cli import find_exe
from tools.llm.client import LLMUnavailable

REAL_ARGS = ("real_ssh_host", "real_ssh_port", "key_ref", "models_dir", "character", "scene", "prop")


def verdict(layers: list[dict]) -> str:
    states = [x["state"] for x in layers]
    if "FAIL" in states:
        return "FAIL"
    if all(s == "PASS" for s in states):
        return "PASS"
    return "PARTIAL"


def _run(cmd: list[str], log: Path, env: dict | None = None) -> int:
    with log.open("w", encoding="utf-8") as f:
        return subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, env=env).returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AI 短剧控制台验收入口(三层,有头)")
    ap.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--real-ssh-host")
    ap.add_argument("--real-ssh-port", type=int)
    ap.add_argument("--key-ref")
    ap.add_argument("--models-dir")
    ap.add_argument("--character")
    ap.add_argument("--scene")
    ap.add_argument("--prop")
    a = ap.parse_args(argv)
    if a.headless:
        print("验收入口拒绝无头模式:Playwright 测试一律有头(CLAUDE.md 硬约束),验收要让人看见每个面板被打开。",
              file=sys.stderr)
        return 2

    out = Path("outputs/accept") / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    py = [sys.executable, "-X", "utf8"]
    layers: list[dict] = []

    print("第 1 层 单测 …", flush=True)
    rc = _run([*py, "-m", "pytest", "-q"], out / "1_pytest.log")
    layers.append({"layer": "单测", "state": "PASS" if rc == 0 else "FAIL", "log": str(out / "1_pytest.log")})

    try:                                            # LLM 走本机 Claude Code 命令行(2026-10-23 起)
        find_exe()
        no_llm = ""
    except LLMUnavailable as e:
        no_llm = str(e)
    if not no_llm:
        print("第 2 层 真 LLM 契约(黄金样本,几分钟)…", flush=True)
        rc = _run([*py, "-m", "pytest", "-m", "real_llm", "-q"], out / "2_real_llm.log")
        layers.append({"layer": "真 LLM 契约", "state": "PASS" if rc == 0 else "FAIL", "log": str(out / "2_real_llm.log")})
    else:
        layers.append({"layer": "真 LLM 契约", "state": "SKIP", "reason": f"{no_llm} —— 这一层没跑,不算通过"})

    print("第 3 层 Playwright 有头自测(会弹出浏览器)…", flush=True)
    rc = _run([*py, "-m", "tools.serve.selftest", "--out", str(out / "selftest")], out / "3_selftest.log")
    layers.append({"layer": "Playwright 有头自测", "state": "PASS" if rc == 0 else "FAIL",
                   "log": str(out / "3_selftest.log"), "screenshots": str(out / "selftest")})

    given = [k for k in REAL_ARGS if getattr(a, k)]
    if not given:
        layers.append({"layer": "功能整链(真云机)", "state": "SKIP",
                       "reason": "没给云机参数 —— 这一层没跑,不算通过"})
    elif len(given) < len(REAL_ARGS):
        missing = sorted(set(REAL_ARGS) - set(given))
        layers.append({"layer": "功能整链(真云机)", "state": "FAIL", "reason": f"云机参数不全:缺 {missing}"})
    else:
        print("第 4 层 功能整链(真云机,约 45 分钟:定稿一条 + 草稿一条)…", flush=True)
        env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
        rc = _run([*py, "-u", "-m", "tools.serve.e2e_first_segment", "--data-root", str(out / "e2e"),
                   "--ssh-host", a.real_ssh_host, "--ssh-port", str(a.real_ssh_port), "--key-ref", a.key_ref,
                   "--models-dir", a.models_dir, "--character", a.character, "--scene", a.scene, "--prop", a.prop],
                  out / "4_e2e.log", env=env)
        layers.append({"layer": "功能整链(真云机)", "state": "PASS" if rc == 0 else "FAIL",
                       "log": str(out / "4_e2e.log"), "note": "覆盖首帧(用非内置模板预设)、中间关键帧图、角色四视图、视频(首帧 + 第 3、6 秒两张关键帧锚定、四视图参考;每张关键帧对比差值 < 20)、同种子 10 步草稿片(实际交出 10 步、耗时 < 定稿 0.75 倍)、字幕对齐;标识 / 拼接走本机 ffmpeg,由单测与自测覆盖"})

    result = {"verdict": verdict(layers), "layers": layers}
    (out / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for x in layers:
        print(f"  [{x['state']}] {x['layer']}" + (f" —— {x['reason']}" if "reason" in x else ""))
    print(f"总判决:{result['verdict']}(报告 {out / 'report.json'})")
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
