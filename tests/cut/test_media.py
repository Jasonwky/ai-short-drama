# -*- coding: utf-8 -*-
"""真 ffmpeg 跑(本机 4.4)。每条几百毫秒。"""
import subprocess

import pytest

from tools.cut import media


def _ramp(path, n=10):
    """n 帧 64x64,第 i 帧亮度 = 20·i(无损编码)—— 取到哪一帧,看亮度就知道。"""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=black:s=64x64:r=10:d={n / 10}",
                    "-vf", "geq=lum='N*20':cb=128:cr=128", "-c:v", "libx264", "-qp", "0", str(path)], check=True)


def test_extract_last_frame_really_takes_the_last_one(tmp_path):
    _ramp(tmp_path / "ramp.mp4")
    assert media.frame_count(tmp_path / "ramp.mp4") == 10
    media.extract_last_frame(tmp_path / "ramp.mp4", tmp_path / "out" / "last.png")
    px = media.gray_frame(tmp_path / "out" / "last.png", 0, 64, 64)
    # 第 10 帧亮度 180(视频是有限范围),转成全范围 PNG 约 191;第 9 帧(160)会是约 168
    assert abs(sum(px) / len(px) - 191) < 3


def test_encode_strips_old_metadata_and_tags_keep_stream_hash(tmp_path, mp4_bytes):
    src = tmp_path / "comfy.mp4"
    (tmp_path / "raw.mp4").write_bytes(mp4_bytes)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp_path / "raw.mp4"), "-c", "copy",
                    "-movflags", "use_metadata_tags", "-metadata", 'prompt={"3": {"seed": 1}}', str(src)], check=True)
    assert "prompt" in media.format_tags(src)                 # 模拟 ComfyUI:工作流嵌在元数据里

    out = tmp_path / "ep.mp4"
    media.encode_episode([src, src], out, None)
    p = media.probe(out)
    assert p.has_audio and (p.width, p.height) == (384, 672) and abs(p.duration - 2.0) < 0.1
    assert "prompt" not in media.format_tags(out)

    h = media.stream_sha256(out)
    media.write_tags(out, tmp_path / "tagged.mp4", {"AIGC": '{"AIGC":{}}', "comment": "Powered by MiniMax H3"})
    tags = media.format_tags(tmp_path / "tagged.mp4")
    assert tags["AIGC"] == '{"AIGC":{}}' and tags["comment"] == "Powered by MiniMax H3"
    assert media.stream_sha256(tmp_path / "tagged.mp4") == h     # 只换封装,视频流一个字节不变


def test_label_is_burned_in_the_top_left_corner(tmp_path, mp4_bytes):
    (tmp_path / "a.mp4").write_bytes(mp4_bytes)
    media.encode_episode([tmp_path / "a.mp4"], tmp_path / "plain.mp4", None)
    lab = media.Label.for_size(384, 672)
    media.encode_episode([tmp_path / "a.mp4"], tmp_path / "lab.mp4", lab)
    a = media.gray_frame(tmp_path / "lab.mp4", 0, 384, 672)
    b = media.gray_frame(tmp_path / "plain.mp4", 0, 384, 672)
    x0, y0, x1, y1 = media.diff_bbox(a, b, 384)
    assert x1 < 192 and y1 < 168
    assert (y1 - y0 + 1) - 2 * lab.border >= 0.05 * 384


def test_label_size_follows_the_short_side():
    assert media.Label.for_size(768, 1344) == media.Label.for_size(1344, 768) == media.Label(58, 3, 31)


def test_diff_bbox_ignores_isolated_noise():
    a = bytes(36)
    b = bytearray(36)
    for i in (7, 8, 9, 13, 14, 15, 19, 20, 21):   # 6 宽:(1..3, 1..3) 一块 3x3
        b[i] = 200
    assert media.diff_bbox(a, bytes(b), 6) == (1, 1, 3, 3)
    b[35] = 200                                    # 孤立噪点 (5, 5)
    assert media.diff_bbox(a, bytes(b), 6) == (1, 1, 3, 3)
    assert media.diff_bbox(a, a, 6) is None


def test_missing_ffmpeg_is_a_readable_refusal(monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="找不到 ffmpeg / ffprobe"):
        media.require_ffmpeg()


def test_ffmpeg_failure_is_not_swallowed(tmp_path):
    with pytest.raises(media.CutError, match="ffprobe 失败"):
        media.probe(tmp_path / "nope.mp4")
