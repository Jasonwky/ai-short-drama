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


def test_rms_envelope_windows_the_audio(tmp_path, mp4_bytes):
    """样片是 1 秒 440 Hz 正弦:10 个 0.1 s 窗,每窗能量都差不多。"""
    (tmp_path / "a.mp4").write_bytes(mp4_bytes)
    env = media.rms_envelope(tmp_path / "a.mp4")
    assert len(env) == 10
    assert min(env[1:-1]) > 0.8 * max(env) > 0


def test_subtitles_are_burned_at_the_bottom(tmp_path, mp4_bytes):
    (tmp_path / "a.mp4").write_bytes(mp4_bytes)
    (tmp_path / "ep.srt").write_text("1\n00:00:01,100 --> 00:00:01,900\n这块玉佩，是我母亲留给我的。\n\n", encoding="utf-8")
    media.encode_episode([tmp_path / "a.mp4"] * 2, tmp_path / "plain.mp4", None)
    media.encode_episode([tmp_path / "a.mp4"] * 2, tmp_path / "sub.mp4", None, subtitles=tmp_path / "ep.srt")
    media.encode_episode([tmp_path / "a.mp4"] * 2, tmp_path / "both.mp4", media.Label.for_size(384, 672),
                         subtitles=tmp_path / "ep.srt")
    frame = lambda f, t: media.gray_frame(tmp_path / f, t, 384, 672)   # noqa: E731
    assert media.diff_bbox(frame("sub.mp4", 0.5), frame("plain.mp4", 0.5), 384) is None   # 第 1 段(字幕没开始)
    x0, y0, x1, y1 = media.diff_bbox(frame("sub.mp4", 1.5), frame("plain.mp4", 1.5), 384)
    assert y0 > 672 * 0.8                                                  # 在底部
    lab = media.diff_bbox(frame("both.mp4", 1.5), frame("sub.mp4", 1.5), 384)
    assert lab[3] < 168                                                    # 标识照样在左上角,字幕不挡它
