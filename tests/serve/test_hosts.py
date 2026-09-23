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
