# -*- coding: utf-8 -*-
"""真 ffmpeg 真导出(两段 1 秒样片,几秒钟)。"""
import json
import sqlite3

import pytest

from tools.cut import export, media
from tools.drama import repo

PRODUCER = "91310000MA1234567X"


def test_export_writes_both_versions_report_and_passes_readback(store, two_segments):
    s, root = two_segments, two_segments["root"]
    eid = export.begin(store, root, s["episode"], f"  {PRODUCER} ")
    export.run(store, root, eid)
    row = export.get(store, eid)
    checks = json.loads(row["checks_json"])
    assert row["state"] == "done" and row["error"] is None, checks
    assert all(c["ok"] for c in checks) and len(checks) == 8
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
