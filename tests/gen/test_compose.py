# -*- coding: utf-8 -*-
import pytest

from tools.drama.refs import Ref
from tools.drama.tags import check_tags
from tools.gen import compose

XIAO, YARD = Ref("character", "萧珩", "x.png"), Ref("scene", "正堂", "s.png")


def test_second_pass_prompt_names_each_reference_and_passes_tag_check():
    p = compose.second_pass_prompt([XIAO, YARD])
    assert p.startswith("保持 Picture 1 的画面、构图、光线、动作和其他人物完全不变")
    assert "把画面中的萧珩换成 Picture 2(萧珩) 的长相、发型发饰和服装" in p
    assert "把画面中的正堂换成 Picture 3(正堂) 的样子" in p
    assert check_tags(p, n_images=3, dialect="qwen") == []


def test_region_prompt_for_character_and_note():
    p = compose.region_prompt(XIAO, "神情冷峻")
    assert "把其中的人物换成 Picture 2(萧珩) 的长相、发型发饰和服装。神情冷峻" in p
    assert check_tags(p, n_images=2, dialect="qwen") == []
    assert "换成 Picture 2(正堂) 的样子" in compose.region_prompt(YARD, "")


def test_region_box_aligns_pads_and_clamps():
    # 框 (100,200)-(300,500):外扩 32 → (68,168)-(332,532);左上向下、右下向上对齐 16 → (64,160)-(336,544)
    assert compose.region_box([100, 200, 200, 300], (928, 1664)) == (64, 160, 272, 384)
    # 贴边:夹在图内
    assert compose.region_box([0, 0, 200, 200], (928, 1664)) == (0, 0, 240, 240)
    x, y, w, h = compose.region_box([800, 1500, 128, 164], (928, 1664))
    assert x + w <= 928 and y + h <= 1664


def test_region_box_refuses_small_or_outside():
    with pytest.raises(ValueError, match="至少 128"):
        compose.region_box([10, 10, 60, 300], (928, 1664))
    with pytest.raises(ValueError, match="超出图片"):
        compose.region_box([900, 10, 200, 200], (928, 1664))


def test_region_work_size_is_about_one_megapixel_and_16_aligned():
    w, h = compose.work_size(272, 368)
    assert w % 16 == 0 and h % 16 == 0 and 0.85e6 < w * h < 1.15e6
    assert abs(w / h - 272 / 368) < 0.05
