# -*- coding: utf-8 -*-
"""主机面板「刷新节点清单」与 worker 自动刷新。"""
from tools.gen import nodecache
from tools.serve.hosts import add_host


def _host(store):
    return add_host(store, label="4080S", ssh_host="h", ssh_port=1, ssh_user="root", key_ref="k", local_port=8188,
                    vram_gb=32.0, models_dir="/m")


def test_button_refreshes_and_page_shows_the_cache(client, runtime, store):
    hid = _host(store)
    assert "还没抓过" in client.get("/hosts").text
    r = client.post(f"/host/{hid}/object_info", follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/hosts").text
    assert "35 个节点类型" in page and "要求前端 <code>1.51.9</code>" in page


def test_unreachable_cloud_is_a_readable_400(client, runtime, store):
    hid = _host(store)
    runtime.backend.client_for(None).reachable = False
    r = client.post(f"/host/{hid}/object_info")
    assert r.status_code == 400 and "连不上云机的 ComfyUI" in r.text
    assert nodecache.meta(runtime.data_root) is None


def test_worker_refreshes_a_missing_or_stale_cache(runtime, store):
    _host(store)
    runtime.tick_all()
    assert nodecache.meta(runtime.data_root)["node_types"] == 35
