# -*- coding: utf-8 -*-
"""段落编辑页的场记表(spec 2026-10-22-连续性保障-design.md 第 4 项)。"""
import json

from tools.drama import repo


def test_blocking_editor_saves_rows_and_previews_the_phrase(client, store, sample):
    page = client.get(f"/segment/{sample['seg']}/edit").text
    assert "data-blocking-row=\"林小雨\"" in page and "(还没保存)" in page
    form = {"character": "林小雨", "pose": "跪", "facing": "镜头", "side": "左", "holding": "玉佩"}
    assert client.post(f"/segment/{sample['seg']}/blocking", data=form, follow_redirects=False).status_code == 303
    (e,) = json.loads(repo.get(store, "segment", sample["seg"])["blocking_json"])
    assert (e["pose"], e["side"], e["holding"]) == ("跪", "左", "玉佩")
    assert "@林小雨 跪着,面朝镜头,在画面左侧,手里拿着@玉佩" in client.get(f"/segment/{sample['seg']}/edit").text


def test_blocking_with_values_outside_the_closed_set_is_refused(client, store, sample):
    form = {"character": "林小雨", "pose": "飘", "facing": "镜头", "side": "左", "holding": "无"}
    r = client.post(f"/segment/{sample['seg']}/blocking", data=form)
    assert r.status_code == 400 and "姿态「飘」" in r.text
    assert repo.get(store, "segment", sample["seg"])["blocking_json"] == ""
