# -*- coding: utf-8 -*-
"""Playwright 全面自测(CLAUDE.md 控制台纪律,硬门)。

- **遍历注册表里每一个面板**:打开、断言无 JS 报错、无 5xx、关键元素真渲染出来,逐个截图存档;
- 再走几条**真点按钮**的链路:参考帧面板点「生成首帧」→ 等图出现;视频面板点「生成视频」→ 等播放器出现;
  段落 2 点「用段落 1 视频的尾帧」→ 首帧出现 → 视频面板「补全本集缺的视频」→ 确认页 → 段落 2 视频出现;
  成片面板填服务提供者编码点「导出成片」→ 等自检通过、成片播放器出现(本机真 ffmpeg);
  立项面板「导出工程」→ 工程包链接出现 →「导入为新剧」→ 新剧的资产面板打开;
  大纲面板贴原文点「下一步:看分批」→ 分批页出现;资产面板改角色声音描述 → 先出代价确认页(已有视频会作废);
  资产面板给没图的道具「上传」参考图 → 图出现;对它点「从图反推描述」→ 假 LLM 看不见图,被校验数字拦下(失败带原因);
  资产面板点「LLM 定声音」→ 审阅页出现待审产物,且假 LLM 的「批准」按钮是灰的;
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
    "final": "[data-export-form]",
    "hosts": "[data-host-status]",
}


def seed_demo(store: Store, data_root: Path) -> dict:
    """一部写实风格的演示剧:一集、两个素材齐全的段落(第 2 段的首帧留给尾帧衔接)、一台假云机。"""
    for rel in ("assets/lin.png", "assets/yard.png", "assets/jade.png"):
        (data_root / rel).parent.mkdir(parents=True, exist_ok=True)
        (data_root / rel).write_bytes(_png(96, 160, 150))
    d = repo.create_drama(store, "玉佩(自测)", "9:16", "realistic")
    ep = repo.add_episode(store, d, 1, "script", script="林小雨在老宅庭院里回忆母亲。" * 20)
    lin = repo.add_character(store, d, "林小雨", turnaround_path="assets/lin.png", voice_desc="年轻女声,清亮",
                             appearance="二十出头,长发,素色上衣", final_prompt="A young woman in a cream blouse.")
    yard = repo.add_scene(store, d, "老宅庭院", image_path="assets/yard.png", final_prompt="An empty courtyard at dusk.")
    jade = repo.add_prop(store, d, "玉佩", image_path="assets/jade.png", final_prompt="A green jade pendant.")
    photo = repo.add_prop(store, d, "旧照片")                  # 没有参考图:留给「上传」链路
    (data_root / "upload_demo.png").write_bytes(_png(96, 96, 200))
    seg = repo.add_segment(
        store, ep, 1, scene_id=yard, duration_sec=8.0, reference_mode="first",
        description="【镜头1】林小雨低头看着玉佩。【镜头2】林小雨说：「这块玉佩，是我母亲留给我的。」",
        image_prompt="@林小雨 站在 @老宅庭院 里,双手捧着 @玉佩",
        video_prompt="@林小雨 在 @老宅庭院 低头看 @玉佩,然后抬眼说话")
    repo.bind_characters(store, seg, [lin])
    repo.bind_props(store, seg, [jade])
    seg2 = repo.add_segment(
        store, ep, 2, scene_id=yard, duration_sec=8.0, reference_mode="first",
        description="【镜头1】林小雨把玉佩收进怀里。【镜头2】林小雨说：「我会一直带着它。」",
        image_prompt="@林小雨 站在 @老宅庭院 里,把 @玉佩 收进怀里",
        video_prompt="@林小雨 在 @老宅庭院 把 @玉佩 收进怀里,抬头说话")
    repo.bind_characters(store, seg2, [lin])
    repo.bind_props(store, seg2, [jade])
    add_host(store, label="假云机", ssh_host="fake.local", ssh_port=1, ssh_user="root", key_ref="C:/fake/key",
             local_port=8188, vram_gb=24.0, models_dir="/fake/models")
    return {"drama": d, "episode": ep, "segment": seg, "segment2": seg2, "photo": photo, "lin": lin}


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

            seg, seg2, photo, lin = ids["segment"], ids["segment2"], ids["photo"], ids["lin"]
            bundle_zip = lambda: next((work / "outputs" / "bundles").glob("*.zip"))   # noqa: E731 —— 上一条链路才产生
            for n, (key, label, fill, buttons, wait_sel, timeout) in enumerate((
                    ("frames", "生成首帧", {}, [f"#seg-{seg} [data-generate-frame]"], f"#seg-{seg} .current-media img", 40),
                    ("videos", "生成视频", {}, [f"#seg-{seg} [data-generate-video]"], f"#seg-{seg} .current-media video", 60),
                    ("frames", "用段落 1 视频的尾帧", {}, [f"#seg-{seg2} [data-tail-frame]"],
                     f"#seg-{seg2} .current-media img", 20),
                    ("videos", "补全本集缺的视频 → 确认", {}, ["[data-fill-button]", "[data-confirm]"],
                     f"#seg-{seg2} .current-media video", 60),
                    ("final", "导出成片", {"[data-producer]": "SELFTEST-ONLY"}, ["[data-export]"],
                     '[data-export-state="done"] [data-export-video]', 90),
                    ("projects", "导出工程", {}, ["[data-export-bundle]"], "[data-bundle-link]", 20),
                    ("projects", "导入为新剧", {"[data-bundle-file]": bundle_zip}, ["[data-import-button]"],
                     "[data-asset-col=character] [data-asset]", 20),
                    ("assets", "LLM 定声音", {}, ["[data-llm-voice]"],
                     '[data-llm-state="proposed"] ~ section [data-approve][disabled]', 40),
                    ("outline", "下一步:看分批", {"[data-outline-form] textarea": "第一段原文。\n第二段原文。"},
                     ["[data-llm-outline]"], "[data-outline-batches] [data-batch-start]", 20),
                    ("assets", "改声音描述 → 先报代价",
                     {f'[data-asset="character-{lin}"] textarea[name=voice_desc]': "低沉女声,语速偏慢"},
                     [f'form[action="/asset/character/{lin}"] button'], "[data-cost]", 20),
                    ("assets", "上传参考图", {f'[data-asset="prop-{photo}"] [data-upload-file]': lambda: work / "upload_demo.png"},
                     [f'[data-asset="prop-{photo}"] [data-upload-button]'], f'[data-asset="prop-{photo}"] .current-media img', 20),
                    ("assets", "从图反推描述", {}, [f'[data-asset="prop-{photo}"] [data-llm-describe]'],
                     '[data-llm-state="failed"] [data-llm-error]', 40)), 1):
                errors.clear()
                p = next(x for x in REGISTRY if x.key == key)
                page.goto(base + url_for(p, ids["drama"], ids["episode"]))
                for sel, value in fill.items():
                    if callable(value):
                        page.locator(sel).set_input_files(str(value()))
                    else:
                        page.locator(sel).fill(value)
                for button in buttons:
                    page.locator(button).first.click()
                appeared = _wait_for(page, wait_sel, timeout)
                shot = out_dir / f"flow_{n}_{key}.png"
                page.screenshot(path=str(shot), full_page=True)
                problems = list(errors) + ([] if appeared else [f"{timeout}s 内没等到 {wait_sel}"])
                report["flows"].append({"flow": f"{key}: 点「{label}」→ 产物出现在面板上", "ok": not problems,
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
