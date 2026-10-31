# -*- coding: utf-8 -*-
"""**在云机上跑**:IndexTTS2 按定稿音色逐句克隆、念法模板引导、去句内停顿、音高起伏放大,拼成一条。
源码经 SSH 的 stdin 送过去,用 indextts 独立 venv(借 ComfyUI 的 torch)执行:

    <venv python> - <checkpoints 目录> <任务 JSON>

任务 JSON(台词音频):`{"lines": [[说话人, 台词]], "voices": {说话人: {"audio": 云机路径}}, "templates": [{"audio": 云机路径}](逐句),
"seed", "gap", "lead", "tail", "recipe": {emo_alpha, pitch_widen, squeeze_gap_sec, squeeze_db}, "sr_out"}`。
念法模板候选:没有 `templates`、改给 `"emo_vector": [8 维]`(只一句)。
第 k 句用种子 seed + k(spec 2026-10-31-IndexTTS2港片配音-design.md §9 V4)。
stdout 最后一行是 JSON:`{"wav_b64", "sr", "sec", "spans": [[起, 止], ...], "gen_sec"}`(与 `tts_remote` 同格式)。
失败时退出码非 0、stderr 写原因:2 = 文件不在;3 = 显存不够(文本含 `CUDA out of memory`,ttser 认它退回排队)。

配方实测依据:spike-results/2026-10-31-降低AI感调研.md §12。两处兼容垫片(云机实测必需):
torchaudio 2.11 的 save 要 torchcodec → 换 soundfile 写;HF 下载走 hf-mirror。
本模块**顶层只用标准库**:numpy / torch / pyworld / soundfile / indextts 在 `main()` 里才导入,本机单测只测参数检查。
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
import time
from pathlib import Path

INNER_PAUSE = re.compile("[,\uff0c\u3001;\uff1b:\uff1a]")      # 半角 / 全角逗号、顿号、分号、冒号


def strip_inner_pauses(text: str) -> str:
    """去掉句内逗号 / 顿号 / 分号 / 冒号(句末的 。!? 保留),让模型一口气连着念。"""
    return INNER_PAUSE.sub("", text)


def main(argv: list[str]) -> int:
    ckpt_dir, job_json = argv
    job = json.loads(job_json)
    files = [ckpt_dir, *(v["audio"] for v in job["voices"].values()), *(t["audio"] for t in job.get("templates", []))]
    for p in files:
        if not Path(p).exists():
            print(f"不存在:{p}", file=sys.stderr)
            return 2
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_HOME", "/root/autodl-tmp/hf")
    repo_dir = str(Path(ckpt_dir).parent)
    sys.path.insert(0, repo_dir)
    os.chdir(repo_dir)
    import numpy as np
    import pyworld as pw
    import soundfile as sf
    import torch
    import torchaudio

    def _save(path, wav, sr, *a, **k):
        sf.write(str(path), wav.detach().cpu().float().numpy().T, sr)
    torchaudio.save = _save
    from indextts.infer_v2 import IndexTTS2

    r = job["recipe"]

    def squeeze(x, sr):
        """超过 squeeze_gap_sec 的静音只留 squeeze_gap_sec。"""
        fr = int(0.01 * sr)
        keep, run = [], 0
        for k in range(0, len(x), fr):
            seg = x[k:k + fr]
            run = run + 1 if 20 * np.log10(np.sqrt((seg ** 2).mean()) + 1e-9) < r["squeeze_db"] else 0
            if run * 0.01 <= r["squeeze_gap_sec"]:
                keep.append(seg)
        return np.concatenate(keep) if keep else x

    def widen(x, sr):
        """WORLD 分析 → 对数音高围绕中位数放大 pitch_widen 倍 → 合成(时长不变)。"""
        x = x.astype(np.float64)
        f0, t = pw.harvest(x, sr)
        sp, ap = pw.cheaptrick(x, f0, t, sr), pw.d4c(x, f0, t, sr)
        v = f0 > 0
        if v.sum() < 5:
            return x.astype(np.float32)
        med = np.median(np.log(f0[v]))
        f1 = f0.copy()
        f1[v] = np.exp(med + (np.log(f0[v]) - med) * r["pitch_widen"])
        return pw.synthesize(f1, sp, ap, sr).astype(np.float32)

    t0 = time.time()
    tmp = Path("/tmp/idx_remote")
    tmp.mkdir(exist_ok=True)
    templates = job.get("templates") or [None] * len(job["lines"])
    try:
        tts = IndexTTS2(cfg_path=f"{ckpt_dir}/config.yaml", model_dir=ckpt_dir, use_fp16=True)
        clips = []
        for k, ((who, line), tpl) in enumerate(zip(job["lines"], templates)):
            torch.manual_seed(int(job["seed"]) + k)
            out = tmp / f"{k}.wav"
            emo = ({"emo_audio_prompt": tpl["audio"], "emo_alpha": r["emo_alpha"]} if tpl
                   else {"emo_vector": job["emo_vector"]})
            tts.infer(spk_audio_prompt=job["voices"][who]["audio"], text=strip_inner_pauses(line), output_path=str(out),
                      interval_silence=0, **emo)
            x, sr = sf.read(str(out))
            x = (x.mean(1) if x.ndim > 1 else x).astype(np.float32)
            x = widen(squeeze(x, sr), sr)
            n = int(round(len(x) * job["sr_out"] / sr))                  # 统一重采样(线性插值,V5)
            clips.append(np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32))
    except torch.cuda.OutOfMemoryError as e:
        print(f"显存不够(ComfyUI 可能正占着卡):CUDA out of memory {e}", file=sys.stderr)
        return 3
    sr = job["sr_out"]
    silence = lambda sec: np.zeros(int(round(sec * sr)), dtype=np.float32)   # noqa: E731
    parts, spans, t = [silence(job["lead"])], [], job["lead"]
    for k, clip in enumerate(clips):
        if k:
            parts.append(silence(job["gap"]))
            t += job["gap"]
        parts.append(clip)
        spans.append([round(t, 3), round(t + len(clip) / sr, 3)])
        t += len(clip) / sr
    parts.append(silence(job["tail"]))
    audio = np.clip(np.concatenate(parts), -1, 1)
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
    print(json.dumps({"wav_b64": base64.b64encode(buf.getvalue()).decode(), "sr": sr, "sec": round(len(audio) / sr, 3),
                      "spans": spans, "gen_sec": round(time.time() - t0, 1)}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
