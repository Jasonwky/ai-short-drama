# -*- coding: utf-8 -*-
"""云机 IndexTTS2 脚本:本机只测参数检查与纯文本处理(torch / indextts / pyworld 只在云机上有)。"""
import json

from tools.serve import idx_remote

RECIPE = {"emo_alpha": 1.0, "pitch_widen": 1.4, "squeeze_gap_sec": 0.06, "squeeze_db": -38.0}


def test_top_level_imports_only_stdlib():
    head = open(idx_remote.__file__, encoding="utf-8").read().split("def main")[0]
    for mod in ("torch", "numpy", "pyworld", "soundfile", "indextts"):
        assert f"import {mod}" not in head and f"from {mod}" not in head


def test_missing_files_exit_2_before_importing_torch(capsys, tmp_path):
    job = {"lines": [["燕无咎", "这不是我的身子,这是哪儿?"]], "voices": {"燕无咎": {"audio": str(tmp_path / "v.wav")}},
           "templates": [{"audio": str(tmp_path / "t.wav")}], "seed": 1, "gap": 0.15, "lead": 0.3, "tail": 0.5,
           "recipe": RECIPE, "sr_out": 24000}
    assert idx_remote.main([str(tmp_path), json.dumps(job, ensure_ascii=False)]) == 2
    assert "v.wav" in capsys.readouterr().err


def test_strip_inner_pauses_keeps_sentence_ends():
    assert idx_remote.strip_inner_pauses("和尚听着,我是东厂首领、你们;快走:杀!") == "和尚听着我是东厂首领你们快走杀!"
    assert idx_remote.strip_inner_pauses("这不是我的身子，这是哪儿？") == "这不是我的身子这是哪儿？"
