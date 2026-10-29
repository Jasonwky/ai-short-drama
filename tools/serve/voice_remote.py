# -*- coding: utf-8 -*-
"""**在云机上跑**:Qwen3-TTS VoiceDesign 按声音描述念一句。源码经 SSH 的 stdin 送过去,用 qwen-tts 独立 venv 执行:

    <venv python> - <模型目录> <声音描述> <台词> <种子> <语言>

stdout 最后一行是 JSON:`{"wav_b64": ..., "sr": 24000, "sec": 2.5, "gen_sec": 4.1}`(wav 不大,几秒 = 几百 KB,
直接走 stdout 回本机,不另开 scp)。失败时退出码非 0、stderr 写原因:2 = 模型目录不在;3 = 显存不够(和 ComfyUI 同卡)。

接口按云机上装的 qwen-tts 0.1.1 源码核对过:`Qwen3TTSModel.from_pretrained` / `generate_voice_design(text, instruct, language)`
(spike-results/2026-10-29-台词与口型匹配方案调研.md 第 4.2 节)。
本模块**顶层只用标准库**:torch / qwen_tts 在 `main()` 里才导入,本机单测只测参数检查。
"""
from __future__ import annotations

import base64
import io
import json
import sys
import time
from pathlib import Path


def main(argv: list[str]) -> int:
    model_dir, instruct, text, seed, language = argv
    if not Path(model_dir).is_dir():
        print(f"不存在:{model_dir}", file=sys.stderr)
        return 2
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    t0 = time.time()
    try:
        model = Qwen3TTSModel.from_pretrained(model_dir, device_map="cuda:0", dtype=torch.bfloat16)
        torch.manual_seed(int(seed))
        wavs, sr = model.generate_voice_design(text=text, instruct=instruct, language=language)
    except torch.cuda.OutOfMemoryError as e:
        print(f"显存不够(ComfyUI 可能正占着卡):{e}", file=sys.stderr)
        return 3
    buf = io.BytesIO()
    sf.write(buf, wavs[0], sr, format="WAV")
    print(json.dumps({"wav_b64": base64.b64encode(buf.getvalue()).decode(), "sr": sr,
                      "sec": round(len(wavs[0]) / sr, 2), "gen_sec": round(time.time() - t0, 1)}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
