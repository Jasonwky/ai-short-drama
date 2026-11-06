# -*- coding: utf-8 -*-
"""**在云机上跑**:Qwen3-TTS Base 按定稿音色逐句克隆台词,拼成一条。源码经 SSH 的 stdin 送过去,用 qwen-tts 独立 venv 执行:

    <venv python> - <Base 模型目录> <任务 JSON>

任务 JSON:`{"lines": [[说话人, 台词]], "voices": {说话人: {"audio": 云机绝对路径, "text": 参考文本}},
"seed": 种子, "gap": 句间停顿秒, "lead": 开头留白秒, "tail": 结尾留白秒, "language": "Chinese", "starts": [每句镜头起点秒] | null}`。
starts(台词按镜头对时,spec 2026-10-31-IndexTTS2港片配音-design.md §10):每句不早于它镜头的起点;null / 缺 = 连着排。
第 k 句用种子 seed + k(同一份任务 JSON 逐字节复现)。
stdout 最后一行是 JSON:`{"wav_b64", "sr", "sec", "spans": [[起, 止], ...], "gen_sec"}`(spans 是每句在音频里的秒数)。
失败时退出码非 0、stderr 写原因:2 = 模型目录 / 参考音频不在;3 = 显存不够。

接口按云机上装的 qwen-tts 0.1.1 源码核对过:`create_voice_clone_prompt(ref_audio, ref_text)` /
`generate_voice_clone(text, language, voice_clone_prompt)`(spike-results/2026-10-29 第 4.2 节,克隆实测念对)。
本模块**顶层只用标准库**:torch / qwen_tts / numpy 在 `main()` 里才导入。
"""
from __future__ import annotations

import base64
import io
import json
import sys
import time
from pathlib import Path


def layout(lens: list[float], starts: list[float] | None, *, lead: float, gap: float) -> list[float]:
    """每句的起点:不早于它镜头的起点,也不早于上一句结束 + 句间停顿;第一句不早于开头留白。
    与 idx_remote.layout 同一算法(两个脚本各自经 stdin 送上云机、只用标准库,不能互相导入)。纯函数,本机单测。"""
    out, t = [], lead
    for k, n in enumerate(lens):
        at = max(t, starts[k]) if starts else t
        out.append(round(at, 3))
        t = at + n + gap
    return out


def main(argv: list[str]) -> int:
    model_dir, job_json = argv
    job = json.loads(job_json)
    for p in [model_dir, *(v["audio"] for v in job["voices"].values())]:
        if not Path(p).exists():
            print(f"不存在:{p}", file=sys.stderr)
            return 2
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    t0 = time.time()
    try:
        model = Qwen3TTSModel.from_pretrained(model_dir, device_map="cuda:0", dtype=torch.bfloat16)
        prompts = {who: model.create_voice_clone_prompt(ref_audio=v["audio"], ref_text=v["text"])
                   for who, v in job["voices"].items()}
        clips, sr = [], None
        for k, (who, line) in enumerate(job["lines"]):
            torch.manual_seed(int(job["seed"]) + k)
            wavs, sr = model.generate_voice_clone(text=line, language=job["language"], voice_clone_prompt=prompts[who])
            clips.append(np.asarray(wavs[0], dtype=np.float32))
    except torch.cuda.OutOfMemoryError as e:
        print(f"显存不够(ComfyUI 可能正占着卡):{e}", file=sys.stderr)
        return 3
    at = layout([len(c) / sr for c in clips], job.get("starts"), lead=job["lead"], gap=job["gap"])
    parts, spans, n = [], [], 0
    for a, clip in zip(at, clips):
        pad = int(round(a * sr)) - n
        parts += [np.zeros(pad, dtype=np.float32), clip]
        n += pad + len(clip)
        spans.append([a, round(a + len(clip) / sr, 3)])
    parts.append(np.zeros(int(round(job["tail"] * sr)), dtype=np.float32))
    audio = np.concatenate(parts)
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="WAV")
    print(json.dumps({"wav_b64": base64.b64encode(buf.getvalue()).decode(), "sr": sr, "sec": round(len(audio) / sr, 3),
                      "spans": spans, "gen_sec": round(time.time() - t0, 1)}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
