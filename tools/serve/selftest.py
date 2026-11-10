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
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import uvicorn

from tools.drama import approval, repo
from tools.drama.db import Store
from tools.gen.assets import _png
from tools.serve.app import create_app
from tools.cut import lipsync, prosody
from tools.serve.fakes import fake_backend, lip_measure_following_audio
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
    "asset_review": "[data-asset-review] [data-approve-clean]",
    "storyboard": "[data-segment-list] [data-segment]",
    "frames": "[data-generate-frame]",
    "videos": "[data-readiness] .checklist",
    "final": "[data-export-form]",
    "workflows": "[data-preset-category]",
    "hosts": "[data-host-status]",
    "trace": "[data-trace-filters]",
    "refs_sources": "[data-ref-source]",
    "refs_ranks": "[data-ref-rank-row]",
    "refs_work": "[data-ref-content]",
    "refs_record": "[data-ref-record]",
    "refs_cards": "[data-ref-card-row]",
}


# 演示剧的素材(用户 2026-10-28 定:用最新生成的真图真视频,不用彩条 / 灰图 / 老图):
# 《回到古代做将军》的资产图、第 1 集首帧与定稿视频,缩小后放在仓库里;自测不上云机,假 GPU 把它们当产物交出去
DEMO_ASSETS = Path(__file__).resolve().parent / "demo_assets"


def demo_products() -> dict:
    """假 GPU 交出去的真产物:第 1 集刚出的首帧、定稿视频与一张四视图(缩小后在仓库里)。"""
    return {"videos": [(DEMO_ASSETS / f"video{n}.mp4").read_bytes() for n in range(1, 6)],
            "frames": [(DEMO_ASSETS / f"frame{n}.png").read_bytes() for n in range(1, 6)],
            "fourview": (DEMO_ASSETS / "fourview.png").read_bytes()}


def seed_demo(store: Store, data_root: Path) -> dict:
    """一部写实风格的演示剧:一集、两个素材齐全的段落(第 2 段的首帧留给尾帧衔接)、一台假云机;
    第 2 集一个「首尾帧」模式的段落,和一个三角色 + 场景同框、首帧已有的段落(两遍合成提示 + 局部替换)
    —— 都放第 2 集,不影响第 1 集的导出链路。"""
    for name in ("lin", "yard", "jade", "shen", "zhou"):      # 真资产图(缩小,在仓库里):资产图审 / 首帧页面看得出真东西
        (data_root / "assets").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DEMO_ASSETS / f"{name}.jpg", data_root / f"assets/{name}.jpg")
    d = repo.create_drama(store, "玉佩(自测)", "9:16", "realistic")
    ep = repo.add_episode(store, d, 1, "script", script="林小雨在老宅庭院里回忆母亲。" * 20 + "\n林小雨说：「这块玉佩，是我母亲留给我的。」\n")
    lin = repo.add_character(store, d, "林小雨", turnaround_path="assets/lin.jpg", voice_desc="年轻女声,清亮",
                             appearance="二十出头,长发,素色上衣", final_prompt="A young woman in a cream blouse.")
    yard = repo.add_scene(store, d, "老宅庭院", image_path="assets/yard.jpg", final_prompt="An empty courtyard at dusk.")
    jade = repo.add_prop(store, d, "玉佩", image_path="assets/jade.jpg", final_prompt="A green jade pendant.")
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
    shen = repo.add_character(store, d, "沈墨", turnaround_path="assets/shen.jpg", voice_desc="青年男声,沉稳",
                              appearance="二十七八,短发,深色长衫")
    zhou = repo.add_character(store, d, "周伯", turnaround_path="assets/zhou.jpg", voice_desc="老年男声,沙哑",
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
    for table, row_id in (("character", lin), ("character", shen), ("character", zhou), ("scene", yard), ("prop", jade)):
        approval.approve(store, data_root, table, row_id, "image", failed=["AI 检查没有结果"],
                         reason="自测演示数据:真资产图取自题甲,自测里不跑真 AI 检查")
    add_host(store, label="假云机", ssh_host="fake.local", ssh_port=1, ssh_user="root", key_ref="C:/fake/key",
             local_port=8188, vram_gb=24.0, models_dir="/fake/models", gpu="RTX 4090")
    return {"drama": d, "episode": ep, "segment": seg, "segment2": seg2, "photo": photo, "lin": lin,
            "episode2": ep2, "episode3": ep3, "segment3": seg3, "segment4": seg4, "zhou": zhou, "yard": yard}


# 参考库自测:真解析器 + 假网络。URL 与各适配器拼的地址一致,页面是 tests/refs/fixtures/ 里的脱敏快照
REFS_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "refs" / "fixtures"
FAKE_PAGES = {
    "https://hongguoduanju.com/rank/hot-real-drama": "hongguo_rank.html",
    "https://hongguoduanju.com/detail?series_id=7561390625552092222": "hongguo_detail.html",
    "https://www.jjwxc.net/topten.php?orderstr=7": "jjwxc_rank.html",
    "https://www.jjwxc.net/onebook.php?novelid=6771725": "jjwxc_book.html",
    "https://www.jjwxc.net/onebook.php?novelid=6771725&chapterid=1": "jjwxc_chapter.html",
}
REFS_BOOK = "6771725"
REFS_SERIES = "7561390625552092222"       # 红果详情快照里的那部(有 vid_list)


class SelftestDriver:
    """自测的假录屏浏览器(Driver 接口):不开真浏览器、不联网,每集「录」出一份 ffmpeg 造的 10 秒带声音的 webm。
    控制台的 Runtime 也注入它 —— 自测里谁点了「录这部」都不会弹真红果页面。"""
    def __init__(self, clip: Path):
        self.clip = clip

    def open(self, series_id):
        pass

    def logged_in(self):
        return True

    def go_episode(self, n, vid):
        pass

    def locked(self):
        return None

    def record_current(self, out, on_tick=None):
        shutil.copyfile(self.clip, out)
        return {"duration": 10.0, "box": (0, 0, 320, 240), "viewport": (320, 240)}

    def close(self):
        pass


class _AsrClient:
    """假 ComfyUI 客户端:队列空、显存够(transcribe 经 gpu_ready 问它)。"""
    def queue_ids(self):
        return [], []

    def vram_free_gb(self):
        return 24.0

    def free_models(self):
        pass


def _fake_asr(args, script):
    text = "你怎么还在这里我不是让你走了吗今天这件事没完我们走着瞧这块玉佩是我母亲留给我的我会一直带着它谁也别想拿走"
    chars = [[c, round(i * 0.12, 2), round(i * 0.12 + 0.12, 2)] for i, c in enumerate(text)]
    return 0, json.dumps({"text": text, "chars": chars, "chunks": 1, "asr_sec": 0.1}, ensure_ascii=False)


def _fake_login(profile, series_id) -> bool:
    """自测的「打开浏览器扫码」:不开真浏览器、不碰红果,停一秒当扫上了。"""
    time.sleep(1)
    return True


def _fake_opener(url: str, headers: dict, timeout: float):
    """假网络:robots 一律放行;快照里有的页返回快照(晋江按 gb18030 编码,让解码路径和真网一样),其他 404。
    最终地址就是请求地址(net.Fetcher 会判跳转到别的主机 / 登录页)。"""
    if url.endswith("/robots.txt"):
        return 200, b"User-agent: *\nAllow: /\n", url
    name = FAKE_PAGES.get(url)
    if name is None:
        return 404, b"", url
    text = (REFS_FIXTURES / name).read_text(encoding="utf-8")
    return 200, text.encode("gb18030" if "jjwxc" in url else "utf-8"), url


def seed_refs(work: Path):
    """参考库演示数据:红果真人剧榜、晋江总分榜 + 一部作品的详情与第 1 章,都由真解析器从快照解析;
    再补一条前一天的热度(曲线两点)、手工熔断 k17(来源设置页看得到熔断)。"""
    from tools.refs import clock, heat, jobs, runner, settings, works
    from tools.refs.db import RefStore
    from tools.refs.net import Fetcher
    root = work / "refs"
    root.mkdir(parents=True, exist_ok=True)
    refs = RefStore(root / "refs.db")
    settings.ensure(refs)
    for key, form in (("jjwxc", {"retention_days": "30", "chapters_n": "1"}), ("hongguo", {"retention_days": "30"}),
                      ("manual", {"retention_days": "30"})):
        settings.update(refs, root, key, form)
    today = clock.today()
    fetcher = lambda src, wid: Fetcher(root, src, opener=_fake_opener, sleep=lambda s: None, work_id=wid)  # noqa: E731
    jobs.enqueue(refs, "hongguo", "ranks", day=today, params={"board": "hot-real-drama"})
    jobs.enqueue(refs, "jjwxc", "ranks", day=today, params={"board": "topten7"})
    book = works.upsert(refs, "jjwxc", REFS_BOOK)
    jobs.enqueue(refs, "jjwxc", "meta", work_id=book, day=today)
    jobs.enqueue(refs, "jjwxc", "content", work_id=book, day=today)
    while (j := runner.run_one(refs, root, fetcher=fetcher, today=today)) is not None:
        if j["state"] != "done":
            raise RuntimeError(f"参考库演示数据没灌进去:任务 {j['id']}({j['source']} {j['kind']}){j['error']}")
    yesterday = clock.add_days(f"{today}T12:00:00+08:00", -1)[:10]
    heat.record(refs, book, "detail", yesterday, None, 1.0e9, {"note": "自测造的前一天热度"})
    settings.trip(refs, root, "k17", "自测:演示熔断(假装被风控挡住)", today)
    driver = _seed_recording(refs, root, work, fetcher, today)
    series = refs.conn.execute("SELECT id FROM ref_work WHERE source = 'hongguo' AND external_id = ?", (REFS_SERIES,)).fetchone()[0]
    _seed_card(refs, root, series)
    return refs, {"refs_book": book, "record_driver": driver, "refs_fetcher": fetcher, "refs_series": series}


CARD_QUOTE = "这块玉佩是我母亲留给我的"          # 出自 _fake_asr 的转写
NO_PERIOD = {"from": "", "to": ""}
# 拆解卡演示用的假 LLM 产物(_seed_card)
CARD = {"genre": "古装甜宠", "payoff": "复仇", "conflict": "情感背叛", "hook_type": "直接冲突",
        "persona": {"protagonist": {"label": "守着遗物的孤女", "contrast": "看着柔弱,寸步不让"},
                    "antagonist": {"label": "上门逼迫的人", "contrast": "人前客气,人后紧逼"}},
        "cast_types": [],
        "hypotheses": [
            {"claim": "开场就抛出遗物之争,冲突前置", "evidence": "剧情", "units": [1], "review_ids": [], "quote": CARD_QUOTE, "period": NO_PERIOD},
            {"claim": "每集都回到同一件信物,观众记得住", "evidence": "剧情", "units": [2], "review_ids": [], "quote": CARD_QUOTE, "period": NO_PERIOD},
            {"claim": "第 3 集又一次拿信物做文章", "evidence": "剧情", "units": [3], "review_ids": [], "quote": CARD_QUOTE,
             "period": NO_PERIOD}],
        "techniques": ["开场一分钟内让主角为一件信物与人正面冲突"], "not_borrowable": ["那件信物的具体来历"]}


def _seed_card(refs, root: Path, wid: int) -> None:
    """拆解卡面板的演示数据:用**假 LLM**(llm_mode = fake,永远过不了审批闸门)给录好的 5 集各出一条单元标注,再出一张待审的卡。
    走真的 service.begin / finish(判卷照常跑);自测只演示到「点批准 → 假产物被拒」,不调真 LLM。"""
    from tools.llm import ref_card, ref_unit, service
    from tools.llm.fake import FakeLLM
    from tools.refs import clock
    unit = lambda schema: {"hook": "有", "reversal": "小", "emotion": "压", "paywall": "否",            # noqa: E731
                           "summary": "女主护住母亲遗物,和来人起了争执。", "quote": CARD_QUOTE, "names": [],
                           "visual_hook": "有", "visual_sec": schema["properties"]["visual_sec"]["enum"][0]}

    def card(schema):          # 热度类只在有热度历史时可选(schema 的 enum 说了算);这部通常只见过今天,第 3 条是剧情类
        out = dict(CARD)
        hyp = schema["properties"]["hypotheses"]["items"]["properties"]
        days = hyp["period"]["properties"]["from"]["enum"][1:]
        if "热度" in hyp["evidence"]["enum"]:
            out["hypotheses"] = [*CARD["hypotheses"][:2], {"claim": "上线后热度涨得快", "evidence": "热度", "units": [], "review_ids": [],
                                                           "quote": "", "period": {"from": days[0], "to": days[-1]}}]
        return out
    llm = service.LLMBackend(FakeLLM({ref_unit.TOOL: unit, ref_card.TOOL: card}), "selftest", "fake")
    cids = [r[0] for r in refs.conn.execute("SELECT id FROM ref_content WHERE work_id = ? AND kind = 'transcript' AND verdict = 'ok'"
                                            " AND purged_at = '' ORDER BY unit", (wid,))]
    for task, tid, params in [*(("ref_unit", c, {}) for c in cids), ("ref_card", wid, {"heat_until": clock.today()})]:
        rid, _ = service.begin(refs, task, tid, params, llm, data_root=root)
        kind = service.finish(refs, rid, llm, root / "journal", data_root=root)
        if kind is not None:
            row = refs.conn.execute("SELECT error FROM llm_run WHERE id = ?", (rid,)).fetchone()
            raise RuntimeError(f"拆解卡演示数据没灌进去:{task} {tid} 判失败({kind}):{row['error']}")


def _seed_recording(refs, root: Path, work: Path, fetcher, today: str) -> SelftestDriver:
    """录屏面板的演示数据:真 recorder.run_job(假 driver、快照详情)录 5 集 → 检查、关键帧、排转写;
    再用假云机把转写跑完(真 transcribe.run_one,假 ssh runner)—— 不跑完的话 worker 空闲钩子会拿假对齐脚本去转、全失败。"""
    from tools.refs import jobs, recorder, transcribe, works
    clip = work / "selftest_recording.webm"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x240:d=10", "-f", "lavfi", "-i", "sine=f=440:d=10",
                    "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-c:a", "libopus", "-shortest", str(clip)], check=True)
    driver = SelftestDriver(clip)
    wid = works.upsert(refs, "hongguo", REFS_SERIES)
    jid = jobs.enqueue(refs, "hongguo", "record", day=today, work_id=wid)
    recorder.run_job(refs, root, jobs.get(refs, jid), lambda: driver, fetcher=fetcher, today=today)
    j = jobs.get(refs, jid)
    if j["state"] != "done":
        raise RuntimeError(f"录屏演示数据没灌进去:{j['error']}")
    for _ in range(10):                    # 一集一条转写;有限轮,卡住(等显存之类)不在这里死循环
        if transcribe.run_one(refs, root, _AsrClient(), _fake_asr) is None:
            break
    left = refs.conn.execute("SELECT COUNT(*) FROM ref_job WHERE kind = 'transcribe' AND state != 'done'").fetchone()[0]
    if left:
        raise RuntimeError(f"转写演示数据没灌完:还有 {left} 条没跑成")
    return driver


def _check_ref_view(page) -> None:
    """录屏详情页:关键帧和转写句子都要真渲染出来(不是只有播放器)。"""
    page.wait_for_selector("[data-ref-player]", timeout=10000)
    kf, sents = page.locator("[data-kf]").count(), page.locator("[data-sentence]").count()
    if kf == 0 or sents == 0:
        raise RuntimeError(f"录屏详情页内容是空的:关键帧 {kf} 张、转写 {sents} 句")


EXPECTED_400 = "Failed to load resource: the server responded with a status of 400 (Bad Request)"


def _card_choices(page) -> None:
    """拆解卡审阅页:第 1、3 条采纳、第 2 条驳回,补一条不可借鉴;等预览按这组勾选刷新(采纳 0,2、没有还差的)再点批准。"""
    page.wait_for_selector("[data-creation-view]", timeout=10000)
    for i, v in ((0, "adopt"), (1, "reject"), (2, "adopt")):
        page.locator(f'[data-hyp="{i}"] input[value="{v}"]').check()
    page.locator("[data-extra-not-borrowable]").fill("自测补的:片头那句台词")
    page.locator("[data-extra-not-borrowable]").dispatch_event("change")
    page.wait_for_selector('[data-creation-view][data-adopted="0,2"]', timeout=10000)
    if page.locator("[data-creation-view] [data-decision-problem]").count():
        raise RuntimeError("全部表态后预览里还写着「批准前还差」")
    if page.locator("[data-creation-view]:has-text('自测补的')").count() == 0:
        raise RuntimeError("预览没带上补充的「不可借鉴」")
    if page.locator("[data-fragments]").count():          # 原文高频片段只是提示:勾「看过了」,预览要跟着放行
        page.locator("[data-ack-fragments]").check()
        page.wait_for_selector("[data-fragments].hint", timeout=10000)
    page.locator("[data-card-approve]").click()


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


def _lip_fail(store: Store, root: Path, segment_id: int):
    """假后端的片口型一律「过」(fakes.lip_measure_following_audio):把这段当前视频的口型缓存改成「嘴不动」量出来的结果,
    卡片只读缓存,应出「不过」与放行表单。量法本身不假 —— 走的是 lipsync.judge。"""
    def act(page) -> None:
        from dataclasses import asdict
        from tools.drama.tasks import producer
        from tools.serve import lipgate
        from tools.serve.dispatch import sha256_file
        seg = repo.get(store, "segment", segment_id)
        spans = lipgate.spans_of(root, producer(store, "video", seg["video_path"]))
        if spans is None:
            raise RuntimeError(f"段落 {segment_id} 的当前视频没钉台词音频,造不出口型「不过」")
        env = [0.05 + 0.04 * (t % 6) / 6 for t in range(int(max(e for _, e in spans) * 24) + 24)]
        lines = lipsync.judge(24.0, [[(0.4, 0.01)] for _ in env], env, spans)
        (root / f"{seg['video_path']}.lipsync.json").write_text(json.dumps(
            {"video_sha256": sha256_file(root / seg["video_path"]), "spans": spans, "params": lipsync.PARAMS,
             "lines": [asdict(x) for x in lines]}, ensure_ascii=False), encoding="utf-8")
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


def _heard(backend, text: str):
    """换假对齐脚本「听到的话」(两段各自重新对齐时用)。"""
    def act(page) -> None:
        backend.align_for(None).heard = text
    return act


def _silent_wav(path: Path, seconds: float = 2) -> Path:
    """自测导入念法模板用的 2 秒静音 wav(16 位 PCM,16 kHz)。"""
    import wave
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * int(16000 * seconds))
    return path


def _voice_approve(char_id: int):
    """角色声音:等假后端交回试听音(反复刷新),试听控件在,再定稿(假回听没听到话 → 要写理由)。"""
    def act(page) -> None:
        card = f"#character-{char_id}-voice"
        deadline = time.monotonic() + 30
        while not page.locator(f"{card} audio").count():
            if time.monotonic() > deadline:
                raise RuntimeError("30 秒内试听音没出来")
            page.wait_for_timeout(1000)
            page.reload()
        reason = page.locator(f"{card} input[name=reason]")
        if reason.count():
            reason.fill("自测:假后端的静音试听音,回听自然听不到话")
        page.locator(f"{card} [data-voice-approve]").click()
        page.wait_for_load_state()
    return act


def _until_gone(selector: str, timeout: float = 20):
    """反复刷新,直到 selector 不在页面上(后台任务做完才会消失)。"""
    def act(page) -> None:
        deadline = time.monotonic() + timeout
        while page.locator(selector).count():
            if time.monotonic() > deadline:
                raise RuntimeError(f"{timeout:g} 秒内 {selector} 一直还在")
            page.wait_for_timeout(1000)
            page.reload()
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
    backend, _ = fake_backend(demo_products())
    lipsync.measure = lip_measure_following_audio     # 假 GPU 的片不是照台词音频生成的,量它的口型没有意义(见 fakes 说明)
    prosody.template_problems = lambda x, sr: []      # 自测导入的念法模板是静音 wav;韵律门槛由单测覆盖(tests/cut/test_prosody.py)
    tpl_wav = _silent_wav(work / "selftest_template.wav")
    backend.align_for(None).heard = "这块玉佩是我母亲留给我的我会一直带着它"     # 两段的台词
    refs, ref_ids = seed_refs(work)
    rec_driver = ref_ids["record_driver"]
    runtime = Runtime(store, work, backend, poll_sec=1, refs=refs, refs_root=work / "refs", refs_poll_sec=3600,
                      record_driver=lambda: rec_driver, refs_fetcher=ref_ids["refs_fetcher"], login_fn=_fake_login)
    # 自测期间参考库后台线程不许自己联网(monitor.plan 会按设置排真抓取、runner 会走真 Fetcher):
    # 一轮只做到期清理,演示数据已由 seed_refs 用快照灌好
    from tools.refs import retention
    runtime.refs_tick = lambda: retention.purge(refs, work / "refs")
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
            got_400: list[str] = []          # 收到的 400 响应地址(expect_400 只认批准拆解卡那一条)
            page.on("response", lambda r: got_400.append(r.url) if r.status == 400 else None)
            page.on("request", lambda r: errors.append(f"外网请求:{r.url}")
                    if not r.url.startswith((base, "data:", "blob:", base.replace("http", "ws"))) else None)

            for i, p in enumerate(REGISTRY, 1):
                errors.clear()
                url = base + url_for(p, ids["drama"], ids["episode"])
                if p.key == "refs_ranks":                   # 红果默认榜是热播榜,快照灌的是真人剧榜
                    url += "&source=hongguo&board=hot-real-drama"
                elif p.key == "refs_work":                  # 有详情、有正文、有两天热度的那部
                    url += f"&id={ref_ids['refs_book']}"
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

            def expect_400(pg) -> None:
                """这一步要的就是批准拆解卡被拒的 400 页:response 事件确认 400 来自 `/refs/card/<号>/approve`、恰好一条,
                Chrome 因它记的 console.error 也只放过一条(别的 400 照样算错,审查 M7)。"""
                pg.wait_for_selector("[data-error]", timeout=10000)
                pg.wait_for_load_state("load")
                hits = [u for u in got_400 if re.search(r"/refs/card/\d+/approve$", u.split("?")[0])]
                if len(hits) != 1:
                    raise RuntimeError(f"要的是批准拆解卡那一条 400,实际收到的 400:{got_400 or '无'}")
                got_400.remove(hits[0])
                errors.remove(f"console.error:{EXPECTED_400}")        # 只放过一条;没有这一条会抛 ValueError

            seg, seg2, seg3, photo, lin = ids["segment"], ids["segment2"], ids["segment3"], ids["photo"], ids["lin"]
            seg4, zhou = ids["segment4"], ids["zhou"]
            bundle_zip = lambda: next((work / "outputs" / "bundles").glob("*.zip"))   # noqa: E731 —— 上一条链路才产生
            for n, (key, label, fill, buttons, wait_sel, timeout) in enumerate((
                    # 资产图定稿闸门(spec 2026-10-28 第 6 节):说话的角色要有定稿的四视图才能出视频
                    ("assets", "生成四视图", {}, [f'[data-asset="character-{lin}"] [data-generate-fourview]'],
                     f'[data-asset="character-{lin}"] [data-fourview] img', 40),
                    ("asset_review", "资产图审:四视图没经 AI 检查 → 写理由定稿",
                     {f"#character-{lin}-fourview [data-approve-reason]": "自测:四视图是假 GPU 交回的演示图,没跑 AI 检查"},
                     [f"#character-{lin}-fourview [data-approve]"], f"#character-{lin}-fourview [data-approval]", 20),
                    # 台词配音第 1 期(spec 2026-10-30):按声音描述出 3 版试听音 → 试听 → 定稿
                    ("asset_review", "角色声音:生成 3 版试听 → 定稿", {},
                     [f"#character-{lin}-voice [data-voice-design]", _voice_approve(lin)],
                     f"#character-{lin}-voice [data-voice-approved]", 40),
                    # IndexTTS2 港片念法(spec 2026-10-31):台词音频要念法模板 —— 导入一段(来源必填),给林小雨选上
                    ("asset_review", "念法模板:导入一段(写明来源)", {
                        "[data-voice-templates] input[name=name]": "喝令",
                        "[data-voice-templates] input[name=source]": "自测:假后端合成的静音",
                        "[data-voice-templates] input[type=file]": lambda: tpl_wav},
                     ["[data-template-import]"], "[data-voice-templates] [data-template]", 20),
                    ("asset_review", "给林小雨选念法模板 → 保存", {},
                     [lambda pg: pg.locator(f"#character-{lin}-voice [data-voice-template-select]").select_option(label="喝令"),
                      f"#character-{lin}-voice [data-voice-template-save]"],
                     f'#character-{lin}-voice [data-voice-template-select] option[selected]:has-text("喝令")', 20),
                    # 台词配音第 2 期:林小雨音色定稿后,段落 1 按它合成台词音频,逐句回听命中(假回听多听到的半句不影响)
                    ("videos", "台词音频:合成 → 试听 + 逐句命中", {}, [f"#seg-{seg} [data-synthesize]"],
                     f"#seg-{seg} [data-line-audio] [data-line-hits]", 40),
                    # 第 3 期:视频按台词音频出(没有就不许规划),后面「补全视频」要给段落 2 出片,先合成它的
                    ("videos", "段落 2 也合成台词音频(补全视频要用)", {}, [f"#seg-{seg2} [data-synthesize]"],
                     f"#seg-{seg2} [data-line-audio] [data-line-hits]", 40),
                    ("frames", "生成首帧", {}, [f"#seg-{seg} [data-generate-frame]"], f"#seg-{seg} .current-media img", 40),
                    ("videos", "生成视频", {}, [f"#seg-{seg} [data-generate-video]"], f"#seg-{seg} .current-media video", 60),
                    # 口型闸门(spec 2026-10-30 第 6.3 节):worker 收到视频就量(假后端用 fakes.lip_measure_following_audio,嘴随声动)
                    ("videos", "出片后自动量口型 → 每句「过」", {}, [], f'#seg-{seg} [data-lip-verdict="过"]', 60),
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
                    # 成片质量闸门第一期(spec 2026-10-28 第 3 节):多说的拦导出;各段按自己的台词重新对齐后放行
                    ("final", "台词外多说的段落列在导出检查的问题里", {}, [], 'li.bad:has-text("台词之外多说")', 20),
                    ("videos", "段落 1、2 各自重新对齐(没多说)→ 多说提醒消失", {},
                     [_heard(backend, "这块玉佩是我母亲留给我的"), f"#seg-{seg} [data-align]",
                      _until_gone(f"#seg-{seg} [data-extra-speech]"),
                      _heard(backend, "我会一直带着它"), f"#seg-{seg2} [data-align]",
                      _until_gone(f"#seg-{seg2} [data-extra-speech]")], f"#seg-{seg} [data-cue]", 60),
                    ("videos", "看片区:切镜计数 + 上一段末帧 / 本段首帧并排", {}, [], f"#seg-{seg2} [data-seam] img", 30),
                    ("final", "没看片确认的段落列在导出检查的问题里", {}, [], 'li.bad:has-text("还没看片确认")', 20),
                    ("videos", "验收清单少勾一项 → 确认按钮不可点;勾全 → 可点", {},
                     [_partial_ticks(seg2)], f"#seg-{seg2} [data-check-item]", 20),
                    ("videos", "段落 1、2 逐项勾选验收清单 → 确认 → 卡片显示已确认", {},
                     [_review(seg), _review(seg2)], f"#seg-{seg2} [data-reviewed]", 20),
                    ("final", "导出成片", {"[data-producer]": "SELFTEST-ONLY"}, ["[data-export]"],
                     # first export OCRs every segment cold on CPU; the panel itself promises "1–3 分钟"
                     '[data-export-state="done"] [data-export-video]', 180),
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
                    ("assets", "改声音描述 → 直接保存(不作废视频)",
                     {f'[data-asset="character-{lin}"] textarea[name=voice_desc]': "低沉女声,语速偏慢"},
                     [f'form[action="/asset/character/{lin}"] button'],
                     f'[data-asset="character-{lin}"] textarea[name=voice_desc]:has-text("低沉女声,语速偏慢")', 20),
                    ("assets", "上传参考图", {f'[data-asset="prop-{photo}"] [data-upload-file]': lambda: work / "upload_demo.png"},
                     [f'[data-asset="prop-{photo}"] [data-upload-button]'], f'[data-asset="prop-{photo}"] .current-media img', 20),
                    ("assets", "从图反推描述", {}, [f'[data-asset="prop-{photo}"] [data-llm-describe]'],
                     '[data-llm-state="failed"] [data-llm-error]', 40),
                    ("trace", "只看失败 → 失败归类里有刚才那次反推", {}, ["[data-failed-only]", "[data-trace-apply]"],
                     '[data-trace-timeline] [data-event="llm"][data-action="失败"]', 20),
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
                    # 口型人工看片放行(用户 2026-11-03 定):不过 → 卡片出表单 → 填理由放行 → 卡片显示放行记录
                    ("videos", "口型不过 → 视频卡片出放行表单", {}, [_lip_fail(store, work, seg)],
                     f"#seg-{seg} [data-lip-form]", 20),
                    ("videos", "填理由 → 看过,口型可以接受(放行)→ 卡片显示「已口型人工确认放行」",
                     {f"#seg-{seg} [data-lip-reason]": "侧脸说话,看片嘴型对得上"}, [f"#seg-{seg} [data-lip-confirm]"],
                     f"#seg-{seg} [data-lip-override]", 20),
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
                    # 参考库录屏面板:扫码登录(假 login_fn,不开浏览器)→ 登录结论来自「扫码登录」
                    ("refs_record", "打开浏览器扫码 → 已登录", {}, ["[data-ref-login] form button[type=submit]"],
                     '[data-ref-login-state="ok"][data-ref-login-via="扫码登录"]', 20),
                    ("refs_record", "第 1 集点「查看」→ 播放器、关键帧、转写句子都出来", {},
                     ["[data-ref-view-link]", _check_ref_view],
                     "[data-ref-player]", 20),
                    # 拆解卡(1c):种子是假 LLM 出的待审卡(5 个单元 + 卡)。审阅页 → 逐条表态 → 预览刷新 → 批准被闸门拒
                    ("refs_cards", "点「审阅」→ 假设、逐单元表、创作视图预览都出来", {},
                     [f'[data-ref-card-row="{ref_ids["refs_series"]}"] [data-ref-card-link]'],
                     "[data-creation-view] [data-decision-problem]", 20),
                    ("refs_cards", "逐条采纳 / 驳回 → 预览按勾选刷新 → 点批准 → 「假 LLM 的产物不许通过审批闸门」(400)", {},
                     [f'[data-ref-card-row="{ref_ids["refs_series"]}"] [data-ref-card-link]', _card_choices, expect_400],
                     '@[data-error]:has-text("假 LLM 的产物不许通过审批闸门")', 20),
                    # 放在最后:填了陈设,前面链路的提示词就要按它查
                    ("assets", "场景选用途、填固定陈设 → 保存", {f'[data-asset="scene-{ids["yard"]}"] [data-scene-furnishings]': "石桌、古井"},
                     [lambda pg: pg.locator(f'[data-asset="scene-{ids["yard"]}"] [data-scene-purpose]').select_option("庭院"),
                      f'[data-asset="scene-{ids["yard"]}"] form button[type=submit]'],
                     f'[data-asset="scene-{ids["yard"]}"] [data-scene-furnishings]:has-text("古井")', 20),
                    # 港片画风(spec 2026-10-31):放在最后 —— 切画风会改之后所有提示词的拼法
                    ("episodes", "美术设定选画风「90 年代港产武侠片」→ 保存", {"[data-art-era]": "架空古代,类北宋边军"},
                     [lambda pg: pg.locator("[data-art-style]").select_option("hk90"), "[data-art-save]"],
                     '[data-art-style] option[value="hk90"][selected]', 20),
                    ("videos", "港片画风:点「预览导出调色」→ 出调色预览视频", {}, [f"#seg-{seg} [data-grade-preview-btn]"],
                     f"#seg-{seg} [data-grade-preview-video]", 60)), 1):
                errors.clear()
                got_400.clear()
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
        refs.close()
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
