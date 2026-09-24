# -*- coding: utf-8 -*-
"""Playwright 全面自测(CLAUDE.md 控制台纪律,硬门)。

- **遍历注册表里每一个面板**:打开、断言无 JS 报错、无 5xx、关键元素真渲染出来,逐个截图存档;
- 再走一条**真点按钮**的链路:参考帧面板点「生成首帧」→ 等图出现;视频面板点「生成视频」→ 等播放器出现;
- 在**全新临时目录**里建演示工程、用**假后端**跑 —— 假产物碰不到真工程,页面顶部标着「假后端」;
- **一律有头**(CLAUDE.md 硬约束,用户 2026-09-24 明确要求):人要看得见它点开了每个面板。
  **不提供 `--headless` 开关** —— 有头跑抓到过无头发现不了的问题(浏览器请求 /favicon.ico 的 404)。

    python -X utf8 -m tools.serve.selftest [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import uvicorn

from tools.drama import repo
from tools.drama.db import Store
from tools.gen.assets import _png
from tools.serve.app import create_app
from tools.serve.fakes import fake_backend
from tools.serve.hosts import add_host
from tools.serve.panels import REGISTRY, url_for
from tools.serve.runtime import Runtime

# 每个面板「真渲染出来了」的判据:这个元素必须可见
KEY_SELECTOR = {
    "projects": "[data-drama-list]",
    "outline": "[data-outline]",
    "script": "[data-episode]",
    "assets": "[data-asset-col=character] [data-asset]",
    "storyboard": "[data-segment-list] [data-segment]",
    "frames": "[data-generate-frame]",
    "videos": "[data-readiness] .checklist",
    "hosts": "[data-host-status]",
}


def seed_demo(store: Store, data_root: Path) -> dict:
    """一部写实风格的演示剧:一集、一个素材齐全的段落、一台假云机。"""
    for rel in ("assets/lin.png", "assets/yard.png", "assets/jade.png"):
        (data_root / rel).parent.mkdir(parents=True, exist_ok=True)
        (data_root / rel).write_bytes(_png(96, 160, 150))
    d = repo.create_drama(store, "玉佩(自测)", "9:16", "realistic")
    ep = repo.add_episode(store, d, 1, "script", script="林小雨在老宅庭院里回忆母亲。" * 20)
    lin = repo.add_character(store, d, "林小雨", turnaround_path="assets/lin.png", voice_desc="年轻女声,清亮",
                             final_prompt="A young woman in a cream blouse.")
    yard = repo.add_scene(store, d, "老宅庭院", image_path="assets/yard.png", final_prompt="An empty courtyard at dusk.")
    jade = repo.add_prop(store, d, "玉佩", image_path="assets/jade.png", final_prompt="A green jade pendant.")
    seg = repo.add_segment(
        store, ep, 1, scene_id=yard, duration_sec=8.0, reference_mode="first",
        description="【镜头1】林小雨低头看着玉佩。【镜头2】林小雨说：「这块玉佩，是我母亲留给我的。」",
        image_prompt="@林小雨 站在 @老宅庭院 里,双手捧着 @玉佩",
        video_prompt="@林小雨 在 @老宅庭院 低头看 @玉佩,然后抬眼说话")
    repo.bind_characters(store, seg, [lin])
    repo.bind_props(store, seg, [jade])
    add_host(store, label="假云机", ssh_host="fake.local", ssh_port=1, ssh_user="root", key_ref="C:/fake/key",
             local_port=8188, vram_gb=24.0, models_dir="/fake/models")
    return {"drama": d, "episode": ep, "segment": seg}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(runtime: Runtime) -> tuple[uvicorn.Server, str]:
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(runtime), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 20
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("自测用的控制台 20 秒内没起来")
        time.sleep(0.1)
    return server, f"http://127.0.0.1:{port}"


def _wait_for(page, selector: str, timeout_s: float) -> bool:
    """等某个元素出现。页面有在途任务时自己会 10 秒刷一次;这里更勤快地主动刷新。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if page.locator(selector).count():
            return True
        time.sleep(1.5)
        page.reload()
    return False


def run(out_dir: Path) -> dict:
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="drama-selftest-"))
    store = Store(work / "drama.db")
    ids = seed_demo(store, work)
    backend, _ = fake_backend()
    runtime = Runtime(store, work, backend, poll_sec=1)
    runtime.start()
    server, base = _serve(runtime)
    report = {"base": base, "work_dir": str(work), "headed": True, "panels": [], "flows": []}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False, slow_mo=250)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(f"JS 异常:{e}"))
            page.on("console", lambda m: errors.append(f"console.{m.type}:{m.text}") if m.type == "error" else None)
            page.on("response", lambda r: errors.append(f"HTTP {r.status} {r.url}") if r.status >= 500 else None)

            for i, p in enumerate(REGISTRY, 1):
                errors.clear()
                url = base + url_for(p, ids["drama"], ids["episode"])
                page.goto(url)
                key = page.locator(KEY_SELECTOR[p.key])
                rendered = key.count() > 0 and key.first.is_visible()
                main_text = page.locator(f'main[data-panel="{p.key}"]').inner_text().strip()
                footer = page.locator("footer").inner_text().strip()
                shot = out_dir / f"{i:02d}_{p.key}.png"
                page.screenshot(path=str(shot), full_page=True)
                problems = list(errors)
                if not rendered:
                    problems.append(f"关键元素 {KEY_SELECTOR[p.key]} 没渲染出来")
                if len(main_text) < 10:
                    problems.append("面板内容几乎为空")
                if footer != "Powered by MiniMax H3":
                    problems.append(f"页脚署名不对:{footer!r}")
                report["panels"].append({"panel": p.key, "title": p.title, "url": url, "ok": not problems,
                                         "problems": problems, "screenshot": str(shot)})

            seg = ids["segment"]
            for key, button, wait_sel, timeout in (
                    ("frames", "[data-generate-frame]", f"#seg-{seg} .current-media img", 40),
                    ("videos", "[data-generate-video]", f"#seg-{seg} .current-media video", 60)):
                errors.clear()
                p = next(x for x in REGISTRY if x.key == key)
                page.goto(base + url_for(p, ids["drama"], ids["episode"]))
                page.locator(button).first.click()
                appeared = _wait_for(page, wait_sel, timeout)
                shot = out_dir / f"flow_{key}.png"
                page.screenshot(path=str(shot), full_page=True)
                problems = list(errors) + ([] if appeared else [f"{timeout}s 内没等到 {wait_sel}"])
                report["flows"].append({"flow": f"{key}: 点「生成」→ 产物出现在面板上", "ok": not problems,
                                        "problems": problems, "screenshot": str(shot)})
            browser.close()
    finally:
        server.should_exit = True
        runtime.stop()
        store.close()
    report["ok"] = all(x["ok"] for x in report["panels"] + report["flows"])
    (out_dir / "selftest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="控制台 Playwright 全面自测(一律有头,会弹出浏览器)")
    ap.add_argument("--out", type=Path, default=None, help="截图与报告目录,默认 outputs/selftest/<时间>")
    a = ap.parse_args(argv)
    out = a.out or Path("outputs/selftest") / datetime.now().strftime("%Y%m%d-%H%M%S")
    report = run(out)
    for x in report["panels"] + report["flows"]:
        name = x.get("panel") or x["flow"]
        print(f"{'✅' if x['ok'] else '❌'} {name}" + ("" if x["ok"] else " —— " + ";".join(x["problems"])))
    print(f"截图与报告:{out}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
