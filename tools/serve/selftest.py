# -*- coding: utf-8 -*-
"""Playwright 全面自测(CLAUDE.md 控制台纪律,硬门)。

- **遍历注册表里每一个面板**:打开、断言无 JS 报错、无 5xx、关键元素真渲染出来,逐个截图存档;
- 再走几条**真点按钮**的链路:参考帧面板点「生成首帧」→ 等图出现;视频面板点「生成视频」→ 等播放器出现;
  段落 2 点「用段落 1 视频的尾帧」→ 首帧出现 → 视频面板「补全本集缺的视频」→ 确认页 → 段落 2 视频出现;
  视频面板「补全本集缺的字幕」→ 确认页 → 两段的字幕逐句出现(假对齐脚本「听见」的是两段台词);
  成片面板填服务提供者编码点「导出成片」→ 等自检通过、成片播放器出现(本机真 ffmpeg);
  成片面板上传配乐(合成的粉红噪声)→ 改起止 → 音量曲线与台词底色出现(截图)→ 试听出播放器 → 再导出 → 自检「配乐」PASS;
  立项面板「导出工程」→ 工程包链接出现 →「导入为新剧」→ 新剧的资产面板打开;
  大纲面板贴原文点「下一步:看分批」→ 分批页出现;资产面板改角色声音描述 → 先出代价确认页(已有视频会作废);
  资产面板给没图的道具「上传」参考图 → 图出现;对它点「从图反推描述」→ 假 LLM 看不见图,被校验数字拦下(失败带原因);
  资产面板点「LLM 定声音」→ 审阅页出现待审产物,且假 LLM 的「批准」按钮是灰的;
  资产面板给角色点「生成四视图」→ 四视图出现;第 2 集「首尾帧」模式的段落点「生成尾帧」→ 尾帧出现;
  主机面板「刷新节点清单」→ 缓存信息出现;参考帧历史「在 ComfyUI 中编辑」→ 嵌入的画布里改种子 →「提交为新版本」;
  工作流面板改首帧模板(调度器)→ 确认页列出改动 → 保存 →「设为当前」先出确认页 → 当前变成 v1;
  段落 1 加关键帧 → 生成关键帧图 → 重出视频看「关键帧钉住了吗」→ 选草稿档重出 → 卡片标「草稿 · 10 步」;
  段落 2 加到 3 张关键帧 → 出「未经真机验证」警告、「加一张」置灰;
  第 2 集三角色 + 场景同框的段落 → 参考帧面板出「两遍合成」提示;在它的首帧上拖框 → 选角色 →「生成替换版」→ 历史里多一版;
  第 2 集首尾帧段落换上一条现造的白屏片、跑导出前的白屏检查 → 视频卡片出白屏提示;
- iframe 里的 ComfyUI 前端**不许访问外网**:页面上出现任何非本机请求即判失败(实测前端会去 api.comfy.org,垫片已改向);
- 在**全新临时目录**里建演示工程、用**假后端**跑 —— 假产物碰不到真工程,页面顶部标着「假后端」;
- **一律有头**(CLAUDE.md 硬约束,用户 2026-09-24 明确要求):人要看得见它点开了每个面板。
  **不提供 `--headless` 开关** —— 有头跑抓到过无头发现不了的问题(浏览器请求 /favicon.ico 的 404)。

    python -X utf8 -m tools.serve.selftest [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
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

# 已知的上游报错:只放行这几条**原文**,别的 console 报错照样判失败。每条写明出处与核实方式。
KNOWN_UPSTREAM_ERRORS = {
    # ComfyUI 前端 1.51.9 启动时自己打的(rootGraph 的 getter 在初始化前被读)。2026-10-08 用有头浏览器打开云机上的
    # 真 ComfyUI 核实过,同样会打 —— 不是本机垫片造成的;前端是 GPL 第三方包,我们不改它
    "ComfyApp graph accessed before initialization",
}

# 每个面板「真渲染出来了」的判据:这个元素必须可见
KEY_SELECTOR = {
    "projects": "[data-drama-list]",
    "outline": "[data-outline]",
    "script": "[data-episode]",
    "episodes": "[data-episode-row]",
    "assets": "[data-asset-col=character] [data-asset]",
    "storyboard": "[data-segment-list] [data-segment]",
    "frames": "[data-generate-frame]",
    "videos": "[data-readiness] .checklist",
    "final": "[data-export-form]",
    "workflows": "[data-preset-category]",
    "hosts": "[data-host-status]",
    "trace": "[data-trace-filters]",
}


def seed_demo(store: Store, data_root: Path) -> dict:
    """一部写实风格的演示剧:一集、两个素材齐全的段落(第 2 段的首帧留给尾帧衔接)、一台假云机;
    第 2 集一个「首尾帧」模式的段落,和一个三角色 + 场景同框、首帧已有的段落(两遍合成提示 + 局部替换)
    —— 都放第 2 集,不影响第 1 集的导出链路。"""
    for rel in ("assets/lin.png", "assets/yard.png", "assets/jade.png", "assets/shen.png", "assets/zhou.png"):
        (data_root / rel).parent.mkdir(parents=True, exist_ok=True)
        (data_root / rel).write_bytes(_png(96, 160, 150))
    d = repo.create_drama(store, "玉佩(自测)", "9:16", "realistic")
    ep = repo.add_episode(store, d, 1, "script", script="林小雨在老宅庭院里回忆母亲。" * 20 + "\n林小雨说：「这块玉佩，是我母亲留给我的。」\n")
    lin = repo.add_character(store, d, "林小雨", turnaround_path="assets/lin.png", voice_desc="年轻女声,清亮",
                             appearance="二十出头,长发,素色上衣", final_prompt="A young woman in a cream blouse.")
    yard = repo.add_scene(store, d, "老宅庭院", image_path="assets/yard.png", final_prompt="An empty courtyard at dusk.")
    jade = repo.add_prop(store, d, "玉佩", image_path="assets/jade.png", final_prompt="A green jade pendant.")
    photo = repo.add_prop(store, d, "旧照片")                  # 没有参考图:留给「上传」链路
    (data_root / "upload_demo.png").write_bytes(_png(96, 96, 200))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.3", "-t", "3",
                    "-ac", "2", str(data_root / "demo_song.wav")], check=True)       # 配乐链路:合成的,无版权问题
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
    ep2 = repo.add_episode(store, d, 2, "script", script="林小雨离开老宅。" * 20)
    seg3 = repo.add_segment(
        store, ep2, 1, scene_id=yard, duration_sec=8.0, reference_mode="first_last",
        description="【镜头1】林小雨捧着玉佩。【镜头2】林小雨转身走向院子深处。",
        image_prompt="@林小雨 站在 @老宅庭院 里,双手捧着 @玉佩",
        last_image_prompt="@林小雨 的背影走向 @老宅庭院 深处,手里握着 @玉佩",
        video_prompt="@林小雨 在 @老宅庭院 捧着 @玉佩,然后转身走远")
    repo.bind_characters(store, seg3, [lin])
    repo.bind_props(store, seg3, [jade])
    shen = repo.add_character(store, d, "沈墨", turnaround_path="assets/shen.png", voice_desc="青年男声,沉稳",
                              appearance="二十七八,短发,深色长衫")
    zhou = repo.add_character(store, d, "周伯", turnaround_path="assets/zhou.png", voice_desc="老年男声,沙哑",
                              appearance="六十来岁,花白头发,粗布褂子")
    (data_root / "frames").mkdir(parents=True, exist_ok=True)
    (data_root / "frames/demo_s4.png").write_bytes(_png(576, 1024, 120))   # 局部替换的框要 ≥ 128 像素,图不能太小
    seg4 = repo.add_segment(
        store, ep2, 2, scene_id=yard, duration_sec=8.0, reference_mode="first", first_frame_path="frames/demo_s4.png",
        description="【镜头1】林小雨、沈墨、周伯站在庭院里。【镜头2】周伯说：「少爷回来了。」",
        image_prompt="@林小雨 和 @周伯 站在 @老宅庭院 里,@沈墨 站在一旁",      # 沈墨单独一句:第二遍加入
        video_prompt="@林小雨 、@沈墨 和 @周伯 在 @老宅庭院 相见,@周伯 开口说话")
    repo.bind_characters(store, seg4, [lin, shen, zhou])
    # 第 3 集:有大纲、没剧本(单集大纲变体只在没剧本的集上出),放最后,不影响前面各集的链路
    ep3 = repo.add_episode(store, d, 3, "novel", source_text="原文:林小雨回到老宅。",
                           outline="林小雨回到老宅,在庭院里发现母亲留下的玉佩,管家神色慌张。")
    add_host(store, label="假云机", ssh_host="fake.local", ssh_port=1, ssh_user="root", key_ref="C:/fake/key",
             local_port=8188, vram_gb=24.0, models_dir="/fake/models", gpu="RTX 4090")
    return {"drama": d, "episode": ep, "segment": seg, "segment2": seg2, "photo": photo, "lin": lin,
            "episode2": ep2, "episode3": ep3, "segment3": seg3, "segment4": seg4, "zhou": zhou, "yard": yard}


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


def _canvas_ready(page) -> None:
    """等编辑页把工作流载入嵌入的 ComfyUI 画布(comfy_edit.js 载入完会打上 data-loaded)。"""
    page.wait_for_selector("[data-comfy-edit][data-loaded]", timeout=60000)


def _set_widget(name: str, value):
    """模拟人在画布里改一个控件:找到第一个名叫 name 的控件,改成 value。"""
    def act(page) -> None:
        ok = page.evaluate("""([name, value]) => {
            const app = document.querySelector('[data-comfy-frame]').contentWindow.app;
            for (const n of app.graph._nodes) for (const w of (n.widgets || []))
                if (w.name === name) { w.value = value; return true; }
            return false; }""", [name, value])
        if not ok:
            raise RuntimeError(f"画布里找不到控件 {name}")
    return act


def _add_keyframe(page, time_sec: str) -> None:
    """在编辑段落页的「加一张」里填时刻与提示词并保存;段落已有视频时会先出代价确认页。"""
    page.fill("[data-key-new] [data-key-time]", time_sec)
    page.fill("[data-key-new] [data-key-prompt]", "@林小雨 在 @老宅庭院 转身,背对镜头,握着 @玉佩")
    page.locator("[data-save-segment]").click()
    page.wait_for_load_state()
    if page.locator("[data-confirm]").count():
        page.locator("[data-confirm]").click()
        page.wait_for_load_state()


def _set_keyframes(segment_id: int, times: list[str]):
    """从参考帧面板点进编辑段落页,逐张加关键帧(假片只有 24 帧:第 0.5 秒 = 第 12 帧才对比得出来)。
    加满 3 张后「加一张」必须置灰。"""
    def act(page) -> None:
        page.locator(f'a[href="/segment/{segment_id}/edit"]').first.click()
        for t in times:
            _add_keyframe(page, t)
        if len(times) == 3 and not page.locator("[data-key-new][data-full]").count():
            raise RuntimeError("加满 3 张关键帧后「加一张」没有置灰")
    return act


def _drag_region(segment_id: int, shot: Path):
    """在段落首帧的「局部替换」里真拖一个框(鼠标按下 → 拖 → 松开),截图留证;框换算后「生成替换版」必须可点。"""
    def act(page) -> None:
        bb = page.locator(f"#seg-{segment_id} [data-region-img]").bounding_box()
        page.mouse.move(bb["x"] + bb["width"] * 0.2, bb["y"] + bb["height"] * 0.15)
        page.mouse.down()
        page.mouse.move(bb["x"] + bb["width"] * 0.75, bb["y"] + bb["height"] * 0.85, steps=12)
        page.mouse.up()
        page.screenshot(path=str(shot), full_page=True)
        if page.locator(f"#seg-{segment_id} [data-region-submit]").is_disabled():
            raise RuntimeError("拖完框「生成替换版」还是灰的:" + page.locator(f"#seg-{segment_id} [data-region-info]").inner_text())
    return act


def _with_premise(store: Store, drama_id: int):
    """出骨架的前提是立项已批准;假 LLM 的产物批准不了,就直接写一份立项当前置数据(同 _blank_video 直接换视频)。"""
    def act(page) -> None:
        repo.update(store, "drama", drama_id, premise_json=json.dumps({
            "title": "玉佩之谜", "logline": "孤女回老宅,从母亲的玉佩里挖出身世", "genre": "悬疑", "payoff": "身份揭晓",
            "conflict": "身份错位", "hook_type": "强悬念", "protagonist": {"name": "林小雨", "tag": "孤女", "contrast": "柔弱外表下心思缜密"},
            "antagonist": "沈墨", "hook": "雨夜,玉佩在她掌心裂开一道缝"}, ensure_ascii=False))
        page.reload()
    return act


def _blank_video(store: Store, root: Path, segment_id: int):
    """假后端出不了白屏片:本机 ffmpeg 现造一条「1 秒正常 + 1 秒纯白」的片子当这段的当前视频,
    再跑导出开工前那一道白屏检查(写缓存)—— 视频卡片只读缓存,命中就该出提示。"""
    def act(page) -> None:
        import subprocess
        from tools.cut import blankscan
        rel = f"outputs/selftest_blank/{segment_id}.mp4"
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=384x672:rate=24:d=1",
                        "-f", "lavfi", "-i", "color=white:size=384x672:rate=24:d=1", "-filter_complex",
                        "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[v]", "-map", "[v]", str(root / rel)], check=True)
        repo.update(store, "segment", segment_id, video_path=rel)
        if not blankscan.check(root, rel):
            raise RuntimeError("现造的白屏片没被白屏检测查出来")
        page.reload()
    return act


def _review(seg_id: int):
    """逐项勾选这段的验收清单,再点确认,等页面刷新出已确认(两段连着点,第二下要在新页面上点)。"""
    def act(page) -> None:
        for box in page.locator(f"#seg-{seg_id} [data-check-box]").all():
            box.check()
        page.locator(f"#seg-{seg_id} [data-review-confirm]").click()
        page.wait_for_selector(f"#seg-{seg_id} [data-reviewed]", timeout=20000)
    return act


def _partial_ticks(seg_id: int):
    """验收清单少勾一项,确认按钮不可点;勾上最后一项才可点(spec 2026-10-23-不穿帮验收 第 1 节)。不提交。"""
    def act(page) -> None:
        boxes = page.locator(f"#seg-{seg_id} [data-check-box]").all()
        btn = page.locator(f"#seg-{seg_id} [data-review-confirm]")
        for box in boxes[:-1]:
            box.check()
        if not btn.is_disabled():
            raise RuntimeError("验收清单少勾一项,确认按钮却可以点")
        boxes[-1].check()
        if btn.is_disabled():
            raise RuntimeError("验收清单勾全了,确认按钮还是不能点")
        for box in boxes:
            box.uncheck()
    return act


def _no_blank_problem(page) -> None:
    """成片面板的导出检查:人工确认放行过的白屏段落不该再列为问题。"""
    left = [t for t in page.locator("li.bad").all_inner_texts() if "白 / 灰的平底" in t]
    if left:
        raise AssertionError(f"放行后导出检查仍列着白屏:{left}")


def _music_cue(shot: Path):
    """配乐卡片:把起止改成成片第 1–2 秒(段落 2 的位置,淡入淡出各 0.2 秒)并保存;曲线与台词底色必须画出来,截图留证。"""
    def act(page) -> None:
        card = page.locator("[data-music-cue]").first
        for name, value in (("start_sec", "1"), ("end_sec", "2"), ("fade_in", "0.2"), ("fade_out", "0.2")):
            card.locator(f'[data-music-field="{name}"]').fill(value)
        card.locator("[data-music-save]").click()
        page.wait_for_load_state()
        curve = page.locator("[data-music-cue] [data-music-curve]").first
        curve.scroll_into_view_if_needed()
        page.screenshot(path=str(shot), full_page=True)
        points = curve.locator("polyline").get_attribute("points") or ""
        if len(points.split()) < 10 or not curve.locator("[data-music-speech]").count():
            raise RuntimeError(f"音量曲线没画出来:{len(points.split())} 个点,台词底色 {curve.locator('[data-music-speech]').count()} 块")
    return act


def _expect(selector: str, why: str):
    def act(page) -> None:
        if not page.locator(selector).count():
            raise RuntimeError(why)
    return act


def run(out_dir: Path) -> dict:
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="drama-selftest-"))
    store = Store(work / "drama.db")
    ids = seed_demo(store, work)
    backend, _ = fake_backend()
    backend.align_for(None).heard = "这块玉佩是我母亲留给我的我会一直带着它"     # 两段的台词
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
            page.on("console", lambda m: errors.append(f"console.{m.type}:{m.text}")
                    if m.type == "error" and m.text not in KNOWN_UPSTREAM_ERRORS else None)
            page.on("response", lambda r: errors.append(f"HTTP {r.status} {r.url}") if r.status >= 500 else None)
            page.on("request", lambda r: errors.append(f"外网请求:{r.url}")
                    if not r.url.startswith((base, "data:", "blob:", base.replace("http", "ws"))) else None)

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

            seg, seg2, seg3, photo, lin = ids["segment"], ids["segment2"], ids["segment3"], ids["photo"], ids["lin"]
            seg4, zhou = ids["segment4"], ids["zhou"]
            bundle_zip = lambda: next((work / "outputs" / "bundles").glob("*.zip"))   # noqa: E731 —— 上一条链路才产生
            for n, (key, label, fill, buttons, wait_sel, timeout) in enumerate((
                    ("frames", "生成首帧", {}, [f"#seg-{seg} [data-generate-frame]"], f"#seg-{seg} .current-media img", 40),
                    ("videos", "生成视频", {}, [f"#seg-{seg} [data-generate-video]"], f"#seg-{seg} .current-media video", 60),
                    ("videos", "生成记录里点「溯源」→ 倒查到首帧", {}, [f"#seg-{seg} [data-trace-link]"],
                     "[data-trace-tree] [data-node-inputs] img", 20),
                    ("frames", "生成记录里点任务号 → 任务详情", {}, [f"#seg-{seg} [data-task-link]"], "[data-task-params]", 20),
                    ("frames", "用段落 1 视频的尾帧", {}, [f"#seg-{seg2} [data-tail-frame]"],
                     f"#seg-{seg2} .current-media img", 20),
                    ("videos", "补全本集缺的视频 → 确认", {}, ["[data-fill-button]", "[data-confirm]"],
                     f"#seg-{seg2} .current-media video", 60),
                    ("videos", "补全本集缺的字幕 → 确认", {}, ['[data-fill="subtitles"] [data-fill-button]', "[data-confirm]"],
                     f"#seg-{seg2} [data-cue]", 30),
                    # 两段共用一段假的「听到的话」(见上面 heard):段落 1 天然多出「我会一直带着它」—— 台词外多说提醒的正例
                    ("videos", "字幕区出现台词外多说提醒", {}, [], f"#seg-{seg} [data-extra-speech]", 20),
                    ("videos", "看片区:切镜计数 + 上一段末帧 / 本段首帧并排", {}, [], f"#seg-{seg2} [data-seam] img", 30),
                    ("final", "没看片确认的段落列在导出检查的问题里", {}, [], 'li.bad:has-text("还没看片确认")', 20),
                    ("videos", "验收清单少勾一项 → 确认按钮不可点;勾全 → 可点", {},
                     [_partial_ticks(seg2)], f"#seg-{seg2} [data-check-item]", 20),
                    ("videos", "段落 1、2 逐项勾选验收清单 → 确认 → 卡片显示已确认", {},
                     [_review(seg), _review(seg2)], f"#seg-{seg2} [data-reviewed]", 20),
                    ("final", "导出成片", {"[data-producer]": "SELFTEST-ONLY"}, ["[data-export]"],
                     '[data-export-state="done"] [data-export-video]', 90),
                    # 配乐:第二次导出(上一条是 #1),等的是 #2 的自检里「配乐」PASS
                    ("final", "配乐:上传 → 设起止 → 曲线 → 试听 → 再导出 → 自检「配乐」PASS",
                     {"[data-music-file]": lambda: work / "demo_song.wav",
                      "[data-music-new-license]": "自测用合成音乐(粉红噪声,ffmpeg 现造),无版权"},
                     ["[data-music-upload-button]", _music_cue(out_dir / "flow_music_curve.png"), "[data-music-preview]",
                      _expect("[data-music-audio]", "点了试听,页面上没有出现播放器"), "[data-export]"],
                     '[data-export-row="2"][data-export-state="done"] [data-check-name="配乐"][data-check-ok="1"]', 120),
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
                     '[data-llm-state="failed"] [data-llm-error]', 40),
                    ("trace", "只看失败 → 失败归类里有刚才那次反推", {}, ["[data-failed-only]", "[data-trace-apply]"],
                     '[data-trace-timeline] [data-event="llm"][data-action="失败"]', 20),
                    ("assets", "生成四视图", {}, [f'[data-asset="character-{lin}"] [data-generate-fourview]'],
                     f'[data-asset="character-{lin}"] [data-fourview] img', 40),
                    ("frames:episode2", "生成尾帧(首尾帧模式)", {}, [f"#seg-{seg3} [data-generate-last-frame]"],
                     f"#seg-{seg3} [data-last-frame] img", 40),
                    ("hosts", "刷新节点清单", {}, ["[data-refresh-nodes]"], "[data-node-cache] code", 20),
                    ("hosts", "改 GPU 型号(估时按它)", {},
                     [lambda pg: pg.locator("[data-host-gpu] select").select_option("RTX 4080 SUPER"), "[data-host-gpu] button"],
                     '[data-host-gpu] option[value="RTX 4080 SUPER"][selected]', 20),
                    ("frames", "在 ComfyUI 中编辑 → 画布里改种子 → 提交为新版本", {},
                     [f"#seg-{seg} [data-comfy-edit-link]", _canvas_ready, _set_widget("seed", 4242), "[data-submit]"],
                     "@[data-edit-ok]", 60),
                    ("workflows", "改首帧模板 → 确认改动 → 保存", {},
                     ['[data-preset-category="frame"] [data-edit-preset]', _canvas_ready, _set_widget("scheduler", "normal"),
                      "[data-submit]", "[data-confirm-save]"], "@[data-edit-ok]", 60),
                    ("workflows", "设为当前(先出确认页)", {}, ['[data-preset-category="frame"] [data-activate]', "[data-confirm]"],
                     '[data-preset-category="frame"] [data-preset-version="1"] .tag', 20),
                    # 中间关键帧:排在所有现有链路之后(段落 1 设了关键帧,视频就要等关键帧图,不能挡前面的链路)
                    ("frames", "编辑段落设关键帧(第 0.5 秒)", {}, [_set_keyframes(seg, ["0.5"])], "[data-key-timeline]", 20),
                    ("frames", "生成关键帧", {}, [f"#seg-{seg} [data-generate-key-frame]"], f"#seg-{seg} [data-key-frame] img", 40),
                    ("videos", "重新生成视频 → 关键帧 vs 实际画面", {}, [f"#seg-{seg} [data-generate-video]"],
                     f"#seg-{seg} [data-keycheck] [data-key-diff]", 60),
                    # 草稿档:新版本留在历史里(当前版不被覆盖),卡片上标「草稿 · 10 步」
                    ("videos", "选草稿档重出 → 卡片标「草稿 · 10 步」", {},
                     [lambda pg: pg.locator(f"#seg-{seg} [data-steps]").select_option("10"), f"#seg-{seg} [data-generate-video]"],
                     f"#seg-{seg} [data-few-steps]", 60),
                    ("frames", "段落 2 加到 3 张关键帧 → 未经真机验证的警告、「加一张」置灰", {},
                     [_set_keyframes(seg2, ["2", "4", "6"])], '[data-readiness] [data-check="keyframe_count"]', 20),
                    ("frames:episode2", "三角色 + 场景同框 → 「两遍合成」提示(第二遍加入沈墨)", {}, [],
                     f'#seg-{seg4} [data-frame-notes] [data-check="two_pass"]', 20),
                    ("frames:episode2", "局部替换:拖框 → 选角色 → 生成替换版 → 历史多一版", {},
                     [f"#seg-{seg4} [data-region] summary", _drag_region(seg4, out_dir / "flow_region_box.png"),
                      lambda pg: pg.locator(f"#seg-{seg4} [data-region-asset]").select_option(f"character:{zhou}"),
                      f"#seg-{seg4} [data-region-submit]"],
                     f"#seg-{seg4} .history .ver.state-done img", 40),
                    # 白屏检测:排最后(换掉了第 2 集首尾帧段落的视频)
                    ("videos:episode2", "白屏片过导出前检查 → 视频卡片出白屏提示", {}, [_blank_video(store, work, seg3)],
                     f"#seg-{seg3} [data-blank-screen]", 20),
                    ("final:episode2", "白屏段落列在导出检查的问题里", {}, [], 'li.bad:has-text("白 / 灰的平底")', 20),
                    # 人工确认放行(用户 2026-10-20 定):填理由 → 放行 → 卡片标「已人工确认放行」→ 导出检查不再列它
                    ("videos:episode2", "填理由 → 确认不是白屏(放行)→ 卡片显示「已人工确认放行」",
                     {f"#seg-{seg3} [data-blank-reason]": "雪景,本来就白"}, [f"#seg-{seg3} [data-blank-confirm]"],
                     f"#seg-{seg3} [data-blank-override]", 20),
                    ("final:episode2", "放行后导出检查不再列这段白屏", {}, [_no_blank_problem], "[data-final-segment]", 20),
                    ("storyboard", "段落编辑页场记表:改姿态为跪 → 保存 → 预览句出现「跪着」", {},
                     [lambda pg: pg.goto(pg.url.split("/episode/")[0] + f"/segment/{seg}/edit"),
                      lambda pg: pg.locator('[data-blocking-row] [data-blocking-pose]').first.select_option("跪"),
                      "[data-save-blocking]"],
                     '@[data-blocking-preview]:has-text("跪着")', 20),
                    # 假 LLM 没有这个工具的样例 → 闸门判失败;演示的是「按钮 → 审阅页 → 失败原因写明」,真模型的行为由契约测试管
                    ("videos", "有场记表 → 视觉抽检按钮 → 审阅页落成失败并写明原因(假 LLM)", {},
                     [f"#seg-{seg} [data-vision-start]"], '[data-llm-state="failed"] [data-llm-error]', 30),
                    ("script", "评审团:选视角 → 请评审 → 审阅页逐条采纳 / 驳回(假 LLM 不能批准)", {},
                     [f'[data-episode="{ids["episode"]}"] [data-llm-critique] button'],
                     "[data-llm-preview] [data-issue]", 40),
                    ("script", "局部改写:粘贴一段 → 出几个写法 → 审阅页候选与原文并排(假 LLM 不能批准)",
                     {f'[data-episode="{ids["episode"]}"] [data-rewrite-span]': "林小雨说：「这块玉佩，是我母亲留给我的。」"},
                     [f'[data-episode="{ids["episode"]}"] [data-llm-rewrite] button'],
                     "[data-llm-preview] [data-candidate]", 40),
                    ("projects", "脑爆立项:一句话想法 → 三个方案、套路款标签、逐字段单选(假 LLM 不能批准)", {},
                     [f'a[href="/drama/{ids["drama"]}/premise"]',
                      lambda pg: pg.locator("[data-llm-premise] textarea[name=idea]").fill("替嫁冲喜的庶女其实是神医"),
                      "[data-llm-premise] button[type=submit]"],
                     "[data-llm-preview] [data-premise-candidate]", 40),
                    # 假 LLM 读不到大纲,节拍的逐字依据对不上 → 判卷失败页(演示「编造的依据被拦住」)
                    ("outline", "标注节拍 → 编造的依据被拦住(假 LLM)", {}, ["[data-llm-beats]"],
                     '[data-llm-state="failed"] [data-llm-error]', 40),
                    ("outline", "第 3 集(有大纲没剧本)出大纲变体 → 审阅页三个版本并排(假 LLM 不能批准)", {},
                     [f'[data-episode="{ids["episode3"]}"] [data-llm-variants] button'],
                     "[data-llm-preview] [data-variant]", 40),
                    # 第 2 集第 1 段:单角色、同场景下一段没有场记表,假方案的姿态不会撞相邻段对表
                    ("storyboard", "段落编辑页出镜头方案 → 审阅页两个方案并排(假 LLM 不能批准)", {},
                     [lambda pg: pg.goto(base + f"/segment/{seg3}/edit"), "[data-llm-shot-options] button"],
                     "[data-llm-preview] [data-shot-option]", 40),
                    ("script", "修改回路:写两条意见 → 让 AI 改 → 审阅页按意见分组接受 / 驳回(假 LLM 不能批准)",
                     {f'[data-episode="{ids["episode"]}"] [data-llm-script-revise] input[name="note-0"]': "这句台词改得更动情",
                      f'[data-episode="{ids["episode"]}"] [data-llm-script-revise] input[name="note-1"]': "开场太慢"},
                     [f'[data-episode="{ids["episode"]}"] [data-llm-script-revise] button[type=submit]'],
                     "[data-llm-preview] [data-revise-group]", 40),
                    ("storyboard", "分镜修改:针对第 1 段写意见 → 让 AI 改 → 审阅页显示补丁与代价(假 LLM 不能批准)",
                     {'[data-llm-storyboard-revise] input[name="note-0"]': "第 1 段运镜加一个推镜",
                      '[data-llm-storyboard-revise] input[name="target-0"]': "1"},
                     ["[data-llm-storyboard-revise] button[type=submit]"],
                     "[data-llm-preview] [data-revise-group]", 40),
                    ("episodes", "逐集:给缺剧本的集出剧本 → 确认页选 1 分钟 → 确认 → 第 3 集待你审(假 LLM)", {},
                     ["[data-batch-scripts]",
                      lambda pg: pg.locator("[data-batch-minutes]").select_option("1"),
                      "[data-batch-confirm]"],
                     f'[data-episode-row="{ids["episode3"]}"] [data-script-state="proposed"]', 60),
                    ("outline", "原创:有立项 → 出骨架方案 → 审阅页三个方案、各幕集号、付费卡点提示(假 LLM 不能批准)",
                     {}, [_with_premise(store, ids["drama"]),
                          lambda pg: pg.locator("[data-llm-skeleton] input[name=total]").fill("12"),
                          "[data-llm-skeleton] button[type=submit]"],
                     "[data-llm-preview] [data-skeleton-candidate]", 40),
                    # 放在最后:填了陈设,前面链路的提示词就要按它查
                    ("assets", "场景选用途、填固定陈设 → 保存", {f'[data-asset="scene-{ids["yard"]}"] [data-scene-furnishings]': "石桌、古井"},
                     [lambda pg: pg.locator(f'[data-asset="scene-{ids["yard"]}"] [data-scene-purpose]').select_option("庭院"),
                      f'[data-asset="scene-{ids["yard"]}"] form button[type=submit]'],
                     f'[data-asset="scene-{ids["yard"]}"] [data-scene-furnishings]:has-text("古井")', 20)), 1):
                errors.clear()
                key, _, ep_key = key.partition(":")               # 「面板:集」—— 默认第 1 集
                p = next(x for x in REGISTRY if x.key == key)
                page.goto(base + url_for(p, ids["drama"], ids[ep_key or "episode"]))
                for sel, value in fill.items():
                    if callable(value):
                        page.locator(sel).set_input_files(str(value()))
                    else:
                        page.locator(sel).fill(value)
                try:
                    for button in buttons:
                        if callable(button):
                            button(page)
                        else:
                            page.locator(button).first.click()
                    if wait_sel.startswith("@"):              # 编辑页:不能刷新(画布和提交结果会丢),原地等
                        page.wait_for_selector(wait_sel[1:], timeout=timeout * 1000)
                        appeared = True
                    else:
                        appeared = _wait_for(page, wait_sel, timeout)
                except Exception as e:                        # 这条链路失败:记下原因、照常截图,接着跑下一条
                    errors.append(f"链路中断:{type(e).__name__}: {str(e).splitlines()[0]}")
                    appeared = False
                shot = out_dir / f"flow_{n}_{key}.png"
                page.screenshot(path=str(shot), full_page=True)
                problems = list(errors) + ([] if appeared else [f"{timeout}s 内没等到 {wait_sel}(停在 {page.url})"])
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
