# -*- coding: utf-8 -*-
"""韵律量法:用合成的正弦扫频当「人声」,量值可以手算。"""
import numpy as np

from tools.cut import prosody

SR = 16000


def _tone(f0_curve, sec, gaps=()):
    t = np.arange(int(SR * sec)) / SR
    f = np.interp(t, np.linspace(0, sec, len(f0_curve)), f0_curve)
    x = 0.3 * np.sin(2 * np.pi * np.cumsum(f) / SR)
    for a, b in gaps:
        x[int(a * SR):int(b * SR)] = 0
    return x.astype(np.float32)


def test_occupancy_counts_voiced_time():
    assert prosody.occupancy(_tone([150], 2.0), SR) > 0.95
    assert 0.45 < prosody.occupancy(_tone([150], 2.0, gaps=[(1.0, 2.0)]), SR) < 0.55


def test_pitch_range_in_semitones():
    assert prosody.pitch_range_st(_tone([150], 2.0), SR) < 1.0
    assert 8.0 < prosody.pitch_range_st(_tone([110, 220, 110, 220], 2.0), SR) < 13.0     # 110→220 = 12 半音


def test_template_problems_use_the_measured_bars():
    assert prosody.template_problems(_tone([150], 2.0), SR)                              # 平:起伏不够
    assert prosody.template_problems(_tone([75, 330, 80, 340, 78, 320], 3.0), SR) == []
