# -*- coding: utf-8 -*-
"""增量补全入口:先出确认页,确认才建任务。"""
from tests.gen.test_fill import _seg
from tools.drama import repo
from tools.drama.tasks import tasks_for


def test_every_fill_panel_has_the_button(client, sample):
    for url in (f"/episode/{sample['episode']}/frames", f"/episode/{sample['episode']}/videos",
                f"/drama/{sample['drama']}/assets"):
        assert "data-fill-button" in client.get(url).text


def test_confirm_page_lists_items_then_confirm_creates_tasks(client, store, sample):
    a = _seg(store, sample, 2)
    b = _seg(store, sample, 3)
    repo.set_scene(store, b, None)
    r = client.post(f"/fill/frames/{sample['episode']}", data={"base_seed": "40"})
    assert r.status_code == 200 and "将提交 1 条首帧" in r.text and "未绑定场景" in r.text
    assert f'name="t" value="segment:{a}"' in r.text and f'value="segment:{b}"' not in r.text
    assert tasks_for(store, "segment", a) == []                                  # 只看不改

    r = client.post(f"/fill/frames/{sample['episode']}", data={"base_seed": "40", "t": [f"segment:{a}"],
                                                               "confirm": "1"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/episode/{sample['episode']}/frames"
    [t] = tasks_for(store, "segment", a)
    assert t["seed"] == 40 and t["kind"] == "image"


def test_nothing_to_do_disables_confirm(client, sample):
    r = client.post(f"/fill/videos/{sample['episode']}", data={"base_seed": "1"})
    assert "没有要补的视频" in r.text and "data-confirm disabled" in r.text


def test_asset_fill_prepares_the_canvas(client, runtime, store):
    d = repo.create_drama(store, "新剧", "9:16", "realistic")
    lin = repo.add_character(store, d, "林小雨", final_prompt="A young woman.")
    client.post(f"/fill/assets/{d}", data={"base_seed": "3", "t": [f"character:{lin}"], "confirm": "1"})
    assert len(tasks_for(store, "character", lin)) == 1
    assert any(runtime.data_root.rglob("*.png"))                                 # 灰底画布已就位


def test_bad_seed_and_kind_are_readable_400s(client, sample):
    assert "起始种子要填整数" in client.post(f"/fill/frames/{sample['episode']}", data={"base_seed": "x"}).text
    assert client.post(f"/fill/music/{sample['episode']}", data={"base_seed": "1"}).status_code == 400
