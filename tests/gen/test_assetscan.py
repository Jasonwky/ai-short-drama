# -*- coding: utf-8 -*-
"""资产图代码检查(spike-results/2026-10-28-资产图白底检查标定.md):ffmpeg 现画几张图来判。"""
import subprocess

import pytest

from tools.gen import assetscan


def _png(path, bg, boxes, size=(512, 896)):
    """bg 底色上画几个深色块:boxes = [(x, y, w, h)]。"""
    draw = ",".join(f"drawbox=x={x}:y={y}:w={w}:h={h}:color=0x333333:t=fill" for x, y, w, h in boxes) or "null"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={bg}:s={size[0]}x{size[1]}",
                    "-vf", draw, "-frames:v", "1", str(path)], check=True)
    return path


@pytest.mark.parametrize("bg, boxes, ok", [
    ("0xF4F4F4", [(156, 100, 200, 700)], True),           # 浅灰影棚底、全身在画内
    ("0xAA2222", [(156, 100, 200, 700)], False),          # 红布底(银针那种)
    ("0xF4F4F4", [(0, 0, 512, 896)], False),              # 主体占满、顶到四边(半身裁切那种)
])
def test_front_image_light_border(tmp_path, bg, boxes, ok):
    (c,) = assetscan.check(_png(tmp_path / "a.png", bg, boxes), "character", "image")
    assert c.ok is ok and "四边浅色像素占" in c.detail


def test_fourview_needs_someone_in_every_quarter(tmp_path):
    four = [(30 + k * 416, 100, 100, 700) for k in range(4)]
    checks = assetscan.check(_png(tmp_path / "f.png", "0xF4F4F4", four, (1664, 928)), "character", "fourview")
    assert [c.ok for c in checks] == [True, True]
    checks = assetscan.check(_png(tmp_path / "g.png", "0xF4F4F4", four[:3], (1664, 928)), "character", "fourview")
    assert checks[1].ok is False and checks[1].name == "四格都有人"


def test_scenes_are_not_checked(tmp_path):
    assert assetscan.check(_png(tmp_path / "s.png", "0x225522", []), "scene", "image") == []
