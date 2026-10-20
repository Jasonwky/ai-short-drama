# -*- coding: utf-8 -*-
"""真 ffmpeg 真导出(两段 1 秒样片,几秒钟)。"""
import json
import sqlite3
import subprocess

import pytest

from tools.cut import export, media, music
from tools.drama import repo

PRODUCER = "91310000MA1234567X"


def test_export_writes_both_versions_report_and_passes_readback(store, two_segments):
    s, root = two_segments, two_segments["root"]
    eid = export.begin(store, root, s["episode"], f"  {PRODUCER} ")
    export.run(store, root, eid)
    row = export.get(store, eid)
    checks = json.loads(row["checks_json"])
    assert row["state"] == "done" and row["error"] is None, checks
    assert all(c["ok"] for c in checks) and len(checks) == 9
    assert export.saved_producer(store) == PRODUCER                       # 记住,下次预填

    tags = media.format_tags(root / row["labeled_path"])
    body = json.loads(tags["AIGC"])["AIGC"]
    assert body["ProduceID"] == row["produce_id"] and body["ContentProducer"] == PRODUCER
    assert body["ReservedCode1"] == media.stream_sha256(root / row["labeled_path"])
    plain = json.loads(media.format_tags(root / row["plain_path"])["AIGC"])["AIGC"]
    assert plain["ProduceID"] == row["produce_id"] and plain["ReservedCode1"] != body["ReservedCode1"]
    report = json.loads((root / row["report_path"]).read_text(encoding="utf-8"))
    assert report["produce_id"] == row["produce_id"]                      # 同一个编号
    assert (root / row["report_path"]).with_suffix(".md").is_file()
    assert not list((root / row["labeled_path"]).parent.glob("*.raw.mp4"))


def test_finished_export_is_locked(store, two_segments):
    s, root = two_segments, two_segments["root"]
    eid = export.begin(store, root, s["episode"], PRODUCER)
    export.run(store, root, eid)
    with pytest.raises(sqlite3.IntegrityError, match="导出已结束"):
        store.conn.execute("UPDATE export SET error = 'x' WHERE id = ?", (eid,))


def test_produce_id_cannot_change_even_while_running(store, two_segments):
    s, root = two_segments, two_segments["root"]
    eid = export.begin(store, root, s["episode"], PRODUCER)
    with pytest.raises(sqlite3.IntegrityError, match="身份字段不可变"):
        store.conn.execute("UPDATE export SET produce_id = 'y' WHERE id = ?", (eid,))


def test_begin_refuses_with_reasons_and_writes_nothing(store, two_segments):
    s, root = two_segments, two_segments["root"]
    with pytest.raises(ValueError, match="★必答"):
        export.begin(store, root, s["episode"], "")
    with pytest.raises(ValueError, match="不允许的字符"):
        export.begin(store, root, s["episode"], "某某有限公司")
    repo.update(store, "segment", s["seg2"], video_path=None)
    with pytest.raises(ValueError, match="段落 2 还没有视频"):
        export.begin(store, root, s["episode"], PRODUCER)
    assert export.list_for(store, s["episode"]) == [] and export.saved_producer(store) == ""


def test_begin_refuses_broken_provenance(store, two_segments):
    s, root = two_segments, two_segments["root"]
    (root / "outputs/tasks/f2/frame.png").write_bytes(b"swapped")
    with pytest.raises(ValueError, match="被替换过"):
        export.begin(store, root, s["episode"], PRODUCER)


def test_video_swapped_after_begin_fails_the_run(store, two_segments):
    s, root = two_segments, two_segments["root"]
    eid = export.begin(store, root, s["episode"], PRODUCER)
    (root / "outputs/tasks/v2/clip.mp4").write_bytes(b"other")
    with pytest.raises(media.CutError, match="被换掉了"):
        export.run(store, root, eid)


def test_checker_catches_a_missing_label(store, two_segments):
    """把无标识版当带标识版交给检查器:显式标识那项必须 FAIL。"""
    s, root = two_segments, two_segments["root"]
    eid = export.begin(store, root, s["episode"], PRODUCER)
    export.run(store, root, eid)
    row = export.get(store, eid)
    checks = export.check(store, root, row, row["plain_path"], row["plain_path"], row["report_path"],
                          media.Label.for_size(384, 672))
    bad = {c["name"]: c["detail"] for c in checks if not c["ok"]}
    assert list(bad) == ["显式标识(5.4)"] and "看不到标识" in bad["显式标识(5.4)"]


def test_checker_catches_leftover_metadata(store, two_segments):
    s, root = two_segments, two_segments["root"]
    eid = export.begin(store, root, s["episode"], PRODUCER)
    export.run(store, root, eid)
    row = export.get(store, eid)
    f = root / row["labeled_path"]
    tag = media.format_tags(f)["AIGC"]
    media.write_tags(f, root / "leaky.mp4", {"AIGC": tag, "prompt": "{workflow}"})
    checks = export.check(store, root, row, "leaky.mp4", row["plain_path"], row["report_path"],
                          media.Label.for_size(384, 672))
    bad = {c["name"]: c["detail"] for c in checks if not c["ok"]}
    assert "原有元数据已清空 · 带标识版" in bad and "prompt" in bad["原有元数据已清空 · 带标识版"]


def test_stale_running_export_is_failed_on_restart(store, two_segments):
    s, root = two_segments, two_segments["root"]
    eid = export.begin(store, root, s["episode"], PRODUCER)
    assert export.fail_stale(store) == 1
    row = export.get(store, eid)
    assert row["state"] == "failed" and "控制台重启" in row["error"]


def test_subtitles_are_burned_into_both_versions(store, two_segments, tmp_path):
    """段落 1 的字幕 0.2–0.9 s:两版成片在 0.5 s 处底部都有字,1.5 s(段落 2,没台词)处没有;整集 SRT 随成片交付。"""
    s, root = two_segments, two_segments["root"]
    eid = export.begin(store, root, s["episode"], PRODUCER)
    export.run(store, root, eid)
    row = export.get(store, eid)
    clips = [root / "outputs/tasks/v1/clip.mp4", root / "outputs/tasks/v2/clip.mp4"]
    media.encode_episode(clips, tmp_path / "nosub.mp4", None)
    frame = lambda f, t: media.gray_frame(f, t, 384, 672)   # noqa: E731
    box = media.diff_bbox(frame(root / row["plain_path"], 0.5), frame(tmp_path / "nosub.mp4", 0.5), 384)
    assert box is not None and box[1] > 672 * 0.8
    lab = media.diff_bbox(frame(root / row["labeled_path"], 0.5), frame(root / row["plain_path"], 0.5), 384)
    assert lab[3] < 168                      # 两版的差别只有左上角的标识:字幕两版都有
    assert media.diff_bbox(frame(root / row["plain_path"], 1.5), frame(tmp_path / "nosub.mp4", 1.5), 384) is None
    srt = (root / row["plain_path"]).with_name("subtitles.srt").read_text(encoding="utf-8")
    assert "00:00:00,200 --> 00:00:00,900" in srt
    assert {c["name"]: c["ok"] for c in json.loads(row["checks_json"])}["字幕"] is True


def test_dialogue_without_subtitles_blocks_export(store, two_segments):
    s, root = two_segments, two_segments["root"]
    repo.update(store, "segment", s["seg"], subtitle_path=None)
    with pytest.raises(ValueError, match="段落 1 有台词但还没有字幕"):
        export.begin(store, root, s["episode"], PRODUCER)


def test_subtitles_swapped_after_begin_fails_the_run(store, two_segments):
    s, root = two_segments, two_segments["root"]
    eid = export.begin(store, root, s["episode"], PRODUCER)
    (root / "outputs/tasks/a1/subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:00,500\n别的\n\n", encoding="utf-8")
    with pytest.raises(media.CutError, match="字幕"):
        export.run(store, root, eid)


def _reader_seeing(text):
    class R:
        def read(self, frame):
            return [([(0, 1100), (700, 1100), (700, 1150), (0, 1150)], text, 0.9)] if text else []

        def read_column(self, frame, box):
            return ""
    return R()


def test_burned_in_subtitle_blocks_export(store, two_segments, monkeypatch):
    # 实战第 6 段:H3 把台词画进了画面,导出再烧一遍就是两层字 —— 命中就不让导出(用户 2026-10-19 定)
    from tools.cut import textscan
    s, root = two_segments, two_segments["root"]
    monkeypatch.setattr(textscan, "make_reader", lambda: _reader_seeing("这块玉佩是我母亲留给我的"))
    eid = export.begin(store, root, s["episode"], PRODUCER)
    with pytest.raises(media.CutError, match="段落 1 的画面里疑似烧进了字幕"):
        export.run(store, root, eid)
    # 结果已缓存:面板上的清单当场列出,导出按钮不可点
    assert any("烧进了字幕" in p for p in export.readiness(store, root, s["episode"]))
    with pytest.raises(ValueError, match="烧进了字幕"):
        export.begin(store, root, s["episode"], PRODUCER)


def test_blank_screen_blocks_export(store, two_segments, monkeypatch):
    # 实战第 2 段第三轮:中段约 4 秒人物站在白底前 —— 命中就不让导出(用户 2026-10-20 定)
    from tools.cut import blankscan
    s, root = two_segments, two_segments["root"]
    monkeypatch.setattr(blankscan, "detect", lambda path: [blankscan.Span(0.2, 0.9)] if "v2" in str(path) else [])
    eid = export.begin(store, root, s["episode"], PRODUCER)
    with pytest.raises(media.CutError, match="段落 2 第 0.2–0.9 秒画面"):
        export.run(store, root, eid)
    # 结果已缓存:面板上的清单当场列出,导出按钮不可点
    assert any("段落 2 第 0.2–0.9 秒" in p for p in export.readiness(store, root, s["episode"]))
    with pytest.raises(ValueError, match="重出这段再导"):
        export.begin(store, root, s["episode"], PRODUCER)



def test_confirmed_blank_screen_passes_and_is_in_the_report(store, two_segments, monkeypatch):
    # 雪景之类本来就白的画面:人填理由放行,只对这条视频(sha256)有效(用户 2026-10-20 定)
    from tools.cut import blankscan
    s, root = two_segments, two_segments["root"]
    monkeypatch.setattr(blankscan, "detect", lambda path: [blankscan.Span(0.2, 0.9)] if "v2" in str(path) else [])
    with pytest.raises(ValueError, match="没查出白屏"):                      # 没查过 / 没命中:没什么可放行的
        blankscan.confirm(store, root, s["seg2"], "雪景,本来就白")
    blankscan.check(root, "outputs/tasks/v2/clip.mp4")
    with pytest.raises(ValueError, match="★必填"):
        blankscan.confirm(store, root, s["seg2"], "   ")
    blankscan.confirm(store, root, s["seg2"], " 雪景,本来就白 ")
    assert not any("画面近乎全白" in p for p in export.readiness(store, root, s["episode"]))
    eid = export.begin(store, root, s["episode"], PRODUCER)
    snap = {v["number"]: v["blank_override"] for v in json.loads(export.get(store, eid)["inputs_json"])}
    assert snap[1] is None and snap[2]["reason"] == "雪景,本来就白" and snap[2]["spans"] == [[0.2, 0.9]]
    export.run(store, root, eid)
    row = export.get(store, eid)
    assert row["state"] == "done", row["checks_json"]
    report = json.loads((root / row["report_path"]).read_text(encoding="utf-8"))
    assert report["segments"][1]["blank_override"]["reason"] == "雪景,本来就白"
    md = (root / row["report_path"]).with_suffix(".md").read_text(encoding="utf-8")
    assert f"人工确认放行:段落 2,第 0.2–0.9 秒,雪景,本来就白,{snap[2]['created_at']}" in md


def test_blank_override_expires_when_the_video_is_replaced(store, two_segments, monkeypatch, tmp_path):
    from tests.cut.conftest import done_task
    from tools.cut import blankscan
    s, root = two_segments, two_segments["root"]
    monkeypatch.setattr(blankscan, "detect", lambda path: [blankscan.Span(0.2, 0.9)] if "v2" in str(path) else [])
    blankscan.check(root, "outputs/tasks/v2/clip.mp4")
    blankscan.confirm(store, root, s["seg2"], "雪景,本来就白")
    other = tmp_path / "other.mp4"                                          # 重出了一版:内容不同的新视频
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(root / "outputs/tasks/v2/clip.mp4"), "-c", "copy",
                    "-metadata", "comment=reroll", str(other)], check=True)
    done_task(store, root, s["seg2"], "video", "outputs/tasks/v2b/clip.mp4", other.read_bytes(),
              refs=["outputs/tasks/f2/frame.png"])
    assert blankscan.override_for(store, root, repo.get(store, "segment", s["seg2"])) is None
    blankscan.check(root, "outputs/tasks/v2b/clip.mp4")
    assert any("段落 2 第 0.2–0.9 秒" in p for p in export.readiness(store, root, s["episode"]))
    with pytest.raises(ValueError, match="重出这段再导"):
        export.begin(store, root, s["episode"], PRODUCER)
    # 挑回原来那版:哈希又对上了,放行自动恢复
    repo.update(store, "segment", s["seg2"], video_path="outputs/tasks/v2/clip.mp4")
    assert not any("画面近乎全白" in p for p in export.readiness(store, root, s["episode"]))


def test_blank_override_snapshot_is_checked_again_at_run(store, two_segments, monkeypatch):
    """begin 时没有放行,run 才查出白屏:照样拦(放行只认 begin 快照里的,不在 run 时临时去库里找)。"""
    from tools.cut import blankscan
    s, root = two_segments, two_segments["root"]
    eid = export.begin(store, root, s["episode"], PRODUCER)
    monkeypatch.setattr(blankscan, "detect", lambda path: [blankscan.Span(0.2, 0.9)] if "v2" in str(path) else [])
    blankscan.check(root, "outputs/tasks/v2/clip.mp4")
    blankscan.confirm(store, root, s["seg2"], "雪景,本来就白")
    with pytest.raises(media.CutError, match="段落 2 第 0.2–0.9 秒画面"):
        export.run(store, root, eid)


# ── 配乐 ────────────────────────────────────────────────

def _song(root, secs=5):
    """一首「有许可的」合成音乐:粉红噪声立体声。"""
    src = root / "song_src.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.3",
                    "-t", str(secs), "-ac", "2", str(src)], check=True)
    return music.stage(root, src.read_bytes(), "song.wav")


def _with_cue(store, s, **over):
    rel = _song(s["root"])
    return music.add(store, s["root"], s["episode"], rel, "自购曲库 #42,商用授权", **{
        "start_sec": 1.0, "end_sec": 2.0, "fade_in": 0.2, "fade_out": 0.2, **over})


def test_music_is_mixed_into_both_versions_and_read_back(store, two_segments):
    s, root = two_segments, two_segments["root"]
    cid = _with_cue(store, s, gain_db=0.0)                 # 满音量:混没混,电平上一眼看得出
    eid = export.begin(store, root, s["episode"], PRODUCER)
    export.run(store, root, eid)
    row = export.get(store, eid)
    checks = {c["name"]: c for c in json.loads(row["checks_json"])}
    assert row["state"] == "done", checks
    assert len(checks) == 9 and checks["配乐"]["ok"] and f"#{cid}" in checks["配乐"]["detail"]
    ref = media.rms_envelope(root / "outputs/tasks/v2/clip.mp4")[5]               # 段落 2 中间:原片只有 440 Hz 对白
    for key in ("labeled_path", "plain_path"):                                      # 两版都混了
        assert media.rms_envelope(root / row[key])[15] > ref * 1.05
    [m] = json.loads((root / row["report_path"]).read_text(encoding="utf-8"))["music"]
    assert m["license"] == "自购曲库 #42,商用授权" and m["source"] == "人工提供,非 AI 生成"
    assert (m["start_sec"], m["end_sec"]) == (1.0, 2.0) and len(m["sha256"]) == 64
    assert "人工提供,非 AI 生成" in (root / row["report_path"]).with_suffix(".md").read_text(encoding="utf-8")


def test_without_music_the_check_says_so(store, two_segments):
    s, root = two_segments, two_segments["root"]
    eid = export.begin(store, root, s["episode"], PRODUCER)
    export.run(store, root, eid)
    checks = {c["name"]: c for c in json.loads(export.get(store, eid)["checks_json"])}
    assert checks["配乐"]["ok"] and checks["配乐"]["detail"] == "本集无配乐"


def test_checker_catches_music_that_is_missing_from_the_file(store, two_segments):
    """把没混音乐的成片交给检查器:「配乐」必须 FAIL。"""
    s, root = two_segments, two_segments["root"]
    _with_cue(store, s)
    eid = export.begin(store, root, s["episode"], PRODUCER)
    export.run(store, root, eid)
    row = export.get(store, eid)
    media.encode_episode([root / "outputs/tasks/v1/clip.mp4", root / "outputs/tasks/v2/clip.mp4"],
                         root / "nomusic.mp4", None)
    checks = export.check(store, root, row, row["labeled_path"], "nomusic.mp4", row["report_path"],
                          media.Label.for_size(384, 672))
    bad = {c["name"]: c["detail"] for c in checks if not c["ok"]}
    assert "配乐" in bad and "没混进去" in bad["配乐"]


def test_checker_catches_dialogue_that_was_turned_down(store, two_segments):
    """原音轨被压低 / 改动(cue 外与原音轨对不上):「配乐」必须 FAIL。"""
    s, root = two_segments, two_segments["root"]
    _with_cue(store, s)
    eid = export.begin(store, root, s["episode"], PRODUCER)
    export.run(store, root, eid)
    row = export.get(store, eid)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(root / row["plain_path"]), "-c:v", "copy",
                    "-af", "volume=0.5", "-c:a", "aac", "-b:a", "128k", str(root / "quiet.mp4")], check=True)
    checks = export.check(store, root, row, row["labeled_path"], "quiet.mp4", row["report_path"],
                          media.Label.for_size(384, 672))
    bad = {c["name"]: c["detail"] for c in checks if not c["ok"]}
    assert "配乐" in bad and "cue 外" in bad["配乐"]


def test_music_problems_block_export(store, two_segments):
    s, root = two_segments, two_segments["root"]
    cid = _with_cue(store, s, start_sec=1.0, end_sec=2.5)
    probs = export.readiness(store, root, s["episode"])
    assert any(f"配乐 #{cid}" in p and "超出成片" in p for p in probs)
    repo.update(store, "music_cue", cid, end_sec=2.0)
    assert export.readiness(store, root, s["episode"]) == []
    f = root / repo.get(store, "music_cue", cid)["path"]
    f.write_bytes(b"swapped")
    assert any("哈希" in p for p in export.readiness(store, root, s["episode"]))
    f.unlink()
    with pytest.raises(ValueError, match=f"配乐 #{cid} 的文件不在本机"):
        export.begin(store, root, s["episode"], PRODUCER)


def test_music_swapped_after_begin_fails_the_run(store, two_segments):
    s, root = two_segments, two_segments["root"]
    cid = _with_cue(store, s)
    eid = export.begin(store, root, s["episode"], PRODUCER)
    (root / repo.get(store, "music_cue", cid)["path"]).write_bytes(b"other")
    with pytest.raises(media.CutError, match="配乐"):
        export.run(store, root, eid)


def test_cue_edits_after_begin_do_not_change_this_export(store, two_segments):
    """导出按 begin 时的配乐快照做:之后改 cue 不影响这次的混音、报告与自检。"""
    s, root = two_segments, two_segments["root"]
    cid = _with_cue(store, s)
    eid = export.begin(store, root, s["episode"], PRODUCER)
    repo.update(store, "music_cue", cid, start_sec=0.0, end_sec=1.0)
    export.run(store, root, eid)
    row = export.get(store, eid)
    assert row["state"] == "done", row["checks_json"]
    [m] = json.loads(row["music_json"])
    assert (m["start_sec"], m["end_sec"]) == (1.0, 2.0)
