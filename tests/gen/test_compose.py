# -*- coding: utf-8 -*-
import pytest

from tools.drama.refs import Ref
from tools.drama.tags import check_tags
from tools.gen import compose

XIAO, YARD = Ref("character", "萧珩", "x.png"), Ref("scene", "正堂", "s.png")


def test_region_prompt_is_the_one_that_passed_on_the_cloud():
    # spike-results/2026-10-20-P15两遍合成与局部替换云机实测.md 的「regionfix」:3/3 认对人、幞头回来
    p = compose.region_prompt(Ref("character", "李福", "l.png"), "", "深褐色管事长袍,腰系黑色布带,头戴黑色幞头")
    assert p == ("保持 Picture 1 的构图、姿态、动作、神情、光线和背景不变,把其中的人物换成 Picture 2(李福) 的长相、"
                 "发型发饰和服装(深褐色管事长袍,腰系黑色布带,头戴黑色幞头)。")
    assert check_tags(p, n_images=2, dialect="qwen") == []


def test_region_prompt_appends_the_note_and_handles_scenes():
    assert compose.region_prompt(XIAO, " 保持惊恐的神情 ").endswith("的长相、发型发饰和服装。保持惊恐的神情")
    assert compose.region_prompt(YARD, "") == ("保持 Picture 1 的构图、姿态、动作、神情、光线和背景不变,"
                                               "把其中的正堂换成 Picture 2(正堂) 的样子。")


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


def test_png_size_reads_the_header():
    from tools.gen.assets import _png
    assert compose.png_size(_png(96, 160, 150)) == (96, 160)
    with pytest.raises(ValueError, match="不是 PNG"):
        compose.png_size(b"GIF89a....")


def test_second_pass_prompt_is_the_one_that_passed_on_the_cloud():
    # spike-results/2026-10-20-多人同框留位加人实测.md:3/3
    qing = Ref("character", "沈青禾", "q.png")
    p = compose.second_pass_prompt(["@沈青禾 身着大红嫁衣站在床边冷眼旁观"], [qing])
    assert p == ("保持 Picture 1 的画面、构图、光线和已有人物完全不变,在画面中加入:"
                 "Picture 2(沈青禾) 身着大红嫁衣站在床边冷眼旁观。")
    two = compose.second_pass_prompt(["@沈青禾 站在床边", "@玉佩 放在桌上"], [qing, Ref("prop", "玉佩", "j.png")])
    assert two.endswith("加入:Picture 2(沈青禾) 站在床边;Picture 3(玉佩) 放在桌上。")
    assert check_tags(two, n_images=3, dialect="qwen") == []
