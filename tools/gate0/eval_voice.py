# -*- coding: utf-8 -*-
"""闸门 0-4 的机械判据:两两计算说话人声纹余弦相似度。

对应 CLAUDE.md M-5「能确定性测的绝不交给人眼(耳)判断」—— 音色是否一致,
光靠听会受台词内容、情绪、背景声干扰,要有一个可复现的数。

模型:microsoft/wavlm-base-plus-sv(WavLMForXVector,16 kHz)。
🔴 许可证:模型卡指向 UniSpeech 仓库的 LICENSE,原文是 **CC BY-SA 3.0**(ShareAlike)。
   这里只作**内部评测**,不分发、不改权重。**不得打包进产品**;
   若要把音色相似度做成控制台里的质量门,必须换许可宽松的说话人模型。

在云机上跑(借用 ComfyUI venv 里现成的 torch/transformers/scipy,不装任何东西,强制 CPU):
    CUDA_VISIBLE_DEVICES= venv/bin/python eval_voice.py <模型目录> a.mp4 b.mp4 ...
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")  # 别和正在出片的 H3 抢显卡

import numpy as np  # noqa: E402
import torch  # noqa: E402
from scipy.io import wavfile  # noqa: E402
from transformers import AutoFeatureExtractor, WavLMForXVector  # noqa: E402

SR = 16000


def load_mono16k(path: str) -> np.ndarray:
    """任意音视频 → 16 kHz 单声道 float32。用 ffmpeg 解码,不依赖 torchaudio 的后端。"""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as t:
        tmp = t.name
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vn", "-ac", "1",
                        "-ar", str(SR), "-sample_fmt", "s16", tmp], check=True)
        sr, data = wavfile.read(tmp)
    finally:
        os.unlink(tmp)
    assert sr == SR, sr
    return data.astype(np.float32) / 32768.0


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    model_dir, files = sys.argv[1], sys.argv[2:]
    fe = AutoFeatureExtractor.from_pretrained(model_dir)
    model = WavLMForXVector.from_pretrained(model_dir).eval()

    embs = []
    for f in files:
        wav = load_mono16k(f)
        inputs = fe(wav, sampling_rate=SR, return_tensors="pt", padding=True)
        with torch.no_grad():
            e = model(**inputs).embeddings
        embs.append(torch.nn.functional.normalize(e, dim=-1)[0])
        print(f"  {os.path.basename(f):28s} 时长 {len(wav) / SR:5.2f}s")

    names = [os.path.basename(f).split("_")[0] for f in files]
    m = torch.stack(embs) @ torch.stack(embs).T
    print("\n余弦相似度矩阵:")
    print(" " * 12 + "".join(f"{n:>10s}" for n in names))
    for i, n in enumerate(names):
        print(f"{n:>12s}" + "".join(f"{m[i, j].item():10.3f}" for j in range(len(names))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
