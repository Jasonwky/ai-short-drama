# -*- coding: utf-8 -*-
import json
import shutil
import subprocess

import pytest

from tools.refs import avcheck

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                                  reason="本机没有 ffmpeg / ffprobe:录屏检查与关键帧靠它,跳过")


def test_keyframe_times_head_then_every_ten_seconds():
    assert avcheck.keyframe_times(61.6) == [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
    assert avcheck.keyframe_times(2.2) == [0.0, 0.5, 1.0, 1.5, 2.0]
    assert avcheck.keyframe_times(3.007) == [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]      # 3.0 秒已过最后一帧(片长 3.007、30 fps)


def _make(path, video: str, audio: str, sec: int = 10):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"{video}:d={sec}", "-f", "lavfi", "-i", f"{audio}:d={sec}",
                    "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-c:a", "libopus", "-shortest", str(path)],
                   check=True)
    return path


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("本机没有 ffmpeg / ffprobe:录屏检查与关键帧靠它,跳过")
    d = tmp_path_factory.mktemp("av")
    return {
        "ok": _make(d / "ok.webm", "testsrc2=s=320x240", "sine=f=440"),
        "mute": _make(d / "mute.webm", "testsrc2=s=320x240", "anullsrc=r=48000:cl=stereo"),
        "black": _make(d / "black.webm", "color=c=black:s=320x240", "sine=f=440"),
    }


@needs_ffmpeg
def test_good_recording_passes(clips):
    problems, meta = avcheck.recording_problems(clips["ok"], 10.0)
    assert problems == [] and abs(meta["duration"] - 10.0) < 0.5 and meta["mean_volume_db"] > avcheck.SILENT_DB


@needs_ffmpeg
def test_wrong_duration_is_reported(clips):
    problems, _ = avcheck.recording_problems(clips["ok"], 20.0)
    assert len(problems) == 1 and problems[0].startswith("时长对不上")


@needs_ffmpeg
def test_silent_recording_is_reported(clips):
    problems, _ = avcheck.recording_problems(clips["mute"], 10.0)
    assert len(problems) == 1 and problems[0].startswith("没有声音")


@needs_ffmpeg
def test_black_recording_is_reported(clips):
    problems, _ = avcheck.recording_problems(clips["black"], 10.0)
    assert len(problems) == 1 and problems[0].startswith("画面不对")


def _size(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    s = json.loads(out)["streams"][0]
    return s["width"], s["height"]


@needs_ffmpeg
def test_remux_and_crop(clips, tmp_path):
    avcheck.remux_crop(clips["ok"], tmp_path / "copy.webm", None)
    assert _size(tmp_path / "copy.webm") == (320, 240) and abs(avcheck.duration(tmp_path / "copy.webm") - 10.0) < 0.5
    avcheck.remux_crop(clips["ok"], tmp_path / "crop.webm", (40, 20, 160, 200))
    assert _size(tmp_path / "crop.webm") == (160, 200)


@needs_ffmpeg
def test_extract_frame_and_pcm(clips, tmp_path):
    avcheck.extract_frame(clips["ok"], 2.5, tmp_path / "k.jpg")
    assert (tmp_path / "k.jpg").stat().st_size > 0
    pcm = avcheck.pcm16k(clips["ok"])
    assert abs(len(pcm) - 10 * 16000 * 2) <= 0.05 * 10 * 16000 * 2


@needs_ffmpeg
def test_missing_file_is_an_error(tmp_path):
    with pytest.raises(RuntimeError):
        avcheck.duration(tmp_path / "none.webm")
