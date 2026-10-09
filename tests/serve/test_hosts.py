# -*- coding: utf-8 -*-
import pytest

from tools.serve.hosts import add_host, get_host, set_ssh_port

BASE = dict(label="4090", ssh_host="h", ssh_port=1, ssh_user="root", key_ref="k.pem",
            local_port=8188, vram_gb=24.0, models_dir="/m")


def test_round_trip_and_endpoint(store, host_id):
    h = get_host(store, host_id)
    assert (h.ssh_port, h.models_dir, h.remote_comfy_port) == (14138, "/root/autodl-tmp/comfyui-models", 8188)
    assert h.local_endpoint == "127.0.0.1:8188"


def test_private_key_content_is_refused(store):
    with pytest.raises(ValueError, match="私钥不许入库"):
        add_host(store, **{**BASE, "key_ref": "-----BEGIN OPENSSH PRIVATE KEY-----\nabc"})


def test_models_dir_must_be_absolute_remote_path(store):
    with pytest.raises(ValueError, match="绝对路径"):
        add_host(store, **{**BASE, "models_dir": "comfyui-models"})


def test_ssh_port_changes_after_reboot(store, host_id):
    set_ssh_port(store, host_id, 22222)
    assert get_host(store, host_id).ssh_port == 22222
    with pytest.raises(KeyError):
        set_ssh_port(store, 999, 1)


def test_gpu_must_have_timing_data(store):
    with pytest.raises(ValueError, match="没有实测耗时"):
        add_host(store, **{**BASE, "gpu": "RTX 5090"})


def test_gpu_can_be_registered_later_without_looking_like_a_config_change(store, host_id):
    from tools.serve.hosts import set_gpu
    before = get_host(store, host_id)
    assert before.gpu is None
    set_gpu(store, host_id, "RTX 4080 SUPER")
    after = get_host(store, host_id)
    assert after.gpu == "RTX 4080 SUPER"
    assert after == before          # worker 拿 Host 相等判断「连接配置变了」要重建隧道 —— 换卡型号不算
    with pytest.raises(ValueError, match="没有实测耗时"):
        set_gpu(store, host_id, "GTX 1080")
