# -*- coding: utf-8 -*-
"""配乐:增益包络(纯函数)+ 真 ffmpeg 混音实测(几百毫秒)。"""
import math
import subprocess

import pytest

from tools.cut import media, music
from tools.cut.subtitles import Cue


def cue(**over):
    return {"start_sec": 2.0, "end_sec": 12.0, "gain_db": -14.0, "duck_db": -10.0, "fade_in": 1.0, "fade_out": 1.0,
            **over}


def db(x):
    return 20 * math.log10(x)


def test_envelope_is_zero_outside_the_cue_and_fades_in_and_out():
    c = cue()
    assert music.gain_at(c, [], 1.99) == 0 and music.gain_at(c, [], 12.0) == 0 and music.gain_at(c, [], 13) == 0
    assert music.gain_at(c, [], 2.0) == 0                                  # 淡入从 0 起
    assert music.gain_at(c, [], 2.5) == pytest.approx(10 ** (-14 / 20) * 0.5)
    assert db(music.gain_at(c, [], 7.0)) == pytest.approx(-14)
    assert music.gain_at(c, [], 11.5) == pytest.approx(10 ** (-14 / 20) * 0.5)


def test_speech_ducks_with_ramps_before_and_after():
    c = cue()
    speech = [(5.0, 7.0)]
    assert db(music.gain_at(c, speech, 4.6)) == pytest.approx(-14)            # 过渡之前
    assert db(music.gain_at(c, speech, 4.85)) == pytest.approx(-19)            # 句首前 0.3 秒线性(dB)压下去
    assert db(music.gain_at(c, speech, 5.0)) == pytest.approx(-24)
    assert db(music.gain_at(c, speech, 6.0)) == pytest.approx(-24)
    assert db(music.gain_at(c, speech, 7.15)) == pytest.approx(-19)            # 句尾后 0.3 秒回来
    assert db(music.gain_at(c, speech, 7.4)) == pytest.approx(-14)


def test_short_gaps_between_lines_stay_ducked():
    """两句间隔不足 2×0.3 秒:连成一段,不在句间来回抽。"""
    assert music.merge_speech([(5.0, 6.0), (6.5, 7.0), (9.0, 9.5)]) == [(5.0, 7.0), (9.0, 9.5)]
    assert db(music.gain_at(cue(), [(5.0, 6.0), (6.5, 7.0)], 6.25)) == pytest.approx(-24)


def test_breakpoints_reproduce_the_envelope_and_stay_inside_the_cue():
    c = cue(start_sec=5.5, end_sec=9.0)
    speech = [(5.0, 6.0), (8.9, 10.0)]
    pts = music.breakpoints(c, speech)
    assert pts[0][0] == 5.5 and pts[-1][0] == 9.0 and all(5.5 <= t <= 9.0 for t, _ in pts)
    for (t0, g0), (t1, g1) in zip(pts, pts[1:]):                          # 拐点之间线性 = 逐点算的
        mid = (t0 + t1) / 2
        assert g0 + (g1 - g0) / 2 == pytest.approx(music.db_at(c, speech, mid))


def test_speech_intervals_follow_segment_offsets():
    parts = [([Cue("甲", 0.2, 0.9)], 0.0), ([], 1.0), ([Cue("乙", 0.1, 0.5), Cue("丙", 0.6, 0.8)], 2.0)]
    assert music.speech_of(parts) == [(0.2, 0.9), (2.1, 2.5), (2.6, 2.8)]


def test_validate_reports_every_reason():
    music.validate(cue(), "自购曲库 #123,商用授权")
    with pytest.raises(ValueError) as e:
        music.validate(cue(start_sec=-1, end_sec=-2, gain_db=3, duck_db=2, fade_in=-1), "  ")
    msg = str(e.value)
    for why in ("★必填", "起点不能为负", "终点要晚于起点", "音量", "压低量", "淡入淡出"):
        assert why in msg
    with pytest.raises(ValueError, match="淡入淡出"):
        music.validate(cue(start_sec=0, end_sec=1, fade_in=0.6, fade_out=0.6), "x")


# ── 真 ffmpeg ──────────────────────────────────────────────

def _silence(path, secs):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=64x64:r=24",
                    "-f", "lavfi", "-i", "anullsrc=channel_layout=mono:sample_rate=48000", "-t", str(secs),
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-c:a", "pcm_s16le", str(path)], check=True)


def _tone(path, secs):
    """恒定电平的 1 kHz 立体声正弦(峰值 0.5),当音乐用:混进去后的电平 = 它本身 × 包络。"""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000",
                    "-af", "volume=4", "-t", str(secs), "-ac", "2", "-c:a", "pcm_s16le", str(path)], check=True)


def test_mixed_level_follows_the_envelope(tmp_path):
    """无声视频(PCM 音轨,避开 AAC 噪声)混入一条 cue:每个时刻测到的电平 = 包络(含压低、淡入淡出、cue 外为 0)。"""
    _silence(tmp_path / "clip.mkv", 6)
    _tone(tmp_path / "song.wav", 10)
    c = cue(start_sec=1.0, end_sec=5.0, fade_in=0.5, fade_out=0.5)
    speech = [(2.5, 3.0)]
    m = media.Music(tmp_path / "song.wav", 1.0, 5.0, music.filter_expr(c, speech))
    out = tmp_path / "mix.wav"
    media.mix_audio([tmp_path / "clip.mkv"], [m], out, codec=["-c:a", "pcm_s16le"])
    env = media.rms_envelope(out, step=0.05)
    full = media.rms_envelope(tmp_path / "song.wav", step=0.05)[10]           # 音乐本身的电平(同一种量法)
    for t in (0.5, 1.6, 2.0, 2.4, 2.7, 3.5, 4.6, 5.5):
        want = music.gain_at(c, speech, t + 0.025) * full
        got = env[round(t / 0.05)]
        assert got == pytest.approx(want, rel=0.12, abs=full * 0.003), t


def test_plateau_is_the_full_volume_part_without_fades_and_lines():
    c = cue(start_sec=2.0, end_sec=12.0, fade_in=1.0, fade_out=1.0)
    assert music.plateau(c, []) == [(3.0, 11.0)]
    assert music.plateau(c, [(5.0, 6.0), (6.2, 7.0), (10.9, 13.0)]) == [(3.0, 4.7), (7.3, 10.6)]   # 句前后过渡也扣掉
    assert music.plateau(c, [(1.0, 12.5)]) == []


def test_outside_is_what_no_cue_touches_with_a_margin():
    a, b = cue(start_sec=2.0, end_sec=5.0), cue(start_sec=5.1, end_sec=8.0)
    assert music.outside([a, b], 10.0) == [(0.0, 1.8), (8.2, 10.0)]          # 两条之间不足 0.3 秒的缝不量
    assert music.outside([cue(start_sec=0.0, end_sec=10.0)], 10.0) == []


def test_stage_refuses_files_without_an_audio_track(tmp_path):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=64x64", "-t", "1",
                    str(tmp_path / "v.mp4")], check=True)
    with pytest.raises(ValueError, match="读不出音轨"):
        music.stage(tmp_path / "root", (tmp_path / "v.mp4").read_bytes(), "song.m4a")
    with pytest.raises(ValueError, match="只收"):
        music.stage(tmp_path / "root", b"x", "song.exe")
    assert not list((tmp_path / "root" / "assets" / "music").glob("*"))        # 不合格的不留文件
    _tone(tmp_path / "s.wav", 2)
    rel = music.stage(tmp_path / "root", (tmp_path / "s.wav").read_bytes(), "我的歌.WAV")
    assert rel.startswith("assets/music/") and rel.endswith(".wav") and len(rel) == len("assets/music/") + 16 + 4
