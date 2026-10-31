# -*- coding: utf-8 -*-
"""导出调色(spec 2026-10-31-港片画风-design.md §2):真 ffmpeg 跑,样片由 fakes.sample_mp4 现生成。"""
import pytest

from tools.cut import grade, media
from tools.serve.fakes import sample_mp4


@pytest.fixture
def clip(tmp_path):
    p = tmp_path / "src.mp4"
    p.write_bytes(sample_mp4(seconds=2))
    return p


def test_unknown_grade_fails_loudly():
    with pytest.raises(KeyError, match="未知调色"):
        grade.filters("sepia")


def test_hk90_keeps_duration_size_and_audio(tmp_path, clip):
    out = tmp_path / "g.mp4"
    grade.apply(clip, out, "hk90")
    a, b = media.probe(clip), media.probe(out)
    assert (b.width, b.height) == (a.width, a.height) and abs(b.duration - a.duration) < 0.1 and b.has_audio


def test_hk90_lowers_saturation_and_lifts_blacks(tmp_path, clip):
    out = tmp_path / "g.mp4"
    grade.apply(clip, out, "hk90")
    before, after = grade.measure(clip), grade.measure(out)
    assert after["sat"] < before["sat"] and after["ylow"] > before["ylow"]
