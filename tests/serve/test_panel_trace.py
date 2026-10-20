# -*- coding: utf-8 -*-
"""「留痕」面板、倒查页、任务详情页(测试客户端;有头浏览器的那一遍在自测里)。"""
import html
import json

from tests.cut.conftest import done_task, mp4_bytes, two_segments  # noqa: F401
from tests.serve.test_trace_views import _export, _with_music
from tools.cut import provenance
from tools.drama import repo
from tools.drama.tasks import create_task, set_task_state
from tools.serve import journal
from tools.serve.panels import REGISTRY
from tools.serve.panels.trace import _utc, bj


def _failed(store, target_id, msg="ComfyUI 拒收:/prompt 返回 HTTP 400:x"):
    t = create_task(store, kind="image", target_type="character", target_id=target_id, model="m", params={},
                    seed=1, prompt="p", refs=[])
    set_task_state(store, t, "failed", error=msg)
    return t


def test_registered_as_a_global_panel():
    p = next(p for p in REGISTRY if p.key == "trace")
    assert (p.title, p.scope, p.path) == ("留痕", "global", "/trace")


def test_empty_state_says_so(client, sample):
    page = client.get(f"/trace?d={sample['drama']}").text
    assert "data-trace-filters" in page and "还没有失败" in page and "没有符合条件的事件" in page and "还没有流水" in page


def test_failures_timeline_and_filters(client, store, sample):
    t = _failed(store, sample["lin"])
    d2 = repo.create_drama(store, "别的剧", "9:16")
    other = _failed(store, repo.add_character(store, d2, "沈一"), "参考图不存在:x")
    page = client.get(f"/trace?d={sample['drama']}").text
    assert 'data-failure-group="ComfyUI 拒收"' in page and f'href="/task/{t}"' in page
    assert f'href="/task/{other}"' not in page                                    # 别的剧不显示
    only_failed = client.get(f"/trace?d={sample['drama']}&failed=1").text
    assert only_failed.count('data-action="失败"') == only_failed.count("data-event=") == 1
    assert "没有符合条件的事件" in client.get(f"/trace?d={sample['drama']}&kind=export").text
    assert "没有符合条件的事件" in client.get(f"/trace?since=2999-01-01T00:00").text


def test_raw_journal_lines_link_to_tasks(client, runtime, sample):
    journal.append(runtime.data_root, ["task 7: pending → queued p1"], prefix="host=1 ")
    journal.append(runtime.data_root, ["工程导出 drama 1 → x.zip"], prefix="console ")
    page = client.get("/trace").text
    assert '<a href="/task/7">task 7</a>' in page
    assert "工程导出" not in client.get("/trace?prefix=host%3D").text


def test_beijing_time_both_ways():
    assert bj("2026-10-09T04:48:07+00:00") == "10-09 12:48:07"
    assert _utc("2026-10-09T12:48") == "2026-10-09T04:48:00+00:00" and _utc("") == ""


def test_product_page_walks_back_and_refuses_escapes(client, runtime, store, two_segments):
    s = two_segments
    runtime.data_root = s["root"]
    video = repo.get(store, "segment", s["seg1"])["video_path"]
    page = client.get("/trace/product", params={"path": video}).text
    assert "data-trace-tree" in page and 'data-node="ok"' in page and "outputs/tasks/f1/frame.png" in page
    assert client.get("/trace/product", params={"path": "../x.txt"}).status_code == 404


def test_export_paths_go_to_the_snapshot(client, runtime, store, two_segments):
    s = two_segments
    runtime.data_root = s["root"]
    eid = _export(store, s["root"], s)
    r = client.get("/trace/product", params={"path": "outputs/exports/x/labeled.mp4"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/trace/export/{eid}"
    page = client.get(f"/trace/export/{eid}").text
    assert "ASD-1" in page and "现在的文件与导出时一致" in page and 'data-export-segment="1"' in page
    assert client.get("/trace/export/999").status_code == 404
    assert f'href="/trace/export/{eid}"' in client.get(f"/episode/{s['episode']}/final").text


def test_task_page_shows_the_whole_task(client, runtime, store, two_segments):
    s = two_segments
    runtime.data_root = s["root"]
    tid = provenance.node(store, s["root"], repo.get(store, "segment", s["seg1"])["video_path"])["task"]["id"]
    journal.append(s["root"], [f"task {tid}: queued → done"], prefix="host=1 ")
    page = client.get(f"/task/{tid}").text
    assert "video prompt" in page and "outputs/tasks/f1/frame.png" in page and f'<a href="/task/{tid}">task {tid}</a>: queued → done' in page
    assert json.dumps("video_path") in html.unescape(page)                  # 参数(浏览器里看到的)
    assert client.get("/task/99999").status_code == 404


def test_history_entries_link_to_task_and_trace(client, store, sample):
    t = create_task(store, kind="image", target_type="segment", target_id=sample["seg"], model="m",
                    params={"writes": "first_frame_path"}, seed=1, prompt="p", refs=[])
    set_task_state(store, t, "queued", external_id="p1")
    set_task_state(store, t, "done", result_path="frames/new.png", result_sha256="x")
    page = client.get(f"/episode/{sample['episode']}/frames").text
    assert f'href="/task/{t}"' in page and 'href="/trace/product?path=frames/new.png"' in page


def test_params_show_chinese_as_is_and_timeline_cells_do_not_wrap(client, store, sample):
    """真数据上看出来的(2026-10-09):tojson 把中文转成 Unicode 转义;时间线「动作」「类型」列被挤成一字一行。"""
    t = create_task(store, kind="align", target_type="segment", target_id=sample["seg"], model="m",
                    params={"lines": ["这块玉佩,是我母亲留给我的。"]}, seed=0, prompt="p", refs=[])
    assert "这块玉佩,是我母亲留给我的。" in client.get(f"/task/{t}").text.split("data-task-params")[1]
    page = client.get("/trace").text
    assert page.count('class="nowrap"') >= 3


def test_export_snapshot_page_lists_the_music(client, runtime, store, two_segments):
    s = two_segments
    runtime.data_root = s["root"]
    eid = _export(store, s["root"], s)
    _with_music(s["root"], eid)
    page = client.get(f"/trace/export/{eid}").text
    assert "data-export-music" in page and "自购曲库 #42" in page and "人工提供,非 AI 生成" in page
    assert "assets/music/song.wav" in page and "1.0–9.0 秒" in page
