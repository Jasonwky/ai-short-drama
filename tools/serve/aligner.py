# -*- coding: utf-8 -*-
"""执行一条字幕对齐任务:视频传到云机 → 云机 GPU 上 whisper 出逐字时间 → **本机**算每句起止、判卷、写 SRT。

与 `dispatch` 同一个签名、同一套结局,worker 按 task.kind 选一个调:
- 连不上云机(上传失败 / ssh 连不上,退出码 255)→ **留在 `pending`**,下一轮再试;
- 其余失败(视频不在、规划后视频被换了、云机脚本报错、判卷不过)→ `failed` 写明原因;
- 成功 → 一次走完 `queued → done`(对齐 30 秒内同步跑完,不进 ComfyUI 队列,没有可接管的在途状态)。
  中途进程被杀,任务还在 `pending`,下一轮重跑 —— 对齐是确定性的、便宜的,重跑无害。

**判卷不过不出字幕**(用数据审 LLM 的同一条纪律:识别结果也是模型输出):命中率低、时间不单调、超片长都算失败,
`align.json` 照样落盘(逐字时间、能量曲线、每句判定),人可以查是识别错了还是台词与画面对不上。
写回只填空位(与 collect 一致):段落已有字幕时,新版本留在任务记录里等人挑。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tools.cut import media, subtitles
from tools.drama import repo
from tools.drama.db import Store
from tools.drama.tasks import set_task_state
from tools.gen import preflight
from tools.gen.capability import WHISPER_ALIGN
from tools.gen.comfy_client import ComfyError, ComfyUnreachable
from tools.serve.dispatch import Outcome, sha256_file
from tools.serve.hosts import Host

SCRIPT = Path(__file__).with_name("align_remote.py")
SSH_UNREACHABLE = 255


def parse_output(out: str) -> list[tuple[str, float, float]]:
    """云机脚本的输出(stdout 与 stderr 混在一起):取最后一行以 `{"chars"` 开头的 JSON。"""
    rows = [line for line in out.splitlines() if line.startswith('{"chars"')]
    if not rows:
        raise ValueError(f"云机脚本没有输出结果:{out.strip()[-500:]}")
    return [tuple(c) for c in json.loads(rows[-1])["chars"]]


def run(store: Store, client, host: Host, task: sqlite3.Row, *, data_root: Path, run: preflight.Runner) -> Outcome:
    tid = task["id"]
    root = Path(data_root)
    p = json.loads(task["params_json"])

    def fail(msg: str) -> Outcome:
        set_task_state(store, tid, "failed", error=msg)
        return Outcome("failed", msg)

    video = root / p["video"]
    if not video.is_file():
        return fail(f"视频不存在:{p['video']}")
    seg = repo.get(store, "segment", task["target_id"])
    if seg["video_path"] != p["video"]:
        return fail(f"规划后段落换了视频({p['video']} → {seg['video_path']}),这条对齐作废 —— 请对新视频重新对齐")

    try:
        name = client.upload_image(video, subfolder=f"ai-short-drama/{sha256_file(video)[:16]}")
    except ComfyUnreachable as e:
        return Outcome("pending", f"连不上云机,下轮再试:{e}")
    except ComfyError as e:
        return fail(f"视频传不上云机:{e}")
    code, out = run([WHISPER_ALIGN.model_dir, f"{WHISPER_ALIGN.input_dir}/{name}"], SCRIPT.read_bytes())
    if code == SSH_UNREACHABLE:
        return Outcome("pending", f"ssh 连不上云机,下轮再试:{out.strip()[-300:]}")
    if code != 0:
        return fail(f"云机对齐脚本失败(退出码 {code}):{out.strip()[-500:]}")
    try:
        chars = parse_output(out)
    except ValueError as e:
        return fail(str(e))

    envelope = media.rms_envelope(video)
    duration = media.probe(video).duration
    cues, problems = subtitles.build(p["lines"], chars, envelope, duration)
    out_dir = root / "outputs" / "tasks" / str(tid)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "align.json").write_text(json.dumps(
        {"lines": p["lines"], "chars": chars, "envelope": envelope, "duration": duration,
         "cues": [c.__dict__ for c in cues], "problems": problems}, ensure_ascii=False, indent=1), encoding="utf-8")
    if problems:
        return fail("判卷不过:" + ";".join(problems) + f"(逐字时间见 outputs/tasks/{tid}/align.json)")

    srt = out_dir / "subtitles.srt"
    srt.write_text(subtitles.srt(cues), encoding="utf-8")
    rel = srt.relative_to(root).as_posix()
    set_task_state(store, tid, "queued", external_id=f"align-{tid}", host_id=host.id)
    note = ""
    if repo.get(store, "segment", task["target_id"])["subtitle_path"]:
        note = "subtitle_path 已有字幕,新版本留在任务记录里待人挑选"
    else:
        repo.update(store, "segment", task["target_id"], subtitle_path=rel)
    set_task_state(store, tid, "done", result_path=rel, result_sha256=sha256_file(srt))
    return Outcome("done", note or rel)
