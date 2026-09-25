# -*- coding: utf-8 -*-
import json

import pytest

from tools.drama import repo, uploads
from tools.drama.tasks import tasks_for

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 32
JPG = b"\xff\xd8\xff\xe0" + b"y" * 32


def test_stage_accepts_png_and_jpeg_by_content_and_is_content_addressed(tmp_path):
    a = uploads.stage(tmp_path, PNG)
    assert a.startswith("assets/uploads/") and a.endswith(".png") and (tmp_path / a).read_bytes() == PNG
    assert uploads.stage(tmp_path, PNG) == a
    assert uploads.stage(tmp_path, JPG).endswith(".jpg")


def test_stage_rejects_other_files_and_oversize(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="只收 PNG 或 JPEG"):
        uploads.stage(tmp_path, b"GIF89a....")
    monkeypatch.setattr(uploads, "MAX_BYTES", 10)
    with pytest.raises(ValueError, match="图片太大"):
        uploads.stage(tmp_path, PNG)


def test_first_upload_costs_nothing_and_is_recorded_as_a_task(store, sample, tmp_path):
    d = repo.create_drama(store, "新剧", "9:16", "realistic")
    ch = repo.add_character(store, d, "沈墨")
    rel = uploads.stage(tmp_path, PNG)
    imp = uploads.plan_use(store, "character", ch)
    assert imp.summary() == "不影响任何已生成的产物"
    tid = uploads.confirm_use(store, tmp_path, "character", ch, rel, "shen.png", imp)
    assert repo.get(store, "character", ch)["turnaround_path"] == rel
    [t] = tasks_for(store, "character", ch)
    assert t["id"] == tid and (t["model"], t["state"], t["result_path"]) == ("manual:upload", "done", rel)
    assert json.loads(t["params_json"]) == {"workflow": "upload", "writes": "turnaround_path", "filename": "shen.png"}


def test_replacing_a_used_image_invalidates_bound_segments(store, sample, tmp_path):
    rel = uploads.stage(tmp_path, PNG)
    imp = uploads.plan_use(store, "character", sample["lin"])
    assert imp.videos == 1 and imp.segment_ids == (sample["seg"],)
    uploads.confirm_use(store, tmp_path, "character", sample["lin"], rel, "lin.png", imp)
    seg = repo.get(store, "segment", sample["seg"])
    assert seg["video_path"] is None and seg["first_frame_path"] is None


def test_confirm_only_takes_staged_uploads(store, sample, tmp_path):
    for bad in ("../../etc/passwd", "assets/lin.png", "assets/uploads/0123456789abcdef.png"):
        with pytest.raises(ValueError, match="不是本机暂存的上传图"):
            uploads.confirm_use(store, tmp_path, "character", sample["lin"], bad, "x", uploads.plan_use(
                store, "character", sample["lin"]))
