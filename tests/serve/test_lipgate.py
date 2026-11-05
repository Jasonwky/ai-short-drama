# -*- coding: utf-8 -*-
"""口型闸门(spec 2026-10-30 第 6.3、6.4 节;用户 2026-10-30:画外音不算合格,不过就自动换种子重出)。"""
import json
import math

import pytest

from tests.drama.conftest import give_line_audio
from tools.cut import lipsync
from tools.drama import repo
from tools.drama.tasks import set_task_state
from tools.gen.planner import ON_CAMERA, plan_video
from tools.serve import lipgate

ENV = [0.05 + 0.04 * math.sin(2 * math.pi * t / 6) for t in range(96)]
TALKING = (24.0, [[(0.4, 0.02 + 0.5 * e)] for e in ENV], ENV)
SILENT_FACE = (24.0, [[(0.4, 0.01)] for _ in ENV], ENV)       # 镜头里只有一张不动的嘴 = 说话人是画外音


@pytest.fixture
def world(store, sample, tmp_path):
    give_line_audio(store, sample["seg"])
    audio = repo.get(store, "segment", sample["seg"])["line_audio_path"]
    (tmp_path / audio).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / audio).with_name("tts.json").write_text(json.dumps({"spans": [[0.3, 3.5]]}), encoding="utf-8")
    return {"root": tmp_path, "seg": sample["seg"]}


def _video(store, world, n, **plan):
    tid = plan_video(store, world["seg"], seed=n, **plan)
    rel = f"outputs/tasks/{tid}/h3.mp4"
    (world["root"] / rel).parent.mkdir(parents=True, exist_ok=True)
    (world["root"] / rel).write_bytes(b"mp4-%d" % tid)
    set_task_state(store, tid, "queued", external_id="x")
    set_task_state(store, tid, "done", result_path=rel)
    return repo.get(store, "task", tid)


def _videos(store, seg):
    return store.conn.execute("SELECT * FROM task WHERE kind = 'video' AND target_id = ? ORDER BY id", (seg,)).fetchall()


def test_passing_lips_do_not_retry(store, world, monkeypatch):
    monkeypatch.setattr(lipsync, "measure", lambda path: TALKING)
    log = lipgate.after_video(store, world["root"], _video(store, world, 1))
    assert "第 1 句 过" in log[0] and len(_videos(store, world["seg"])) == 1


def test_failing_lips_retry_with_the_speaker_on_camera_at_most_twice(store, world, monkeypatch):
    monkeypatch.setattr(lipsync, "measure", lambda path: SILENT_FACE)
    first = _video(store, world, 1)
    log = lipgate.after_video(store, world["root"], first)
    assert "自动重出" in log[-1] and "第 1 / 2 次" in log[-1]
    retry = _videos(store, world["seg"])[-1]
    p = json.loads(retry["params_json"])
    assert ON_CAMERA in retry["prompt"] and p["lip_retry"] == 1 and retry["seed"] != first["seed"]
    assert p["script"]["line_audio"] == json.loads(first["params_json"])["script"]["line_audio"]   # 同一份台词音频
    set_task_state(store, retry["id"], "queued", external_id="y")
    set_task_state(store, retry["id"], "done", result_path=first["result_path"].replace(str(first["id"]), str(retry["id"])))
    (world["root"] / repo.get(store, "task", retry["id"])["result_path"]).parent.mkdir(parents=True, exist_ok=True)
    (world["root"] / repo.get(store, "task", retry["id"])["result_path"]).write_bytes(b"retry")
    assert "第 2 / 2 次" in lipgate.after_video(store, world["root"], repo.get(store, "task", retry["id"]))[-1]
    third = _videos(store, world["seg"])[-1]
    set_task_state(store, third["id"], "queued", external_id="z")
    rel = f"outputs/tasks/{third['id']}/h3.mp4"
    (world["root"] / rel).parent.mkdir(parents=True, exist_ok=True)
    (world["root"] / rel).write_bytes(b"third")
    set_task_state(store, third["id"], "done", result_path=rel)
    log = lipgate.after_video(store, world["root"], repo.get(store, "task", third["id"]))
    assert "停下交给人看" in log[-1] and len(_videos(store, world["seg"])) == 3


def test_retry_of_a_draft_stays_a_draft(store, world, monkeypatch):
    """2026-11-05 acceptance: a 10-step draft's lip retry went out at 20 steps (params had no `steps`)."""
    from tools.gen.planner import DRAFT_STEPS
    monkeypatch.setattr(lipsync, "measure", lambda path: SILENT_FACE)
    lipgate.after_video(store, world["root"], _video(store, world, 1, steps=DRAFT_STEPS))
    assert json.loads(_videos(store, world["seg"])[-1]["params_json"])["steps"] == DRAFT_STEPS


def test_new_line_audio_gets_its_own_retries(store, world, monkeypatch):
    monkeypatch.setattr(lipsync, "measure", lambda path: SILENT_FACE)
    _video(store, world, 1, lip_retry=True)
    _video(store, world, 2, lip_retry=True)
    sha =json.loads(_videos(store, world["seg"])[0]["params_json"])["script"]["line_audio"]["sha256"]
    assert lipgate.retries_used(store, world["seg"], sha) == 2
    assert lipgate.retries_used(store, world["seg"], "another-audio") == 0


def test_faceless_lines_cannot_be_judged_and_do_not_retry(store, world):
    log = lipgate.after_video(store, world["root"], _video(store, world, 1))     # conftest 默认:画面里没有脸
    assert "无法判定" in log[0] and len(_videos(store, world["seg"])) == 1


def test_export_problem_follows_the_current_video(store, world, monkeypatch):
    v = _video(store, world, 1)
    repo.update(store, "segment", world["seg"], video_path=v["result_path"])
    seg = lambda: repo.get(store, "segment", world["seg"])   # noqa: E731
    assert "还没量口型" in lipgate.problem(store, world["root"], seg(), v)
    monkeypatch.setattr(lipsync, "measure", lambda path: SILENT_FACE)
    lipsync.check(world["root"], v["result_path"], [[0.3, 3.5]])
    assert "口型不过" in lipgate.problem(store, world["root"], seg(), v)
    (world["root"] / v["result_path"]).write_bytes(b"other")               # 换了片(sha 变了)缓存不认
    monkeypatch.setattr(lipsync, "measure", lambda path: TALKING)
    lipsync.check(world["root"], v["result_path"], [[0.3, 3.5]])
    assert lipgate.problem(store, world["root"], seg(), v) == ""


# ── 人工看片放行(用户 2026-11-03 定;spec 第 6.5 节)────────────

def test_human_release_clears_the_export_problem_only_for_that_video(store, world, monkeypatch):
    v = _video(store, world, 1)
    repo.update(store, "segment", world["seg"], video_path=v["result_path"])
    seg = lambda: repo.get(store, "segment", world["seg"])   # noqa: E731
    with pytest.raises(ValueError, match="不用放行"):                          # 还没量过:没什么可放行
        lipgate.confirm(store, world["root"], world["seg"], "看过了")
    monkeypatch.setattr(lipsync, "measure", lambda path: SILENT_FACE)
    lipsync.check(world["root"], v["result_path"], [[0.3, 3.5]])
    with pytest.raises(ValueError, match="★必填"):
        lipgate.confirm(store, world["root"], world["seg"], "  ")
    oid = lipgate.confirm(store, world["root"], world["seg"], "侧脸说话,看片嘴型对得上")
    assert lipgate.problem(store, world["root"], seg(), v) == ""
    row = repo.get(store, "lip_override", oid)
    assert json.loads(row["lines_json"])[0]["n"] == 1 and row["reason"] == "侧脸说话,看片嘴型对得上"
    note = lipsync.override_note(seg()["number"], lipsync.override_snapshot(row))
    assert "第 1 句" in note and "侧脸说话" in note
    (world["root"] / v["result_path"]).write_bytes(b"other")                 # 换了片:放行失效,重新量、重新拦
    lipsync.check(world["root"], v["result_path"], [[0.3, 3.5]])
    assert "口型不过" in lipgate.problem(store, world["root"], seg(), v)
    assert lipgate.override_for(store, world["root"], seg()) is None


def test_passing_lips_need_no_release(store, world, monkeypatch):
    v = _video(store, world, 1)
    repo.update(store, "segment", world["seg"], video_path=v["result_path"])
    monkeypatch.setattr(lipsync, "measure", lambda path: TALKING)
    lipsync.check(world["root"], v["result_path"], [[0.3, 3.5]])
    with pytest.raises(ValueError, match="不用放行"):
        lipgate.confirm(store, world["root"], world["seg"], "看过了")
