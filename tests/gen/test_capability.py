# -*- coding: utf-8 -*-
"""能力声明里的配方参数(spec 2026-10-31-IndexTTS2港片配音-design.md §2)。"""

def test_index_tts2_recipe_is_declared_once():
    from tools.gen.capability import INDEX_TTS2
    assert INDEX_TTS2.python.endswith("/indextts/venv/bin/python")
    assert INDEX_TTS2.ckpt_dir.endswith("/indextts/checkpoints")
    assert (INDEX_TTS2.emo_alpha, INDEX_TTS2.pitch_widen, INDEX_TTS2.squeeze_gap_sec) == (1.0, 1.4, 0.06)
    assert INDEX_TTS2.gap_sec == 0.15 and INDEX_TTS2.sr_out == 24000
