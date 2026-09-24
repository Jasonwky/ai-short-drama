# -*- coding: utf-8 -*-
import json

import pytest

from tools.cut import tailframe
from tools.drama import repo
from tools.drama.tasks import tasks_for
from tools.serve.dispatch import pick_next


def test_tail_frame_becomes_next_first_frame_and_is_traceable(store, two_segments):
    s, root = two_segments, two_segments["root"]
    repo.update(store, "segment", s["seg2"], first_frame_path=None, video_path=None)
    plan = tailframe.plan_tail(store, root, s["seg1"])
    assert plan.target_id == s["seg2"] and plan.summary() == "不影响任何已生成的产物"
    tid = tailframe.confirm_tail(store, root, plan)

    seg2 = repo.get(store, "segment", s["seg2"])
    assert (root / seg2["first_frame_path"]).read_bytes()[:4] == b"\x89PNG"
    t = repo.get(store, "task", tid)
    assert (t["state"], t["kind"], t["model"], t["seed"]) == ("done", "image", "ffmpeg:last-frame", 0)
    assert json.loads(t["refs_json"]) == ["outputs/tasks/v1/clip.mp4"]      # 溯源:追得回上一段的视频
    assert t["result_path"] == seg2["first_frame_path"]
    assert pick_next(store, 1) is None                                       # 创建即完成,worker 看不到它


def test_same_video_extracts_to_the_same_file(store, two_segments):
    s, root = two_segments, two_segments["root"]
    repo.update(store, "segment", s["seg2"], video_path=None)
    a = tailframe.confirm_tail(store, root, tailframe.plan_tail(store, root, s["seg1"]))
    b = tailframe.confirm_tail(store, root, tailframe.plan_tail(store, root, s["seg1"]))
    assert repo.get(store, "task", a)["result_path"] == repo.get(store, "task", b)["result_path"]


def test_replacing_a_first_frame_under_an_existing_video_reports_the_cost(store, two_segments):
    s, root = two_segments, two_segments["root"]
    plan = tailframe.plan_tail(store, root, s["seg1"])
    assert plan.impact.videos == 1 and "重跑 1 条视频" in plan.summary()
    tailframe.confirm_tail(store, root, plan)
    seg2 = repo.get(store, "segment", s["seg2"])
    assert seg2["video_path"] is None and seg2["first_frame_path"].startswith("outputs/tailframes/")
    assert [t["kind"] for t in tasks_for(store, "segment", s["seg2"])] == ["image", "video", "image"]  # 旧版都还在


def test_refusals_say_what_to_do(store, two_segments):
    s, root = two_segments, two_segments["root"]
    with pytest.raises(ValueError, match="段落 2 是本集最后一段"):
        tailframe.plan_tail(store, root, s["seg2"])
    (root / "outputs/tasks/v1/clip.mp4").unlink()
    with pytest.raises(ValueError, match="视频文件不在本机"):
        tailframe.plan_tail(store, root, s["seg1"])
    repo.update(store, "segment", s["seg1"], video_path=None)
    with pytest.raises(ValueError, match="段落 1 还没有视频"):
        tailframe.plan_tail(store, root, s["seg1"])
