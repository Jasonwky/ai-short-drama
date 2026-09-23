# -*- coding: utf-8 -*-
import pytest

from tools.gen.capability import MINIMAX_H3, QWEN_IMAGE_EDIT_2511, Capability


def test_estimate_reproduces_both_measurements():
    assert MINIMAX_H3.est_seconds(192) == pytest.approx(20 * 44.4)    # 14.8 分钟
    assert MINIMAX_H3.est_seconds(328) == pytest.approx(20 * 109.3)   # 36.4 分钟


def test_estimate_is_superlinear_between_points():
    mid = MINIMAX_H3.est_seconds(260)
    linear = 20 * 44.4 * 260 / 192
    assert 20 * 44.4 < mid < 20 * 109.3
    assert mid > linear


def test_explicit_steps_override_default():
    assert MINIMAX_H3.est_seconds(192, steps=8) == pytest.approx(8 * 44.4)


def test_no_measurements_refuses_to_guess():
    with pytest.raises(ValueError, match="没有实测耗时点"):
        QWEN_IMAGE_EDIT_2511.est_seconds(192)


def test_single_point_is_linear():
    cap = Capability(name="x", fps=24, frame_grid_step=17, frame_grid_base=5, frame_min_trained=124,
                     frame_max_trained=362, max_ref_images=1, timing_points=((100, 10.0),), default_steps=2)
    assert cap.est_seconds(200) == pytest.approx(40.0)


def test_image_backend_declares_per_item_time():
    assert QWEN_IMAGE_EDIT_2511.sec_per_item == pytest.approx(25.3)
