# -*- coding: utf-8 -*-
from tests.cut.conftest import done_task
from tools.cut import provenance, tailframe
from tools.drama import repo
from tools.serve.hosts import add_host


def _videos(store, s):
    return [{"segment_id": i, "number": n, "video_path": repo.get(store, "segment", i)["video_path"]}
            for n, i in ((1, s["seg1"]), (2, s["seg2"]))]


def test_chain_goes_back_from_video_to_assets(store, two_segments):
    s, root = two_segments, two_segments["root"]
    lin = done_task(store, root, s["seg1"], "image", "outputs/tasks/a/lin.png", b"lin")   # 充当角色四视图
    repo.update(store, "segment", s["seg1"], first_frame_path=None, video_path=None)
    done_task(store, root, s["seg1"], "image", "outputs/tasks/f9/frame.png", b"f9", refs=["outputs/tasks/a/lin.png"])
    done_task(store, root, s["seg1"], "video", "outputs/tasks/v9/clip.mp4", b"v9", refs=["outputs/tasks/f9/frame.png"])

    r = provenance.build(store, root, s["episode"], produce_id="ASD-1", producer="P1", videos=_videos(store, s))
    v = r["segments"][0]["video"]
    assert v["task"]["model"] == "m-video" and v["inputs"][0]["path"] == "outputs/tasks/f9/frame.png"
    assert v["inputs"][0]["inputs"][0]["task"]["id"] == lin
    assert r["models"] == ["m-image", "m-video"] and provenance.problems(r) == []
    md = provenance.to_markdown(r)
    assert "`ASD-1`" in md and "## 段落 2" in md and "Powered by MiniMax H3" in md


def test_tail_frame_links_segment_2_back_to_segment_1(store, two_segments):
    s, root = two_segments, two_segments["root"]
    repo.update(store, "segment", s["seg2"], first_frame_path=None, video_path=None)
    tailframe.confirm_tail(store, root, tailframe.plan_tail(store, root, s["seg1"]))
    done_task(store, root, s["seg2"], "video", "outputs/tasks/v2b/clip.mp4", b"v2b",
              refs=[repo.get(store, "segment", s["seg2"])["first_frame_path"]])
    r = provenance.build(store, root, s["episode"], produce_id="ASD-1", producer="P1", videos=_videos(store, s))
    frame = r["segments"][1]["video"]["inputs"][0]
    assert frame["task"]["model"] == "ffmpeg:last-frame" and frame["task"]["host"] == "本机"
    assert frame["inputs"][0]["path"] == "outputs/tasks/v1/clip.mp4"
    assert provenance.problems(r) == []


def test_replaced_file_and_untracked_asset_are_reported(store, two_segments):
    s, root = two_segments, two_segments["root"]
    (root / "outputs/tasks/f1/frame.png").write_bytes(b"swapped")
    repo.update(store, "segment", s["seg2"], video_path="manual/clip.mp4")
    (root / "manual").mkdir()
    (root / "manual/clip.mp4").write_bytes(b"x")
    r = provenance.build(store, root, s["episode"], produce_id="ASD-1", producer="P1", videos=_videos(store, s))
    probs = provenance.problems(r)
    assert any("frame.png 的内容与 task" in p and "被替换过" in p for p in probs)
    assert any("段落 2:视频 manual/clip.mp4 在 task 表里没有生成记录" in p for p in probs)
    assert "无生成记录(人工提供)" in provenance.to_markdown(r)


def test_host_label_says_where_it_ran(store, two_segments):
    """有登记的机器写机器名;本地抽帧写「本机」;有云端编号却没有机器的(导入的工程)照实写。"""
    s, root = two_segments, two_segments["root"]
    hid = add_host(store, label="4090", ssh_host="h", ssh_port=1, ssh_user="root", key_ref="k", local_port=8188,
                   vram_gb=24.0, models_dir="/m")
    store.conn.execute("UPDATE task SET host_id = ? WHERE result_path = 'outputs/tasks/v1/clip.mp4'", (hid,))
    repo.update(store, "segment", s["seg2"], first_frame_path=None, video_path=None)
    tailframe.confirm_tail(store, root, tailframe.plan_tail(store, root, s["seg1"]))
    done_task(store, root, s["seg2"], "video", "outputs/tasks/v2b/clip.mp4", b"v2b",
              refs=[repo.get(store, "segment", s["seg2"])["first_frame_path"]])
    r = provenance.build(store, root, s["episode"], produce_id="ASD-1", producer="P1", videos=_videos(store, s))
    v1, v2 = r["segments"][0]["video"], r["segments"][1]["video"]
    assert v1["task"]["host"] == "4090"
    assert v2["inputs"][0]["task"]["host"] == "本机"
    assert v2["task"]["host"] == "未登记的机器(导入的工程)"


def test_subtitles_chain_back_to_the_video_they_were_aligned_to(store, two_segments):
    s, root = two_segments, two_segments["root"]
    videos = [{"segment_id": s["seg"], "number": 1, "video_path": "outputs/tasks/v1/clip.mp4",
               "subtitle_path": "outputs/tasks/a1/subtitles.srt"}]
    report = provenance.build(store, root, s["episode"], produce_id="x", producer="p", videos=videos)
    sub = report["segments"][0]["subtitles"]
    assert sub["task"]["kind"] == "align" and sub["inputs"][0]["path"] == "outputs/tasks/v1/clip.mp4"
    assert "m-align" in report["models"] and "字幕" in provenance.to_markdown(report)
    (root / "outputs/tasks/a1/subtitles.srt").write_text("改过", encoding="utf-8")
    report = provenance.build(store, root, s["episode"], produce_id="x", producer="p", videos=videos)
    assert any("subtitles.srt" in p and "被替换过" in p for p in provenance.problems(report))


def test_node_is_public_for_the_trace_viewer(store, two_segments):
    """倒查页与导出共用同一棵树。"""
    s, root = two_segments, two_segments["root"]
    video = repo.get(store, "segment", s["seg1"])["video_path"]
    n = provenance.node(store, root, video)
    assert n["path"] == video and n["task"]["kind"] == "video" and n["sha256"] == n["task"]["result_sha256"]
    assert provenance.node(store, root, "assets/没有记录.png") == {"path": "assets/没有记录.png", "sha256": None,
                                                                   "task": None, "inputs": []}


def test_sha256_is_cached_until_the_file_changes(tmp_path, monkeypatch):
    import os
    calls = []
    real = provenance.sha256_file
    monkeypatch.setattr(provenance, "sha256_file", lambda p: calls.append(p) or real(p))
    f = tmp_path / "v.mp4"
    f.write_bytes(b"one")
    first = provenance.cached_sha256(f)
    assert provenance.cached_sha256(f) == first and len(calls) == 1          # 整集倒查十几段视频,不每次现算
    f.write_bytes(b"two")
    os.utime(f, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns + 10**9))   # 同样大小,修改时间变了
    assert provenance.cached_sha256(f) != first and len(calls) == 2
