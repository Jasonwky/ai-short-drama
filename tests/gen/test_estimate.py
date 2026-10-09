# -*- coding: utf-8 -*-
"""估时:每张卡一份实测数据,按已登记云机里最慢的那张卡算(最终用哪张卡没定,不能写死一张)。"""
import pytest

from tools.gen import estimate
from tools.gen.capability import GPU_TIMINGS, MINIMAX_H3, QWEN_IMAGE_EDIT_2511, WHISPER_ALIGN
from tools.serve.hosts import add_host

R4090, R4080S = GPU_TIMINGS["RTX 4090"], GPU_TIMINGS["RTX 4080 SUPER"]
HOST = dict(ssh_host="h", ssh_port=1, ssh_user="root", key_ref="k", local_port=8188, vram_gb=24.0, models_dir="/m")


def test_4090_reproduces_both_measurements():
    assert R4090.video(MINIMAX_H3, 192) == pytest.approx(20 * 44.4)    # 14.8 分钟
    assert R4090.video(MINIMAX_H3, 328) == pytest.approx(20 * 109.3)   # 36.4 分钟


def test_cost_is_superlinear_in_frames():
    mid = R4090.video(MINIMAX_H3, 260)
    assert 20 * 44.4 * 260 / 192 < mid < 20 * 109.3


def test_4080s_measured_at_192_frames_and_borrows_the_model_shape():
    assert R4080S.video(MINIMAX_H3, 192) == pytest.approx(20 * 72.3)   # 2026-10-09 实测
    # 328 帧没在 4080S 上测过:曲线形状(帧数 → 每步耗时的幂律)是模型的性质,与 4090 同比例放大
    assert R4080S.video(MINIMAX_H3, 328) == pytest.approx(20 * 109.3 * 72.3 / 44.4)


def test_explicit_steps_override_default():
    assert R4080S.video(MINIMAX_H3, 192, steps=8) == pytest.approx(8 * 72.3)


def test_per_item_times():
    assert (R4090.item(QWEN_IMAGE_EDIT_2511), R4080S.item(QWEN_IMAGE_EDIT_2511)) == (25.3, 41.0)
    assert (R4090.item(WHISPER_ALIGN), R4080S.item(WHISPER_ALIGN)) == (30.0, 36.0)


def test_a_backend_without_measurement_refuses_to_guess():
    with pytest.raises(ValueError, match="没有实测"):
        R4090.video(QWEN_IMAGE_EDIT_2511, 192)


def test_no_host_no_estimate(store):
    b = estimate.basis(store)
    assert b.timing is None and b.video(MINIMAX_H3, 192) is None and b.item(QWEN_IMAGE_EDIT_2511) is None
    assert b.note == "无法估时:还没登记云机"


def test_host_without_gpu_is_named(store):
    add_host(store, label="老机器", **HOST)
    add_host(store, label="新机器", gpu="RTX 4090", **HOST)
    b = estimate.basis(store)
    assert b.timing is None and b.note == "无法估时:云机「老机器」没登记 GPU 型号 —— 到「主机」面板登记"


def test_one_host_uses_its_card(store):
    add_host(store, label="a", gpu="RTX 4090", **HOST)
    b = estimate.basis(store)
    assert b.video(MINIMAX_H3, 192) == pytest.approx(20 * 44.4) and b.note == "按 RTX 4090 估算"


def test_mixed_cards_use_the_slowest(store):
    add_host(store, label="a", gpu="RTX 4090", **HOST)
    add_host(store, label="b", gpu="RTX 4080 SUPER", **HOST)
    b = estimate.basis(store)
    assert b.timing is R4080S and b.note == "按 RTX 4080 SUPER 估算(已登记云机里最慢的一张)"
