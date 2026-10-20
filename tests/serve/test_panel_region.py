# -*- coding: utf-8 -*-
"""参考帧面板的「局部替换」:框选 + 选素材 → 规划 qwen_region 任务(测试客户端;真拖框在有头自测里)。"""
import json

from tools.drama import repo
from tools.drama.tasks import tasks_for
from tools.gen.assets import _png


def _frame(runtime, rel="frames/s1.png", size=(512, 912)):
    path = runtime.data_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_png(*size, 100))


def _post(client, sample, **over):
    data = {"slot": "first_frame_path", "source": "frames/s1.png", "x": "100", "y": "200", "w": "200", "h": "300",
            "asset": f"character:{sample['lin']}", "note": "神情冷峻", "seed": "7"} | over
    return client.post(f"/segment/{sample['seg']}/region", data=data, follow_redirects=False)


def test_region_form_plans_a_task_with_the_real_image_size(client, runtime, store, sample):
    _frame(runtime)
    r = _post(client, sample)
    assert r.status_code == 303 and r.headers["location"] == f"/episode/{sample['episode']}/frames#seg-{sample['seg']}"
    [t] = tasks_for(store, "segment", sample["seg"])
    p = json.loads(t["params_json"])
    assert (p["workflow"], p["writes"], p["size"], p["note"], t["seed"]) == (
        "qwen_region", "first_frame_path", [512, 912], "神情冷峻", 7)
    assert f'data-task="{t["id"]}"' in client.get(f"/episode/{sample['episode']}/frames").text   # 进了历史


def test_region_on_a_stale_image_is_refused(client, runtime, store, sample):
    _frame(runtime, "frames/old.png")
    r = _post(client, sample, source="frames/old.png")
    assert r.status_code == 400 and "框是在旧图上画的" in r.text
    assert tasks_for(store, "segment", sample["seg"]) == []


def test_region_with_an_unbound_asset_is_refused(client, runtime, store, sample):
    _frame(runtime)
    r = _post(client, sample, asset=f"character:{sample['shen']}")
    assert r.status_code == 400 and "不是本段绑定" in r.text
    assert tasks_for(store, "segment", sample["seg"]) == []


def test_region_refuses_missing_files_and_paths_outside_data_root(client, runtime, store, sample):
    r = _post(client, sample)                                     # 当前帧的文件不在盘上
    assert r.status_code == 400 and "图片文件不存在" in r.text
    r = _post(client, sample, source="../outside.png")
    assert r.status_code == 400
    assert tasks_for(store, "segment", sample["seg"]) == []


def test_frames_page_offers_region_replace_with_bound_assets_only(client, store, sample):
    page = client.get(f"/episode/{sample['episode']}/frames").text
    assert "局部替换" in page and "框住整个人;宽高至少 128 像素" in page
    assert f'value="character:{sample["lin"]}"' in page and f'value="prop:{sample["jade"]}"' in page
    assert f'value="scene:{sample["yard"]}"' in page
    assert f'value="character:{sample["shen"]}"' not in page        # 没绑定的不列
    repo.update(store, "segment", sample["seg"], first_frame_path=None, video_path=None)
    assert "data-region" not in client.get(f"/episode/{sample['episode']}/frames").text   # 没图就没有局部替换


def test_region_note_is_not_prefilled_but_teaches_to_write_the_expression(client, runtime, store, sample):
    """补充说明不预填(照搬图提示词实测 0/3),占位提示教人写神情和发饰(手写 3/3)。"""
    repo.update(store, "segment", sample["seg"], image_prompt="@林小雨 惊恐地回头,站在 @老宅庭院 里,手里拿着 @玉佩")
    _frame(runtime)
    html = client.get(f"/episode/{sample['episode']}/frames").text
    assert "data-note" not in html and "惊恐地回头,站在" not in html.split("data-region", 1)[1]
    assert 'placeholder="写出此刻的神情和要保住的发饰,如:保持惊恐的神情,头戴黑色幞头"' in html
