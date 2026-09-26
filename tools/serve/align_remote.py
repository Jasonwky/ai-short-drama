# -*- coding: utf-8 -*-
"""**在云机上跑**的逐字时间戳脚本:源码经 SSH 的 stdin 送过去,用 ComfyUI 的 venv 执行(不在云机上装任何东西)。

    <venv python> - <whisper 目录> <视频绝对路径>

stdout 最后一行是 JSON:`{"chars": [[字, 起, 止], ...], "asr_sec": 秒}`。失败时退出码非 0、stderr 写原因:
2 = 模型目录 / 视频不在;3 = 显存不够(和 ComfyUI 同卡)。

2026-09-26 云机实测(`spike-results/2026-09-26-字幕对齐云机实测.md`):
- `pipeline(return_timestamps="word")` 对中文退化成整句一块 —— 用 `generate(return_token_timestamps=True)`;
- transformers 5 返回 dict,取 `out["sequences"]` / `out["token_timestamps"]`;
- 一个汉字可能被拆成几个字节级 token,单独解码是 `�` —— 攒到能解出完整字再算一组(`merge_tokens`);
- GPU fp16 每条 8 秒片 5–6 s(CPU 要 290 s,不用)。

本模块**顶层只用标准库**:torch / transformers 在 `main()` 里才导入,本机单测只测 `merge_tokens`。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

BAD = "�"


def merge_tokens(ids: list[int], times: list[float], decode) -> list[tuple[str, float, float]]:
    """token 序列 → 逐字 (字, 起, 止)。第 k 个 token 的时间段是「上一个 token 的时刻 → 它的时刻」;
    解不出完整字的 token 与后面的攒成一组,组内每个字共用这一组的时间段。空白、特殊 token 丢掉。"""
    out: list[tuple[str, float, float]] = []
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


def _audio(video: str):
    import numpy as np
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", video, "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                       capture_output=True, check=True)
    return np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768


def main(argv: list[str]) -> int:
    model_dir, video = argv
    for p in (model_dir, video):
        if not Path(p).exists():
            print(f"不存在:{p}", file=sys.stderr)
            return 2
    import torch
    from transformers import WhisperForConditionalGeneration, WhisperProcessor

    t0 = time.time()
    try:
        proc = WhisperProcessor.from_pretrained(model_dir)
        model = WhisperForConditionalGeneration.from_pretrained(model_dir, dtype=torch.float16).to("cuda")
        feats = proc(_audio(video), sampling_rate=16000, return_tensors="pt").input_features.to("cuda", model.dtype)
        out = model.generate(feats, language="zh", task="transcribe", return_token_timestamps=True,
                             return_dict_in_generate=True)
    except torch.cuda.OutOfMemoryError as e:
        print(f"显存不够(ComfyUI 可能正占着卡):{e}", file=sys.stderr)
        return 3
    ids = [int(x) for x in out["sequences"][0]]
    times = [float(x) for x in out["token_timestamps"][0]]
    chars = merge_tokens(ids, times, lambda t: proc.tokenizer.decode(t, skip_special_tokens=True))
    print(json.dumps({"chars": chars, "asr_sec": round(time.time() - t0, 1)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
