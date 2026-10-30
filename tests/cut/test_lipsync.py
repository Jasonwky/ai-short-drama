# -*- coding: utf-8 -*-
"""口型检查的判定(纯函数)与缓存。量法本身(mediapipe)在真片上标定,见 spike-results/2026-10-29 第 4 节。"""
import math

from tools.cut import lipsync

FPS = 24.0


def _env(n):
    """一段起伏的音量:每 6 帧一个音节。"""
    return [0.05 + 0.04 * math.sin(2 * math.pi * t / 6) for t in range(n)]


def test_mouth_following_the_voice_passes_and_a_still_mouth_fails():
    env = _env(72)
    talking = [[(0.4, 0.02 + 0.5 * e)] for e in env]                  # 一张脸,嘴随声音开合
    still = [[(0.4, 0.01)] for _ in env]                               # 嘴一动不动
    [ok] = lipsync.judge(FPS, talking, env, [[0.5, 2.5]])
    [bad] = lipsync.judge(FPS, still, env, [[0.5, 2.5]])
    assert (ok.verdict, ok.lag) == ("过", 0) and ok.r > 0.9
    assert bad.verdict == "不过" and bad.r == 0.0 and "相关只有" in bad.why


def test_the_speaker_is_whichever_face_moves_with_the_line():
    """两人同框:听的人嘴不动,说的人嘴随声动 —— 取相关最高的那条轨迹。"""
    env = _env(72)
    rows = [[(0.3, 0.01), (0.7, 0.02 + 0.5 * e)] for e in env]
    [line] = lipsync.judge(FPS, rows, env, [[0.5, 2.5]])
    assert line.verdict == "过"


def test_short_lines_and_lines_without_faces_cannot_be_judged():
    env = _env(72)
    rows = [[] for _ in env]
    short, faceless = lipsync.judge(FPS, rows, env, [[0.2, 0.8], [1.0, 2.8]])
    assert (short.verdict, faceless.verdict) == ("无法判定", "无法判定")
    assert "太短" in short.why and "没有正脸" in faceless.why


def test_results_are_cached_per_video_and_spans(tmp_path, monkeypatch):
    calls = []
    env = _env(72)

    def fake_measure(path):
        calls.append(path)
        return FPS, [[(0.4, 0.02 + 0.5 * e)] for e in env], env
    monkeypatch.setattr(lipsync, "measure", fake_measure)
    (tmp_path / "v.mp4").write_bytes(b"video-1")
    a = lipsync.check(tmp_path, "v.mp4", [[0.5, 2.5]])
    b = lipsync.check(tmp_path, "v.mp4", [[0.5, 2.5]])
    assert a == b and len(calls) == 1 and lipsync.cached(tmp_path, "v.mp4", [[0.5, 2.5]]) == a
    assert lipsync.cached(tmp_path, "v.mp4", [[0.4, 2.5]]) is None           # 台词时段变了不认
    (tmp_path / "v.mp4").write_bytes(b"video-2")
    assert lipsync.cached(tmp_path, "v.mp4", [[0.5, 2.5]]) is None           # 换了视频不认
