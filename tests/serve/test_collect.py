# -*- coding: utf-8 -*-
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from tests.serve.fakes import FakeComfy
from tools.drama import repo
from tools.drama.tasks import create_task, set_task_state
from tools.gen.comfy_client import ComfyUnreachable
from tools.serve.collect import GRACE_SEC, Collector

T0 = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


@pytest.fixture
def inflight(store, sample, host_id):
    """一条已交给云机(p1)的首帧任务;段落的首帧位是空的。"""
    repo.update(store, "segment", sample["seg"], first_frame_path=None)
    comfy = FakeComfy()
    pid = comfy.submit({})
    tid = create_task(store, kind="image", target_type="segment", target_id=sample["seg"], model="q",
                      params={"writes": "first_frame_path", "expect_ext": ".png"}, seed=1, prompt="p", refs=[])
    set_task_state(store, tid, "queued", external_id=pid, host_id=host_id)
    return tid, pid, comfy


def _t(store, tid):
    return repo.get(store, "task", tid)


def test_queued_then_running_then_done_writes_back(store, sample, inflight, tmp_path):
    tid, pid, comfy = inflight
    c = Collector(tmp_path, Clock())
    assert c.advance(store, comfy, _t(store, tid)).state == "queued"
    comfy.start(pid)
    assert c.advance(store, comfy, _t(store, tid)).state == "running"
    comfy.finish(pid, "ComfyUI_00001_.png", b"frame-bytes")
    out = c.advance(store, comfy, _t(store, tid))
    t = _t(store, tid)
    assert out.state == "done" and t["result_path"] == f"outputs/tasks/{tid}/ComfyUI_00001_.png"
    assert t["result_sha256"] == hashlib.sha256(b"frame-bytes").hexdigest()
    assert (tmp_path / t["result_path"]).read_bytes() == b"frame-bytes"
    assert repo.get(store, "segment", sample["seg"])["first_frame_path"] == t["result_path"]


def test_existing_output_is_not_overwritten(store, sample, inflight, tmp_path):
    tid, pid, comfy = inflight
    repo.update(store, "segment", sample["seg"], first_frame_path="frames/picked.png")
    comfy.finish(pid, "new.png")
    out = Collector(tmp_path, Clock()).advance(store, comfy, _t(store, tid))
    assert out.state == "done" and "待人挑选" in out.message
    assert repo.get(store, "segment", sample["seg"])["first_frame_path"] == "frames/picked.png"


def test_execution_error_fails_with_comfy_message(store, inflight, tmp_path):
    tid, pid, comfy = inflight
    comfy.fail(pid, "CUDA out of memory")
    Collector(tmp_path, Clock()).advance(store, comfy, _t(store, tid))
    t = _t(store, tid)
    assert t["state"] == "failed" and "CUDA out of memory" in t["error"]


def test_wrong_output_type_fails(store, inflight, tmp_path):
    tid, pid, comfy = inflight
    comfy.finish(pid, "preview.webp")
    Collector(tmp_path, Clock()).advance(store, comfy, _t(store, tid))
    assert "期望恰好 1 个 .png" in _t(store, tid)["error"]


def test_vanished_task_fails_only_after_grace(store, inflight, tmp_path):
    tid, pid, comfy = inflight
    clock = Clock()
    c = Collector(tmp_path, clock)
    comfy.vanish(pid)
    assert c.advance(store, comfy, _t(store, tid)).state == "queued"      # 先观察,history 可能滞后
    clock.t += timedelta(seconds=GRACE_SEC - 1)
    assert c.advance(store, comfy, _t(store, tid)).state == "queued"
    clock.t += timedelta(seconds=2)
    assert c.advance(store, comfy, _t(store, tid)).state == "failed"
    assert "找不到任务" in _t(store, tid)["error"]


def test_late_history_within_grace_still_completes(store, inflight, tmp_path):
    tid, pid, comfy = inflight
    c = Collector(tmp_path, Clock())
    comfy.vanish(pid)
    c.advance(store, comfy, _t(store, tid))
    comfy.finish(pid, "late.png")
    assert c.advance(store, comfy, _t(store, tid)).state == "done"


def test_unreachable_waits_then_resumes_without_resubmitting(store, inflight, tmp_path):
    tid, pid, comfy = inflight
    c = Collector(tmp_path, Clock())
    comfy.reachable = False
    assert c.advance(store, comfy, _t(store, tid)).state == "waiting_host"
    comfy.reachable = True
    comfy.start(pid)
    assert c.advance(store, comfy, _t(store, tid)).state == "running"
    comfy.finish(pid, "back.png")
    assert c.advance(store, comfy, _t(store, tid)).state == "done"
    assert list(comfy.submitted) == [pid]          # 从头到尾只交过一次


def test_takeover_after_restart_uses_external_id(store, inflight, tmp_path):
    tid, pid, comfy = inflight
    comfy.start(pid)
    Collector(tmp_path, Clock()).advance(store, comfy, _t(store, tid))
    comfy.finish(pid, "taken.png")
    fresh = Collector(tmp_path, Clock())            # 控制面重启:新进程、无任何内存状态
    assert fresh.advance(store, comfy, _t(store, tid)).state == "done"


def test_host_gone_for_a_day_gives_up(store, inflight, tmp_path, monkeypatch):
    tid, pid, comfy = inflight
    clock = Clock()
    c = Collector(tmp_path, clock)
    comfy.reachable = False
    monkeypatch.setattr("tools.drama.tasks.now", lambda: T0.isoformat(timespec="seconds"))
    c.advance(store, comfy, _t(store, tid))
    clock.t = T0 + timedelta(hours=23)
    assert c.advance(store, comfy, _t(store, tid)).state == "waiting_host"
    clock.t = T0 + timedelta(hours=25)
    assert c.advance(store, comfy, _t(store, tid)).state == "failed"
    assert "人工核对" in _t(store, tid)["error"]


def test_disconnect_during_fetch_goes_to_waiting_host(store, inflight, tmp_path, monkeypatch):
    tid, pid, comfy = inflight
    c = Collector(tmp_path, Clock())
    comfy.reachable = False
    c.advance(store, comfy, _t(store, tid))          # waiting_host
    comfy.reachable = True
    comfy.finish(pid, "x.png")
    def broken_fetch(item, dest):
        raise ComfyUnreachable("下载到一半断了")
    monkeypatch.setattr(comfy, "fetch", broken_fetch)
    assert c.advance(store, comfy, _t(store, tid)).state == "waiting_host"
    assert _t(store, tid)["state"] == "waiting_host"     # 库里也得是,不能只是返回值
    monkeypatch.undo()
    assert c.advance(store, comfy, _t(store, tid)).state == "done"
