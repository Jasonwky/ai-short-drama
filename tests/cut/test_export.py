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
    assert all(c["ok"] for c in checks) and len(checks) == 7
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
