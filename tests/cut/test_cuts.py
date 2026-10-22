# -*- coding: utf-8 -*-
"""切镜计数:真 ffmpeg 跑现造的小视频(lavfi,每条几百毫秒)。"""
import shutil
import subprocess

import pytest

from tools.cut import cuts

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="本机没有 ffmpeg:切镜计数靠它解帧,跳过")

SIZE = "96x160"


def _clip(path, layers: list[str]):
    args = ["ffmpeg", "-v", "error", "-y"]
    for src in layers:
        args += ["-f", "lavfi", "-i", src]
    n = len(layers)
    graph = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0,format=yuv420p[v]"
    subprocess.run(args + ["-filter_complex", graph, "-map", "[v]", "-c:v", "libx264", "-qp", "0", str(path)],
                   check=True)
    return path


def test_hard_cut_between_two_shots_is_one_cut(tmp_path):
    clip = _clip(tmp_path / "v.mp4", [f"color=0x202020:size={SIZE}:rate=10:d=1",
                                      f"color=0xD0D0D0:size={SIZE}:rate=10:d=1"])
    (c,) = cuts.detect(clip)
    assert c.frame == 10 and abs(c.sec - 1.0) < 0.01 and c.diff > 100


def test_slow_change_is_not_a_cut(tmp_path):
    # 实战里快动作(转身)相邻帧差 20–30,连续多帧;渐变一样,每帧差很小 —— 都不算切镜
    clip = _clip(tmp_path / "v.mp4", [f"color=black:size={SIZE}:rate=10:d=2,fade=in:0:20"])
    assert cuts.detect(clip) == []


def test_flash_spanning_two_frames_counts_once(tmp_path):
    # 实战 task 69 第 131–132 帧:连续两帧都过线,是同一次切换
    clip = _clip(tmp_path / "v.mp4", [f"color=0x202020:size={SIZE}:rate=10:d=1",
                                      f"color=0x808080:size={SIZE}:rate=10:d=0.1",
                                      f"color=0xF0F0F0:size={SIZE}:rate=10:d=1"])
    assert len(cuts.detect(clip)) == 1


def test_check_caches_by_sha256_and_writes_thumbnails(tmp_path):
    clip = _clip(tmp_path / "v.mp4", [f"color=0x202020:size={SIZE}:rate=10:d=1",
                                      f"color=0xD0D0D0:size={SIZE}:rate=10:d=1"])
    rep = cuts.check(tmp_path, "v.mp4")
    assert [c.frame for c in rep.cuts] == [10]
    for rel in (rep.first_png, rep.last_png, *rep.cut_pngs):
        assert (tmp_path / rel).is_file()
    assert (tmp_path / "v.mp4.cuts.json").is_file()
    assert cuts.cached(tmp_path, "v.mp4") == rep
    clip.write_bytes(clip.read_bytes() + b"\0")          # 换了视频:缓存作废
    assert cuts.cached(tmp_path, "v.mp4") is None
