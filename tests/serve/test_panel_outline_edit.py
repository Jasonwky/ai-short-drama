# -*- coding: utf-8 -*-
"""手写每集大纲(spec 2026-10-24-逐集批量出剧本分镜-design.md 1.1 节):有剧本就拒、保存清节拍。"""
from tools.drama import repo


def test_episode_without_script_can_get_a_handwritten_outline(client, store, sample):
    e2 = repo.add_episode(store, sample["drama"], 2, "script")
    page = client.get(f"/drama/{sample['drama']}/outline").text
    assert f'action="/episode/{e2}/outline"' in page
    repo.update(store, "episode", e2, beats_json='{"hook": "有"}')               # 只为验证保存会清掉它
    client.post(f"/episode/{e2}/outline", data={"outline": " 林小雨回老宅找玉佩。 "})
    ep = repo.get(store, "episode", e2)
    assert ep["outline"] == "林小雨回老宅找玉佩。" and ep["beats_json"] == ""


def test_outline_of_a_scripted_episode_is_refused(client, store, sample):
    r = client.post(f"/episode/{sample['episode']}/outline", data={"outline": "新大纲"})
    assert r.status_code == 400 and "已有剧本" in r.text
    assert f'action="/episode/{sample["episode"]}/outline"' not in client.get(f"/drama/{sample['drama']}/outline").text
