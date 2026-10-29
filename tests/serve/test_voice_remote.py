# -*- coding: utf-8 -*-
"""云机配音脚本:本机只测参数检查(torch / qwen_tts 只在云机上有)。"""
from tools.serve import voice_remote


def test_missing_model_dir_exits_2_before_importing_torch(capsys, tmp_path):
    assert voice_remote.main([str(tmp_path / "nope"), "清亮女声", "台词", "7", "Chinese"]) == 2
    assert "不存在" in capsys.readouterr().err
