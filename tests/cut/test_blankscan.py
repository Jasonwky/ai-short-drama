# -*- coding: utf-8 -*-
"""白屏检测:真 ffmpeg 跑现造的小视频(lavfi,每条几百毫秒)。"""
import json
import shutil
import subprocess

import pytest

from tools.cut import blankscan
from tools.cut.blankscan import detect as real_detect      # 收集时取到的是真的;conftest 每条测试前把它换成「没白屏」

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="本机没有 ffmpeg:白屏检测靠它抽亮度,跳过")

SIZE = "96x160"


@pytest.fixture(autouse=True)
def _real_detect(monkeypatch):
    monkeypatch.setattr(blankscan, "detect", real_detect)


def _clip(path, layers: list[str]):
    """layers = 依次拼接的 lavfi 源(每个都带 d=秒数),10 fps,无损编码。"""
    args = ["ffmpeg", "-v", "error", "-y"]
    for src in layers:
        args += ["-f", "lavfi", "-i", src]
    n = len(layers)
    graph = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0,format=yuv420p[v]"
    subprocess.run(args + ["-filter_complex", graph, "-map", "[v]", "-c:v", "libx264", "-qp", "0", str(path)],
                   check=True)
    return path


def _normal(d):
    return f"testsrc2=size={SIZE}:rate=10:d={d}"   # 自带一成近白像素(testsrc 两成,太白)


def _white(d):
    return f"color=white:size={SIZE}:rate=10:d={d}"


def test_white_flash_in_the_middle_is_found(tmp_path):
    clip = _clip(tmp_path / "v.mp4", [_normal(1), _white(1), _normal(1)])
    (span,) = real_detect(clip)
    assert abs(span.start - 1.0) < 0.15 and abs(span.end - 2.0) < 0.15


def test_white_background_behind_a_figure_is_found(tmp_path):
    # 实战第 2 段第三轮:不是整屏白,是人物站在白底前(四视图的白底带进了视频),白占六成
    fig = f"color=white:size={SIZE}:rate=10:d=1,drawbox=x=24:y=20:w=48:h=140:color=0x802020:t=fill"
    clip = _clip(tmp_path / "v.mp4", [_normal(1), fig, _normal(1)])
    (span,) = real_detect(clip)
    assert abs(span.start - 1.0) < 0.15 and abs(span.end - 2.0) < 0.15


def test_bright_frame_with_content_is_not_blank(tmp_path):
    # 白墙 / 白衣占一小块(实战最多 16%),其余是正常内容;整体调亮;满屏浅灰(亮但不白)
    wall = f"{_normal(2)},drawbox=x=0:y=0:w=96:h=24:color=white:t=fill"
    bright = f"{_normal(2)},eq=brightness=0.15"
    gray = f"color=0xC0C0C0:size={SIZE}:rate=10:d=2"
    for i, src in enumerate((wall, bright, gray, _normal(2))):
        assert real_detect(_clip(tmp_path / f"{i}.mp4", [src])) == [], src


def test_short_flash_is_not_blank(tmp_path):
    clip = _clip(tmp_path / "v.mp4", [_normal(1), _white(0.3), _normal(1)])
    assert real_detect(clip) == []


def test_check_caches_by_video_hash(tmp_path, monkeypatch):
    _clip(tmp_path / "v.mp4", [_normal(1), _white(1), _normal(0.5)])
    assert blankscan.cached(tmp_path, "v.mp4") is None                      # 没查过
    found = blankscan.check(tmp_path, "v.mp4")
    assert len(found) == 1 and blankscan.cached(tmp_path, "v.mp4") == found
    monkeypatch.setattr(blankscan, "detect", lambda path: pytest.fail("有缓存还重扫"))
    assert blankscan.check(tmp_path, "v.mp4") == found
    data = json.loads((tmp_path / "v.mp4.blankscan.json").read_text(encoding="utf-8"))
    assert data["params"] == blankscan.PARAMS                                # 改了阈值,旧缓存作废
    _clip(tmp_path / "v.mp4", [_normal(2)])                                 # 换了视频
    assert blankscan.cached(tmp_path, "v.mp4") is None


def test_problem_reads_like_a_person_wrote_it():
    msg = blankscan.problem(2, [blankscan.Span(2.33, 6.21)])
    assert msg.startswith("段落 2 第 2.3–6.2 秒画面") and "重出这段再导" in msg
