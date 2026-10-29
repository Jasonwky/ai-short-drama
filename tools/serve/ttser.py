# -*- coding: utf-8 -*-
"""执行一条台词音频任务(kind = tts):参考音频传云机 → Qwen3-TTS Base 逐句克隆、拼接 → wav 回本机 → 传回云机让 whisper 转写 →
**本机**按拼音逐句判;有句子没念对就换种子重合成,最多 `attempts` 次(spec 2026-10-30-台词配音与口型闸门-design.md 第 4 节)。

与 `voicer` 同一套结局:连不上 → pending;ComfyUI 队列有任务 → pending 不打断;脚本报错 → failed;
次数用完仍有句子不过 → failed,写明哪句、命中多少,`tts.json` 留下每次尝试听到了什么(人可以查是识别错了还是真念错了)。
第 k 次尝试的种子 = 任务种子 + 1000·k(脚本里逐句再加句序号),全部记进 `tts.json`,可复现。写回只填空位。
"""
from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path

from tools.cut import speech
from tools.drama import repo
from tools.drama.db import Store
from tools.drama.tasks import set_task_state
from tools.gen import preflight
from tools.gen.capability import QWEN3_TTS, WHISPER_ALIGN
from tools.gen.comfy_client import ComfyError, ComfyUnreachable
from tools.serve import aligner
from tools.serve.dispatch import Outcome, sha256_file
from tools.serve.hosts import Host

SCRIPT = Path(__file__).with_name("tts_remote.py")
HIT_OK = 0.9            # 每句拼音命中率(与试听音同一条线)
SEED_STEP = 1000        # 换种子:与脚本内逐句 +k 错开
CHECK = "tts.json"


def run(store: Store, client, host: Host, task: sqlite3.Row, *, data_root: Path,
        run_tts: preflight.Runner, run_align: preflight.Runner) -> Outcome:
    tid, root, p = task["id"], Path(data_root), json.loads(task["params_json"])

    def fail(msg: str) -> Outcome:
        set_task_state(store, tid, "failed", error=msg)
        return Outcome("failed", msg)

    def upload(path: Path) -> str:
        return f"{WHISPER_ALIGN.input_dir}/" + client.upload_image(path, subfolder=f"ai-short-drama/{sha256_file(path)[:16]}")

    missing = [v["path"] for v in p["voices"].values() if not (root / v["path"]).is_file()]
    if missing:
        return fail(f"定稿音色文件不在本机:{', '.join(missing)}")
    try:
        wait = aligner.gpu_ready(client, QWEN3_TTS.vram_gb, "合成台词")
        if wait is None:
            refs = {who: upload(root / v["path"]) for who, v in p["voices"].items()}
    except ComfyUnreachable as e:
        return Outcome("pending", f"连不上云机,下轮再试:{e}")
    except ComfyError as e:
        return fail(f"定稿音色传不上云机:{e}")
    if wait is not None:
        return fail(wait.message) if wait.state == "failed" else wait

    out_dir = root / "outputs" / "tasks" / str(tid)
    out_dir.mkdir(parents=True, exist_ok=True)
    wav = out_dir / "lines.wav"
    texts = [line for _, line in p["lines"]]
    attempts = []
    for k in range(p["attempts"]):
        seed = task["seed"] + SEED_STEP * k
        job = {"lines": p["lines"], "voices": {who: {"audio": refs[who], "text": v["ref_text"]} for who, v in p["voices"].items()},
               "seed": seed, "gap": p["gap_sec"], "lead": p["lead_sec"], "tail": p["tail_sec"], "language": p["language"]}
        code, out = run_tts([QWEN3_TTS.base_dir, json.dumps(job, ensure_ascii=False)], SCRIPT.read_bytes())
        if code == aligner.SSH_UNREACHABLE:
            return Outcome("pending", f"ssh 连不上云机,下轮再试:{out.strip()[-300:]}")
        if code != 0:
            return fail(f"云机台词合成脚本失败(退出码 {code}):{out.strip()[-500:]}")
        rows = [line for line in out.splitlines() if line.startswith('{"wav_b64"')]
        if not rows:
            return fail(f"云机台词合成脚本没有输出结果:{out.strip()[-500:]}")
        got = json.loads(rows[-1])
        wav.write_bytes(base64.b64decode(got["wav_b64"]))
        try:
            remote = upload(wav)
        except ComfyUnreachable as e:
            return Outcome("pending", f"连不上云机,下轮再试:{e}")
        except ComfyError as e:
            return fail(f"台词音频传不上云机:{e}")
        code, out = run_align([WHISPER_ALIGN.model_dir, remote], aligner.SCRIPT.read_bytes())
        if code == aligner.SSH_UNREACHABLE:
            return Outcome("pending", f"ssh 连不上云机,下轮再试:{out.strip()[-300:]}")
        if code != 0:
            return fail(f"云机转写脚本失败(退出码 {code}):{out.strip()[-500:]}")
        try:
            heard = "".join(c for c, _, _ in aligner.parse_output(out))
        except ValueError as e:
            return fail(str(e))
        hits = speech.line_hits(texts, heard)
        attempts.append({"seed": seed, "heard": heard, "hits": hits, "sec": got["sec"], "spans": got["spans"],
                         "gen_sec": got["gen_sec"]})
        if all(h >= HIT_OK for h in hits):
            break
    last = attempts[-1]
    ok = all(h >= HIT_OK for h in last["hits"])
    (out_dir / CHECK).write_text(json.dumps(
        {"lines": p["lines"], "ok": ok, "attempt": len(attempts), "sec": last["sec"], "spans": last["spans"],
         "hits": last["hits"], "heard": last["heard"], "attempts": attempts}, ensure_ascii=False, indent=1), encoding="utf-8")
    if not ok:
        bad = ";".join(f"第 {n} 句「{texts[n - 1]}」命中 {h:.2f}" for n, h in enumerate(last["hits"], 1) if h < HIT_OK)
        return fail(f"换了 {len(attempts)} 个种子,回听仍没念对:{bad}(每次听到了什么见 outputs/tasks/{tid}/{CHECK})")

    rel = wav.relative_to(root).as_posix()
    set_task_state(store, tid, "queued", external_id=f"tts-{tid}", host_id=host.id)
    note = ""
    if repo.get(store, "segment", task["target_id"])["line_audio_path"]:
        note = "line_audio_path 已有台词音频,新版本留在任务记录里待人挑选"
    else:
        repo.update(store, "segment", task["target_id"], line_audio_path=rel)
    set_task_state(store, tid, "done", result_path=rel, result_sha256=sha256_file(wav))
    return Outcome("done", note or rel)


def check_of(data_root: Path, path: str | None) -> dict | None:
    """这条台词音频的回听结果(与 wav 同目录的 tts.json);没有返回 None。"""
    if not path:
        return None
    f = (Path(data_root) / path).with_name(CHECK)
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None
