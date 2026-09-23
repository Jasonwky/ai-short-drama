# -*- coding: utf-8 -*-
"""闸门 0-4 的内容核对:转写生成片的台词,和要求的台词逐字比。

0-4 首跑发现 H3 会把参考音频的**内容**搬进新片(段 B 开头复读了段 A 的「这块玉佩」),
所以光看声纹相似度不够 —— 必须同时确认说的是不是该说的话。

模型:openai/whisper-large-v3-turbo(MIT,已读 openai/whisper 的 LICENSE 原文)。
在云机上跑,借用 ComfyUI venv 的现成包、不装任何东西、强制 CPU:
    python asr_check.py <whisper 目录> <片子.mp4>=<要求的台词> ...
"""
import difflib
import os
import subprocess
import sys

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

import numpy as np  # noqa: E402
from scipy.io import wavfile  # noqa: E402
from transformers import pipeline  # noqa: E402

PUNCT = "，。！？、,.!?\"'“”「」 "


def load(p):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", p, "-vn", "-ac", "1", "-ar", "16000",
                    "-sample_fmt", "s16", "/tmp/_asr.wav"], check=True)
    return wavfile.read("/tmp/_asr.wav")[1].astype(np.float32) / 32768


def main():
    asr = pipeline("automatic-speech-recognition", model=sys.argv[1], device="cpu")
    for arg in sys.argv[2:]:
        f, want = arg.split("=", 1)
        r = asr({"raw": load(f), "sampling_rate": 16000}, return_timestamps=True,
                generate_kwargs={"language": "zh", "task": "transcribe"})
        got = r["text"].strip()
        clean = lambda s: "".join(ch for ch in s if ch not in PUNCT)
        ratio = difflib.SequenceMatcher(None, clean(want), clean(got)).ratio()
        print(f"=== {os.path.basename(f)}  字符相似度 {ratio:.2f}")
        print(f"  要求: {want}\n  识别: {got}")
        for c in r.get("chunks", []):
            print(f"     {c['timestamp']} {c['text']}")


if __name__ == "__main__":
    main()
