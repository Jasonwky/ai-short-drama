# -*- coding: utf-8 -*-
"""云机克隆脚本:本机只测参数检查(torch / qwen_tts 只在云机上有)。"""
import json

from tools.serve import tts_remote


def test_missing_reference_audio_exits_2_before_importing_torch(capsys, tmp_path):
    job = {"lines": [["林小雨", "好。"]], "voices": {"林小雨": {"audio": str(tmp_path / "nope.wav"), "text": "试听"}},
           "seed": 1, "gap": 0.4, "lead": 0.3, "tail": 0.5, "language": "Chinese"}
    assert tts_remote.main([str(tmp_path), json.dumps(job, ensure_ascii=False)]) == 2
    assert "nope.wav" in capsys.readouterr().err


def test_layout_puts_each_line_at_its_shot_but_never_overlaps():
    assert tts_remote.layout([1.0, 1.0, 1.0], [0.0, 0.0, 4.0], lead=0.3, gap=0.4) == [0.3, 1.7, 4.0]
    assert tts_remote.layout([2.0, 1.0], [0.0, 1.0], lead=0.3, gap=0.4) == [0.3, 2.7]      # 上一句没说完就顺延
    assert tts_remote.layout([1.0, 1.0], None, lead=0.3, gap=0.4) == [0.3, 1.7]            # 旧任务没有 starts:连着排
