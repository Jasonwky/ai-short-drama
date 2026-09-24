# -*- coding: utf-8 -*-
import pytest
from fastapi.testclient import TestClient

from tests.drama.conftest import sample, store  # noqa: F401  —— 复用 P1 的夹具
from tools.serve.app import create_app
from tools.serve.fakes import fake_backend
from tools.serve.hosts import add_host
from tools.serve.runtime import Runtime


@pytest.fixture
def host_id(store):  # noqa: F811
    return add_host(store, label="4090", ssh_host="connect.example.com", ssh_port=14138, ssh_user="root",
                    key_ref="C:/Users/me/.ssh/id_ed25519", local_port=8188, vram_gb=24.0,
                    models_dir="/root/autodl-tmp/comfyui-models")


@pytest.fixture
def runtime(store, tmp_path):  # noqa: F811
    """控制台运行时,接假后端;data_root 是每个测试自己的临时目录。"""
    backend, _ = fake_backend()
    return Runtime(store, tmp_path, backend)


@pytest.fixture
def client(runtime):
    return TestClient(create_app(runtime))
