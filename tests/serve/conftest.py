# -*- coding: utf-8 -*-
import pytest

from tests.drama.conftest import sample, store  # noqa: F401  —— 复用 P1 的夹具
from tools.serve.hosts import add_host


@pytest.fixture
def host_id(store):  # noqa: F811
    return add_host(store, label="4090", ssh_host="connect.example.com", ssh_port=14138, ssh_user="root",
                    key_ref="C:/Users/me/.ssh/id_ed25519", local_port=8188, vram_gb=24.0,
                    models_dir="/root/autodl-tmp/comfyui-models")
