# -*- coding: utf-8 -*-
"""审阅页批准重拆分镜:先报代价(删几个段落、作废多少产物),确认才删旧建新。"""
from tests.llm.test_storyboard import _episode, good_args
from tools.drama import repo
from tools.llm import runs, service, storyboard
from tools.llm.fake import FakeLLM
from tools.llm.service import LLMBackend


def test_approving_a_resplit_shows_the_cost_first(client, store, tmp_path):
    ep = _episode(store)
    old = repo.add_segment(store, ep, 1, description="旧段落", duration_sec=8.0, first_frame_path="f.png",
                           video_path="v.mp4")
    llm = LLMBackend(FakeLLM({storyboard.TOOL: good_args()}), "m", "real")
    rid, _ = service.begin(store, "storyboard", ep, {}, llm)
    service.finish(store, rid, llm, tmp_path)

    page = client.get(f"/episode/{ep}/storyboard").text
    assert "LLM 重拆分镜" in page and "批准时会先删除它们" in page

    r = client.post(f"/llm/run/{rid}/approve")
    assert r.status_code == 200 and "批准会删除本集已有的 1 个段落" in r.text and "重跑 1 条视频、1 张参考帧" in r.text
    assert runs.get(store, rid)["state"] == "proposed"                            # 还没批

    r = client.post(f"/llm/run/{rid}/approve", data={"confirm": "1"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/episode/{ep}/storyboard"
    ids = [x[0] for x in store.conn.execute("SELECT id FROM segment WHERE episode_id = ?", (ep,))]
    assert len(ids) == 4 and old not in ids


def test_approving_into_an_empty_episode_needs_no_confirm(client, store, tmp_path):
    ep = _episode(store)
    llm = LLMBackend(FakeLLM({storyboard.TOOL: good_args()}), "m", "real")
    rid, _ = service.begin(store, "storyboard", ep, {}, llm)
    service.finish(store, rid, llm, tmp_path)
    assert client.post(f"/llm/run/{rid}/approve", follow_redirects=False).status_code == 303
