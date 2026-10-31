# -*- coding: utf-8 -*-
"""执行一条试听音任务(kind = voice):云机上 Qwen3-TTS 念一句 → wav 回本机 → 传回云机让 whisper 转写 → **本机**按拼音判。

与字幕对齐同一套结局(`aligner`):连不上 → pending;ComfyUI 队列有任务 → pending 不打断;脚本报错 → failed;
成功一次走完 `queued → done`(合成 + 回听约一分钟内同步跑完,不进 ComfyUI 队列)。
**没念对不算任务失败**:试听音照样落盘、照样能挑,`voice.json` 记下听到了什么;定稿时它算不通过项、要写理由
(spec 2026-10-30-台词配音与口型闸门-design.md 第 3 节)。写回只填空位(与 collect 一致)。
"""
from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path

from tools.cut import prosody, speech
from tools.drama import repo
from tools.drama.db import Store
from tools.drama.tasks import set_task_state
from tools.gen import preflight
from tools.gen.capability import INDEX_TTS2, QWEN3_TTS, WHISPER_ALIGN
from tools.gen.comfy_client import ComfyError, ComfyUnreachable
from tools.serve import aligner
from tools.serve.dispatch import Outcome, sha256_file
from tools.serve.hosts import Host

SCRIPT = Path(__file__).with_name("voice_remote.py")
SCRIPT_IDX = Path(__file__).with_name("idx_remote.py")      # 念法模板候选(IndexTTS2)
HIT_OK = 0.9            # 拼音命中率:实测念对的 10 条按拼音全是 1.0(spike-results/2026-10-29 第 4.2 节)
CHECK = "voice.json"
PROSODY = "prosody.json"


def run(store: Store, client, host: Host, task: sqlite3.Row, *, data_root: Path,
        run_tts: preflight.Runner, run_align: preflight.Runner) -> Outcome:
    tid, root, p = task["id"], Path(data_root), json.loads(task["params_json"])

    def fail(msg: str) -> Outcome:
        set_task_state(store, tid, "failed", error=msg)
        return Outcome("failed", msg)

    if p.get("workflow") == "indextts2_template":
        return _template(store, client, host, task, root, run_tts, fail)

    try:
        wait = aligner.gpu_ready(client, QWEN3_TTS.vram_gb, "配音")
    except ComfyUnreachable as e:
        return Outcome("pending", f"连不上云机,下轮再试:{e}")
    if wait is not None:
        return fail(wait.message) if wait.state == "failed" else wait

    code, out = run_tts([QWEN3_TTS.design_dir, p["instruct"], p["text"], str(task["seed"]), p["language"]],
                        SCRIPT.read_bytes())
    if code == aligner.SSH_UNREACHABLE:
        return Outcome("pending", f"ssh 连不上云机,下轮再试:{out.strip()[-300:]}")
    if code != 0 and (busy := aligner.oom_wait(out, "合成试听音")):
        return busy
    if code != 0:
        return fail(f"云机配音脚本失败(退出码 {code}):{out.strip()[-500:]}")
    rows = [line for line in out.splitlines() if line.startswith('{"wav_b64"')]
    if not rows:
        return fail(f"云机配音脚本没有输出结果:{out.strip()[-500:]}")
    got = json.loads(rows[-1])
    out_dir = root / "outputs" / "tasks" / str(tid)
    out_dir.mkdir(parents=True, exist_ok=True)
    wav = out_dir / "voice.wav"
    wav.write_bytes(base64.b64decode(got["wav_b64"]))

    try:
        name = client.upload_image(wav, subfolder=f"ai-short-drama/{sha256_file(wav)[:16]}")
    except ComfyUnreachable as e:
        return Outcome("pending", f"连不上云机,下轮再试:{e}")
    except ComfyError as e:
        return fail(f"试听音传不上云机:{e}")
    code, out = run_align([WHISPER_ALIGN.model_dir, f"{WHISPER_ALIGN.input_dir}/{name}"], aligner.SCRIPT.read_bytes())
    if code == aligner.SSH_UNREACHABLE:
        return Outcome("pending", f"ssh 连不上云机,下轮再试:{out.strip()[-300:]}")
    if code != 0 and (busy := aligner.oom_wait(out, "回听试听音")):
        return busy
    if code != 0:
        return fail(f"云机转写脚本失败(退出码 {code}):{out.strip()[-500:]}")
    try:
        heard = "".join(c for c, _, _ in aligner.parse_output(out))
    except ValueError as e:
        return fail(str(e))
    hit = round(speech.hit(p["text"], heard), 3)
    (out_dir / CHECK).write_text(json.dumps(
        {"text": p["text"], "heard": heard, "hit": hit, "ok": hit >= HIT_OK, "sec": got["sec"],
         "gen_sec": got["gen_sec"]}, ensure_ascii=False, indent=1), encoding="utf-8")

    rel = wav.relative_to(root).as_posix()
    set_task_state(store, tid, "queued", external_id=f"voice-{tid}", host_id=host.id)
    note = ""
    if repo.get(store, "character", task["target_id"])["voice_ref_path"]:
        note = "voice_ref_path 已有试听音,新版本留在任务记录里待人挑选"
    else:
        repo.update(store, "character", task["target_id"], voice_ref_path=rel, voice_ref_text=p["text"])
    set_task_state(store, tid, "done", result_path=rel, result_sha256=sha256_file(wav))
    return Outcome("done", note or rel)


def _template(store: Store, client, host: Host, task: sqlite3.Row, root: Path, run_tts: preflight.Runner, fail) -> Outcome:
    """念法模板候选(spec 2026-10-31-IndexTTS2港片配音-design.md §3):IndexTTS2 用定稿音色 + 情绪向量念一句(去停顿、起伏放大),
    本机量韵律写 `prosody.json`;不回听(只当「怎么念」的参考)、不写回角色 —— 人挑了、过了门槛才存成模板。"""
    tid, p = task["id"], json.loads(task["params_json"])
    if not (root / p["voice"]["path"]).is_file():
        return fail(f"定稿音色文件不在本机:{p['voice']['path']}")
    try:
        wait = aligner.gpu_ready(client, INDEX_TTS2.vram_gb, "合成念法候选")
        if wait is None:
            ref = f"{WHISPER_ALIGN.input_dir}/" + client.upload_image(
                root / p["voice"]["path"], subfolder=f"ai-short-drama/{sha256_file(root / p['voice']['path'])[:16]}")
    except ComfyUnreachable as e:
        return Outcome("pending", f"连不上云机,下轮再试:{e}")
    except ComfyError as e:
        return fail(f"定稿音色传不上云机:{e}")
    if wait is not None:
        return fail(wait.message) if wait.state == "failed" else wait
    who = repo.get(store, "character", task["target_id"])["name"]
    job = {"lines": [[who, p["text"]]], "voices": {who: {"audio": ref}}, "emo_vector": p["emo_vector"], "seed": task["seed"],
           "gap": 0, "lead": 0, "tail": 0, "recipe": p["recipe"], "sr_out": p["sr_out"]}
    code, out = run_tts([INDEX_TTS2.ckpt_dir, json.dumps(job, ensure_ascii=False)], SCRIPT_IDX.read_bytes())
    if code == aligner.SSH_UNREACHABLE:
        return Outcome("pending", f"ssh 连不上云机,下轮再试:{out.strip()[-300:]}")
    if code != 0 and (busy := aligner.oom_wait(out, "合成念法候选")):
        return busy
    if code != 0:
        return fail(f"云机念法脚本失败(退出码 {code}):{out.strip()[-500:]}")
    rows = [line for line in out.splitlines() if line.startswith('{"wav_b64"')]
    if not rows:
        return fail(f"云机念法脚本没有输出结果:{out.strip()[-500:]}")
    out_dir = root / "outputs" / "tasks" / str(tid)
    out_dir.mkdir(parents=True, exist_ok=True)
    wav = out_dir / "template.wav"
    wav.write_bytes(base64.b64decode(json.loads(rows[-1])["wav_b64"]))
    x, sr = prosody.load_wav(wav)
    (out_dir / PROSODY).write_text(json.dumps(
        {"occupancy": round(prosody.occupancy(x, sr), 3), "range_st": round(prosody.pitch_range_st(x, sr), 1),
         "problems": prosody.template_problems(x, sr)}, ensure_ascii=False, indent=1), encoding="utf-8")
    rel = wav.relative_to(root).as_posix()
    set_task_state(store, tid, "queued", external_id=f"voice-{tid}", host_id=host.id)
    set_task_state(store, tid, "done", result_path=rel, result_sha256=sha256_file(wav))
    return Outcome("done", rel)


def prosody_of(data_root: Path, path: str | None) -> dict | None:
    """念法候选的韵律量值(与 wav 同目录的 prosody.json);没有返回 None。"""
    if not path:
        return None
    f = (Path(data_root) / path).with_name(PROSODY)
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None


def check_of(data_root: Path, path: str | None) -> dict | None:
    """这条试听音的回听结果(与 wav 同目录的 voice.json);没有(手工上传的)返回 None。"""
    if not path:
        return None
    f = (Path(data_root) / path).with_name(CHECK)
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None
