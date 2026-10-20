# -*- coding: utf-8 -*-
"""视频面板的白屏提示:只读缓存(设计 specs/2026-10-20-配乐白屏与替换神情-design.md §二)。"""
from tools.cut import blankscan


def _video(root, rel="videos/s1.mp4", body=b"not-really-mp4"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body)


def test_videos_panel_shows_cached_blank_screen(client, runtime, store, sample, monkeypatch):
    _video(runtime.data_root)
    page = client.get(f"/episode/{sample['episode']}/videos").text
    assert "data-blank-screen" not in page                                 # 没查过:不在渲染时现算
    monkeypatch.setattr(blankscan, "detect", lambda path: [blankscan.Span(2.33, 6.21)])
    blankscan.check(runtime.data_root, "videos/s1.mp4")
    monkeypatch.setattr(blankscan, "detect", lambda path: (_ for _ in ()).throw(AssertionError("渲染时现算了")))
    page = client.get(f"/episode/{sample['episode']}/videos").text
    assert "data-blank-screen" in page and "段落 1 第 2.3–6.2 秒画面" in page


def test_videos_panel_ignores_cache_of_a_replaced_video(client, runtime, store, sample, monkeypatch):
    _video(runtime.data_root)
    monkeypatch.setattr(blankscan, "detect", lambda path: [blankscan.Span(2.33, 6.21)])
    blankscan.check(runtime.data_root, "videos/s1.mp4")
    _video(runtime.data_root, body=b"another-video")
    assert "data-blank-screen" not in client.get(f"/episode/{sample['episode']}/videos").text
