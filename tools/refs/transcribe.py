# -*- coding: utf-8 -*-
"""参考库转写(spec 1.1 第 4 条):worker 机器空闲时调一次 `run_one`,取一条 transcribe 任务,在云机 GPU 上识别整集台词。

- 音频本机转 16 kHz PCM,base64 嵌进脚本末尾经 ssh stdin 送过去 —— **云机上不落盘**,没有要删的副本(设计「转写后删云机副本」由此满足);
- 显存与共用:沿用 `aligner.gpu_ready`(队列里有别人的任务就等;显存不够让 ComfyUI 卸载);ssh 连不上 / 显存被抢 → 任务留 waiting 下轮再试,
  连续 `config.TRANSCRIBE_RETRIES` 次就判失败(人点「重新转写」再排);开跑后的意外异常 → 任务失败、留痕、再抛;
- 结果存 ref_content(kind='transcript',到期时间继承录屏行),全文进指纹;几乎没字(< 20 个汉字)判「不收」(纯配乐或识别失败)。
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from tools.gen.capability import WHISPER_ALIGN
from tools.gen.comfy_client import ComfyUnreachable
from tools.refs import clock, config, fingerprint, jobs, rjournal
from tools.refs.avcheck import pcm16k
from tools.refs.sources.base import hanzi
from tools.serve.aligner import CUDA_OOM, SSH_UNREACHABLE, gpu_ready

SCRIPT = Path(__file__).with_name("asr_remote.py")
MIN_HANZI = 20


def payload(pcm: bytes) -> bytes:
    tail = f'\n_AUDIO_B64 = "{base64.b64encode(pcm).decode("ascii")}"\nsys.exit(main(sys.argv[1], _AUDIO_B64))\n'
    return SCRIPT.read_bytes() + tail.encode("ascii")


def parse(out: str) -> dict:
    rows = [l for l in out.splitlines() if l.startswith('{"text"')]
    if not rows:
        raise ValueError(f"云机转写脚本没有输出结果:{out.strip()[-400:]}")
    return json.loads(rows[-1])


def run_one(refs, root: Path, client, run) -> str | None:
    """跑一条转写;没有就返回 None。返回给 worker 日志的一行。"""
    job = jobs.next_waiting(refs, clock.today(), kinds=("transcribe",))
    if job is None:
        return None
    rec = refs.conn.execute("SELECT * FROM ref_content WHERE id = ?", (jobs.params(job)["recording_id"],)).fetchone()
    if rec is None or rec["purged_at"] or not (Path(root) / rec["path"]).is_file():
        jobs.start(refs, job["id"]); jobs.finish(refs, job["id"], error="录屏文件不在了(到期清理或被删),转写作废")
        return f"参考库转写 {job['id']}:录屏不在,作废"
    try:
        wait = gpu_ready(client, WHISPER_ALIGN.vram_gb, "参考库转写")
    except ComfyUnreachable as e:
        return f"参考库转写 {job['id']}:连不上云机,下轮再试({e})"
    if wait is not None:
        if wait.state == "failed":
            jobs.start(refs, job["id"]); jobs.finish(refs, job["id"], error=wait.message)
        return f"参考库转写 {job['id']}:{wait.message}"
    jobs.start(refs, job["id"])
    try:
        return _run(refs, Path(root), job, rec, run)
    except Exception as e:
        if jobs.get(refs, job["id"])["state"] == "running":       # 已经落了结论的(成功 / 失败)不改
            msg = f"意外异常:{type(e).__name__}: {e}"
            jobs.finish(refs, job["id"], error=msg)
            rjournal.append(root, "job", source=job["source"], work_id=job["work_id"], job=job["id"], kind="transcribe", error=msg)
        raise


def _run(refs, root: Path, job: dict, rec, run) -> str:
    try:
        pcm = pcm16k(Path(root) / rec["path"])
    except RuntimeError as e:
        jobs.finish(refs, job["id"], error=str(e))
        return f"参考库转写 {job['id']}:失败"
    code, out = run([WHISPER_ALIGN.model_dir], payload(pcm))
    if code == SSH_UNREACHABLE or (code != 0 and CUDA_OOM in out):
        if job["attempts"] + 1 >= config.TRANSCRIBE_RETRIES:
            jobs.finish(refs, job["id"], error=f"连续 {config.TRANSCRIBE_RETRIES} 次 ssh 连不上 / 显存被抢,先停下 —— 云机空出来后点「重新转写」")
            return f"参考库转写 {job['id']}:失败(连续 {config.TRANSCRIBE_RETRIES} 次连不上 / 显存被抢)"
        jobs.retry(refs, job["id"], "ssh 连不上 / 显存被抢,下个空档再跑")
        return f"参考库转写 {job['id']}:下个空档再跑"
    if code != 0:
        jobs.finish(refs, job["id"], error=f"云机转写脚本失败(退出码 {code}):{out.strip()[-400:]}")
        return f"参考库转写 {job['id']}:失败"
    try:
        r = parse(out)
    except ValueError as e:
        jobs.finish(refs, job["id"], error=str(e))
        return f"参考库转写 {job['id']}:失败"
    n = hanzi(r["text"])
    ok = n >= MIN_HANZI
    rel = Path(rec["path"]).with_suffix(".transcript.json").as_posix()
    body = json.dumps({"text": r["text"], "chars": r["chars"]}, ensure_ascii=False)
    (Path(root) / rel).write_bytes(body.encode("utf-8"))
    with refs.write() as c:
        cid = c.execute("INSERT INTO ref_content (work_id, kind, unit, title, path, sha256, origin, verdict, reason, fetched_at, due_at, hard_due_at, meta_json)"
                        " VALUES (?, 'transcript', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (rec["work_id"], rec["unit"], f"第 {rec['unit']} 集转写", rel, hashlib.sha256(body.encode("utf-8")).hexdigest(),
                         f"whisper 转写录屏 #{rec['id']}", "ok" if ok else "rejected", "" if ok else f"几乎没有台词({n} 个汉字)",
                         clock.now_iso(), rec["due_at"], rec["hard_due_at"],
                         json.dumps({"chunks": r["chunks"], "asr_sec": r["asr_sec"]}))).lastrowid
    if ok:
        fingerprint.add(refs, cid, r["text"])
    jobs.finish(refs, job["id"])
    rjournal.append(root, "transcribe", source=job["source"], work_id=rec["work_id"], job=job["id"], chars=len(r["chars"]),
                    cloud="音频经 ssh stdin 送达,云机不落盘")
    return f"参考库转写 {job['id']}:{n} 个汉字"
