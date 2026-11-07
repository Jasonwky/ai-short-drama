# -*- coding: utf-8 -*-
"""**在云机上跑**:参考库录屏的整集转写。音频(16 kHz 单声道 s16le)由本机嵌在脚本末尾经 ssh stdin 送来,只在内存里用,云机不落盘。

    <venv python> - <whisper 目录>        # 脚本末尾由本机追加:_AUDIO_B64 = "..." 与 sys.exit(main(...))

stdout 最后一行 JSON:{"text": 全文, "chars": [[字, 起, 止], ...], "chunks": 块数, "asr_sec": 秒}。退出码 2 = 模型目录不在;3 = 显存不够。
whisper 用法与 serve/align_remote.py 相同(generate + return_token_timestamps;中文 token 攒成完整字)。
whisper 一次只认 30 秒 → 每 25 秒一块,时间加上块的偏移。

本模块**顶层只用标准库**:numpy / torch / transformers 在 `main()` 里才导入,本机单测只测纯函数。
"""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

BAD = "�"
CHUNK_SEC = 25


def merge_tokens(ids, times, decode):
    """token 序列 → 逐字 (字, 起, 止);解不出完整字的 token 与后面的攒成一组(同 align_remote.merge_tokens)。"""
    out = []
    k = 0
    while k < len(ids):
        j = k
        text = decode(ids[k:j + 1])
        while BAD in text and j + 1 < len(ids):
            j += 1
            text = decode(ids[k:j + 1])
        start = times[k - 1] if k > 0 else 0.0
        for c in text:
            if not c.isspace() and c != BAD:
                out.append((c, round(start, 2), round(times[j], 2)))
        k = j + 1
    return out


def chunks(n_samples: int, sr: int = 16000, sec: int = CHUNK_SEC) -> list[tuple[int, int]]:
    step = sr * sec
    return [(a, min(a + step, n_samples)) for a in range(0, n_samples, step)]


def main(model_dir: str, audio_b64: str) -> int:
    if not Path(model_dir).exists():
        print(f"不存在:{model_dir}", file=sys.stderr)
        return 2
    import numpy as np
    import torch
    from transformers import WhisperForConditionalGeneration, WhisperProcessor
    t0 = time.time()
    audio = np.frombuffer(base64.b64decode(audio_b64), dtype=np.int16).astype(np.float32) / 32768
    try:
        proc = WhisperProcessor.from_pretrained(model_dir)
        model = WhisperForConditionalGeneration.from_pretrained(model_dir, dtype=torch.float16).to("cuda")
        chars = []
        spans = chunks(len(audio))
        for a, b in spans:
            feats = proc(audio[a:b], sampling_rate=16000, return_tensors="pt").input_features.to("cuda", model.dtype)
            out = model.generate(feats, language="zh", task="transcribe", return_token_timestamps=True, return_dict_in_generate=True)
            ids = [int(x) for x in out["sequences"][0]]
            ts = [float(x) for x in out["token_timestamps"][0]]
            off = a / 16000
            chars += [(c, round(s + off, 2), round(e + off, 2))
                      for c, s, e in merge_tokens(ids, ts, lambda t: proc.tokenizer.decode(t, skip_special_tokens=True))]
    except torch.cuda.OutOfMemoryError as e:
        print(f"显存不够(ComfyUI 可能正占着卡):{e}", file=sys.stderr)
        return 3
    print(json.dumps({"text": "".join(c for c, _, _ in chars), "chars": chars, "chunks": len(spans),
                      "asr_sec": round(time.time() - t0, 1)}, ensure_ascii=False))
    return 0
