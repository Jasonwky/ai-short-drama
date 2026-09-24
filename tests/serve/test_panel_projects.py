# -*- coding: utf-8 -*-
"""控制台骨架 + 立项面板。面板「看起来对不对」由 Playwright 自测截图为凭,这里只测路由行为。"""
from tools.drama import repo
from tools.serve.panels import REGISTRY, url_for


def test_every_registered_panel_renders(client, sample):
    for p in REGISTRY:
        r = client.get(url_for(p, sample["drama"], sample["episode"]))
        assert r.status_code == 200, p.key
        assert f'data-panel="{p.key}"' in r.text and "Powered by MiniMax H3" in r.text


def test_fake_backend_is_announced_on_every_page(client):
    assert "假后端" in client.get("/").text


def test_create_drama_with_style(client, store):
    r = client.post("/dramas", data={"title": "新剧", "aspect_ratio": "9:16", "style": "realistic"},
                    follow_redirects=False)
    assert r.status_code == 303
    did = int(r.headers["location"].split("/")[2])
    assert repo.get(store, "drama", did)["style"] == "realistic"
    assert "新剧" in client.get("/").text


def test_refusals_are_400_pages_not_500(client):
    r = client.post("/dramas", data={"title": " ", "aspect_ratio": "9:16", "style": "realistic"})
    assert r.status_code == 400 and "剧名不能为空" in r.text
    r = client.post("/dramas", data={"title": "x", "aspect_ratio": "9:16", "style": "watercolor"})
    assert r.status_code == 400 and "未知风格预设" in r.text


def test_media_refuses_paths_outside_data_root(client, runtime):
    secret = runtime.data_root.parent / "secret.txt"
    secret.write_text("outside")
    (runtime.data_root / "inside.txt").write_text("inside")
    assert client.get("/media/inside.txt").text == "inside"
    r = client.get("/media/%2e%2e/secret.txt")     # 编码过的 ..,httpx 不会替我们规范化掉
    assert r.status_code == 404 and "outside" not in r.text
