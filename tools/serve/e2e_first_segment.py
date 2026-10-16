# -*- coding: utf-8 -*-
"""真链路验收:一个段落从无到有 —— 首帧(用一个**非内置的模板预设**)→ 中间关键帧图 → 角色四视图 → 视频(首帧钉在第 0 帧、
关键帧钉在第 96 帧、四视图作参考)→ 对齐字幕,溯源字段逐项核对。预设那一步验证:叠上改动的工作流云机真能跑,且实际提交的就是叠过的那份。
关键帧用调研 B 的设定(spike-results/2026-10-09-中间关键帧锚定实测.md:第 4 秒、转身背对镜头),出片后**用数据判钉没钉住**:
视频第 96 帧与关键帧图的差 < KEY_THRESHOLD(调研 8.7–9.9,对照 54–65)。

这是**验收夹具**,不是生产入口:P3 起同一条链路由控制台的环节面板驱动,
那时本脚本只留作「绕开界面、单测执行层」的对照。

- 在一个**全新的** data_root 里建库、拷素材,不碰任何已有数据;
- 真链路要开卡:它会起隧道、做 LoRA 预检、占用 GPU 约 20 分钟(8 秒片 20 步),四视图约 30 秒,字幕对齐再约 30 秒;
- 任一任务失败即退出码 1,并打印失败原因。

用法(AutoDL 端口每次开机会变):
    python -X utf8 -m tools.serve.e2e_first_segment --data-root tmp/e2e-p2 \\
        --ssh-host connect.westc.gpuhub.com --ssh-port 12345 --key-ref C:/Users/me/.ssh/id_ed25519 \\
        --models-dir /root/autodl-tmp/comfyui-models \\
        --character outputs/gate0/0-2-front_fast_20260923-094028/0-2-front_00001_.png \\
        --scene outputs/gate0/0-3-scene_baseline_20260923-094310/0-3-scene_00001_.png \\
        --prop outputs/gate0/0-3-prop_baseline_20260923-094422/0-3-prop_00001_.png
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from collections.abc import Callable
from pathlib import Path

from tools.drama import repo
from tools.drama.db import Store
from tools.gen.align import plan_align
from tools.gen import presets
from tools.gen.assets import plan_fourview
from tools.cut import keycheck
from tools.gen.planner import plan_first_frame, plan_key_frame, plan_video
from tools.serve.dispatch import SUBMITTED
from tools.serve.hosts import add_host, get_host
from tools.serve.worker import POLL_SEC, Worker

# 溯源必须齐的字段。started_at 不在内:4 步出图可能在两次轮询之间就跑完,没被看到 running,
# 如实留空,而不是编一个时间。
PROVENANCE = ("model", "params_json", "seed", "prompt", "refs_json", "created_at", "host_id",
              "external_id", "result_path", "result_sha256", "finished_at")


def build_drama(store: Store, assets: dict[str, str]) -> int:
    d = repo.create_drama(store, "玉佩", "9:16", "realistic")
    ep = repo.add_episode(store, d, 1, "script", script="林小雨在老宅庭院里回忆母亲。")
    lin = repo.add_character(store, d, "林小雨", turnaround_path=assets["character"], voice_desc="年轻女声,清亮,语速偏慢",
                             final_prompt="A young woman in her early twenties, the same person and outfit as the input image.")
    yard = repo.add_scene(store, d, "老宅庭院", image_path=assets["scene"])
    jade = repo.add_prop(store, d, "玉佩", image_path=assets["prop"])
    seg = repo.add_segment(
        store, ep, 1, scene_id=yard, duration_sec=8.0, reference_mode="first",
        description="【镜头1】林小雨低头看着手里的玉佩。【镜头2】林小雨说：「这块玉佩，是我母亲留给我的。」",
        image_prompt="@林小雨 站在黄昏的 @老宅庭院 里,双手捧着 @玉佩,中景,竖构图",
        video_prompt="@林小雨 在 @老宅庭院 低头看 @玉佩,然后抬眼说话,固定机位,环境声,无配乐")
    repo.bind_characters(store, seg, [lin])
    repo.bind_props(store, seg, [jade])
    key = repo.add_keyframe(store, seg, 4.0, "@林小雨 在黄昏的 @老宅庭院 里转过身,背对镜头,手里握着 @玉佩,中景,竖构图")
    return seg, lin, key


PRESET_SCHEDULER = "normal"      # 首帧模板预设:采样调度器 simple → normal(只改一处,验证叠加机制)
KEY_THRESHOLD = 20               # 关键帧钉住的判据:72×128 灰度平均绝对差(调研钉中 8.7–9.9,对照 54–65)


TUNNEL_WAIT_SEC = 60


def _use_frame_preset(store: Store, worker: Worker, host_id: int, sleep: Callable[[float], None]) -> dict:
    """存一个「首帧 / 尾帧」模板预设并设为当前:用云机的实时节点清单过保存前把关(各种情况逐一试拼)。
    隧道是刚拉起的,`ensure` 只说「此刻通没通」—— 等它通(2026-10-09 真机:第一次检查就判未通退出过)。"""
    host = get_host(store, host_id)
    for _ in range(TUNNEL_WAIT_SEC // 2):
        if worker.tunnel.ensure(host):
            break
        sleep(2)
    else:
        raise RuntimeError(f"隧道 {TUNNEL_WAIT_SEC}s 内没通,拿不到节点清单 —— 查云机是否开机、SSH 端口对不对")
    object_info = worker.client_for(host).object_info()
    edited, _ = presets.example("frame")
    edited["27"]["inputs"]["scheduler"] = PRESET_SCHEDULER
    plan = presets.plan_save(store, "frame", edited, object_info)
    pid = presets.confirm_save(store, plan, "验收:调度器 normal")
    presets.activate(store, "frame", pid)
    return {"id": pid, "lines": list(plan.lines)}


def run_until_final(worker: Worker, store: Store, task_id: int, *, timeout_sec: float,
                    sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> str:
    deadline = clock() + timeout_sec
    while True:
        for line in worker.tick():
            print(line, flush=True)
        row = repo.get(store, "task", task_id)
        if row["state"] in ("done", "failed"):
            return row["state"]
        if clock() > deadline:
            raise TimeoutError(f"task {task_id} {timeout_sec}s 内没结束,停在 {row['state']} —— "
                               f"它可能还在云机上跑,external_id={row['external_id']},不要重新提交")
        sleep(POLL_SEC)


def provenance(store: Store, task_id: int) -> dict:
    row = repo.get(store, "task", task_id)
    missing = [k for k in PROVENANCE if row[k] in (None, "")]
    if missing:
        raise AssertionError(f"task {task_id} 溯源字段缺失:{missing}")
    return {k: row[k] for k in row.keys()}


def run(data_root: Path, images: dict[str, Path], host: dict, *, seed: int, timeout_sec: float,
        make_worker: Callable[[Store, int, Path], Worker] = lambda s, h, root: Worker(s, h, data_root=root),
        sleep: Callable[[float], None] = time.sleep, check_key=keycheck.check) -> dict:
    if data_root.exists() and any(data_root.iterdir()):
        raise FileExistsError(f"{data_root} 不是空目录 —— 验收要在全新目录里跑,不碰已有数据")
    (data_root / "assets").mkdir(parents=True)
    assets = {}
    for kind, src in images.items():
        dst = data_root / "assets" / f"{kind}{Path(src).suffix}"
        shutil.copyfile(src, dst)
        assets[kind] = dst.relative_to(data_root).as_posix()

    store = Store(data_root / "drama.db")
    seg, lin, key = build_drama(store, assets)
    host_id = add_host(store, **host)
    worker = make_worker(store, host_id, data_root)
    try:
        report = {"segment": seg, "tasks": [], "preset": _use_frame_preset(store, worker, host_id, sleep)}

        for plan in (lambda: plan_first_frame(store, seg, seed=seed), lambda: plan_key_frame(store, key, seed=seed),
                     lambda: plan_fourview(store, lin, seed=seed + 2),
                     lambda: plan_video(store, seg, seed=seed + 1), lambda: plan_align(store, seg)):
            tid = plan()
            state = run_until_final(worker, store, tid, timeout_sec=timeout_sec, sleep=sleep)
            if state == "failed":
                raise RuntimeError(f"task {tid} 失败:{repo.get(store, 'task', tid)['error']}")
            report["tasks"].append(provenance(store, tid))
        s = repo.get(store, "segment", seg)
        report["first_frame_path"], report["video_path"] = s["first_frame_path"], s["video_path"]
        report["subtitle_path"] = s["subtitle_path"]
        report["fourview_path"] = repo.get(store, "character", lin)["fourview_path"]
        submitted = json.loads((data_root / "outputs/tasks" / str(report["tasks"][0]["id"]) / SUBMITTED).read_text(encoding="utf-8"))
        if submitted["submitted"]["27"]["inputs"]["scheduler"] != PRESET_SCHEDULER:
            raise RuntimeError("首帧实际提交的工作流里没有叠上预设的改动")
        kcs = check_key(store, data_root, seg)
        unknown = [k.problem for k in kcs if k.diff is None]
        if not kcs or unknown:
            raise RuntimeError(f"关键帧对比算不出来:{';'.join(unknown) or '视频没有钉关键帧'}")
        report["keycheck"] = [{"frame_idx": k.frame_idx, "diff": k.diff, "threshold": KEY_THRESHOLD} for k in kcs]
        missed = [f"第 {k.frame_idx} 帧与关键帧图差 {k.diff}" for k in kcs if k.diff >= KEY_THRESHOLD]
        if missed:
            raise RuntimeError(f"关键帧没钉住:{';'.join(missed)}(判据 < {KEY_THRESHOLD})")
        (data_root / "provenance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        # 隧道是脱离进程起的,不收就一直留着(两次验收后残留);只停本进程起的,接管来的 stop 碰不到
        worker.tunnel.stop(host_id)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P2 真链路验收:一个段落出首帧 + 视频")
    ap.add_argument("--data-root", required=True, type=Path)
    ap.add_argument("--ssh-host", required=True)
    ap.add_argument("--ssh-port", required=True, type=int)
    ap.add_argument("--ssh-user", default="root")
    ap.add_argument("--key-ref", required=True, help="私钥文件路径(只存路径,不读内容)")
    ap.add_argument("--local-port", type=int, default=8188)
    ap.add_argument("--vram-gb", type=float, default=24.0)
    ap.add_argument("--models-dir", required=True)
    ap.add_argument("--character", required=True, type=Path)
    ap.add_argument("--scene", required=True, type=Path)
    ap.add_argument("--prop", required=True, type=Path)
    ap.add_argument("--seed", type=int, default=20260923)
    ap.add_argument("--timeout-min", type=float, default=60)
    a = ap.parse_args(argv)

    host = {"label": "e2e", "ssh_host": a.ssh_host, "ssh_port": a.ssh_port, "ssh_user": a.ssh_user,
            "key_ref": a.key_ref, "local_port": a.local_port, "vram_gb": a.vram_gb, "models_dir": a.models_dir}
    report = run(a.data_root, {"character": a.character, "scene": a.scene, "prop": a.prop}, host,
                 seed=a.seed, timeout_sec=a.timeout_min * 60)
    print(f"首帧:{a.data_root / report['first_frame_path']}")
    print(f"四视图:{a.data_root / report['fourview_path']}")
    print(f"视频:{a.data_root / report['video_path']}")
    print(f"字幕:{a.data_root / report['subtitle_path']}")
    print(f"溯源:{a.data_root / 'provenance.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
