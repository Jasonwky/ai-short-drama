# -*- coding: utf-8 -*-
from tools.serve.hosts import get_host
from tools.serve.panels import REGISTRY, url_for

FORM = {"label": "4090", "ssh_host": "connect.example.com", "ssh_port": "14138", "key_ref": "C:/k/id_ed25519",
        "models_dir": "/root/autodl-tmp/comfyui-models"}


def test_add_host_then_status_after_a_round(client, runtime):
    client.post("/hosts", data=FORM)
    assert "worker 还没跑完第一轮" in client.get("/hosts").text
    runtime.tick_all()
    assert "正常:ComfyUI 队列" in client.get("/hosts").text


def test_private_key_content_is_refused(client):
    r = client.post("/hosts", data={**FORM, "key_ref": "-----BEGIN OPENSSH PRIVATE KEY-----"})
    assert r.status_code == 400 and "私钥不许入库" in r.text


def test_change_port(client, store):
    client.post("/hosts", data=FORM)
    client.post("/host/1/port", data={"ssh_port": "22222"})
    assert get_host(store, 1).ssh_port == 22222


def test_global_panels_keep_the_drama_context(client, sample):
    hosts = next(p for p in REGISTRY if p.key == "hosts")
    url = url_for(hosts, sample["drama"], sample["episode"])
    assert url == f"/hosts?d={sample['drama']}"
    assert f'href="/drama/{sample["drama"]}/assets"' in client.get(url).text     # 左侧步骤仍可点
