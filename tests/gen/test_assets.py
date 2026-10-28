# -*- coding: utf-8 -*-
import json
import struct
import zlib

import pytest

from tools.drama import repo
from tools.gen.assets import CANVAS, ensure_canvas, plan_asset_image
from tools.serve.dispatch import build_workflow


def _task(store, tid):
    row = repo.get(store, "task", tid)
    return row, json.loads(row["params_json"])


def test_canvas_is_a_valid_gray_png(tmp_path):
    rel = ensure_canvas(tmp_path)
    data = (tmp_path / rel).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    idat_len = struct.unpack(">I", data[33:37])[0]
    pixels = zlib.decompress(data[41:41 + idat_len])
    assert (width, height) == (1024, 1024) and pixels[1] == 128
    assert ensure_canvas(tmp_path) == rel            # 已存在就不重写


def test_realistic_character_is_forced_fictional(store, sample):
    repo.update(store, "character", sample["lin"], final_prompt="A young woman in a cream blouse.")
    store.conn.execute("UPDATE drama SET style = 'realistic' WHERE id = ?", (sample["drama"],))
    row, p = _task(store, plan_asset_image(store, "character", sample["lin"], seed=5101))
    assert (row["target_type"], row["kind"], row["seed"]) == ("character", "image", 5101)
    assert row["prompt"].startswith("Completely replace the entire image with a photorealistic")
    assert "fictional person" in row["prompt"] and "front view" in row["prompt"]
    assert p["writes"] == "turnaround_path" and p["size"] == [928, 1664] and p["image_refs"] == [CANVAS]


def test_scene_follows_drama_aspect(store, sample):
    repo.update(store, "scene", sample["yard"], final_prompt="An empty courtyard at dusk.")
    store.conn.execute("UPDATE drama SET style = 'realistic' WHERE id = ?", (sample["drama"],))
    row, p = _task(store, plan_asset_image(store, "scene", sample["yard"], seed=1))
    assert p["size"] == [928, 1664] and "no people" in row["prompt"] and "fictional" not in row["prompt"]


def test_missing_style_and_prompt_fail_together(store, sample):
    store.conn.execute("UPDATE drama SET style = '' WHERE id = ?", (sample["drama"],))
    with pytest.raises(ValueError) as e:
        plan_asset_image(store, "prop", sample["jade"], seed=1)
    assert "风格预设" in str(e.value) and "final_prompt" in str(e.value)


def test_asset_task_builds_a_qwen_workflow_at_target_size(store, sample):
    repo.update(store, "prop", sample["jade"], final_prompt="A green jade pendant.")
    store.conn.execute("UPDATE drama SET style = 'anime' WHERE id = ?", (sample["drama"],))
    row, _ = _task(store, plan_asset_image(store, "prop", sample["jade"], seed=9))
    wf = build_workflow(row, {"assets/_canvas/gray_1024.png": "canvas.png"})
    assert wf["30"]["inputs"]["width"] == 1024 and wf["27"]["inputs"]["seed"] == 9


ART = '{"era": "架空古代边军", "costume": "交领右衽粗布", "banned": ["uniform", "口袋"]}'


def test_art_setting_is_appended_by_code_and_recorded(store, sample):
    """spec 2026-10-28 第 2 节:时代 / 服饰总则由代码拼进资产图与四视图,参数记当时的设定。"""
    from tools.gen.assets import plan_fourview
    store.conn.execute("UPDATE drama SET style = 'realistic', art_json = ? WHERE id = ?", (ART, sample["drama"]))
    repo.update(store, "character", sample["lin"], final_prompt="A young woman in a cream blouse.")
    row, p = _task(store, plan_asset_image(store, "character", sample["lin"], seed=1))
    assert "cream blouse. Setting: 架空古代边军. Costume: 交领右衽粗布. Full body" in row["prompt"]
    assert p["art"]["era"] == "架空古代边军"
    row, _ = _task(store, plan_fourview(store, sample["lin"], seed=2))
    assert "Setting: 架空古代边军. Costume: 交领右衽粗布." in row["prompt"]


def test_banned_word_blocks_asset_image(store, sample):
    store.conn.execute("UPDATE drama SET style = 'realistic', art_json = ? WHERE id = ?", (ART, sample["drama"]))
    repo.update(store, "character", sample["lin"], final_prompt="Old man in a grey Uniform.", styling="胸前两个口袋")
    with pytest.raises(ValueError, match="禁用词「uniform」.*禁用词「口袋」"):
        plan_asset_image(store, "character", sample["lin"], seed=1)


def test_no_art_keeps_prompt_unchanged(store, sample):
    store.conn.execute("UPDATE drama SET style = 'realistic' WHERE id = ?", (sample["drama"],))
    repo.update(store, "character", sample["lin"], final_prompt="A young woman.")
    row, p = _task(store, plan_asset_image(store, "character", sample["lin"], seed=1))
    assert "Setting:" not in row["prompt"] and "art" not in p
