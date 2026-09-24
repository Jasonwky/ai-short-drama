# -*- coding: utf-8 -*-
import pytest

from tools.drama import repo
from tools.gen.planner import plan_first_frame, plan_video
from tools.serve.fakes import FakeComfy, FakeTunnel
from tools.serve.hosts import set_ssh_port
from tools.serve.worker import Worker, main


def _no_ssh(host):
    def run(args, stdin):
        raise AssertionError("不该触发远端预检")
    return run


@pytest.fixture
def world(store, sample, host_id, tmp_path):
    for rel in ("assets/lin.png", "assets/yard.png", "assets/jade.png"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"png")
    repo.update(store, "segment", sample["seg"], first_frame_path=None, video_path=None)
    comfy, tunnel = FakeComfy(), FakeTunnel()
    w = Worker(store, host_id, data_root=tmp_path, client_for=lambda h: comfy, tunnel=tunnel, run_for=_no_ssh)
    return w, comfy, tunnel


def _state(store, tid):
    return repo.get(store, "task", tid)["state"]


def test_segment_goes_from_nothing_to_frame_and_video(store, sample, world):
    w, comfy, _ = world
    frame = plan_first_frame(store, sample["seg"], seed=11)
    w.tick()
    assert _state(store, frame) == "queued"
    comfy.finish("p1", "frame.png")
    w.tick()
    seg = repo.get(store, "segment", sample["seg"])
    assert _state(store, frame) == "done" and seg["first_frame_path"] == f"outputs/tasks/{frame}/frame.png"

    video = plan_video(store, sample["seg"], seed=12)
    w.tick()
    assert comfy.uploaded[-1].endswith(f"outputs/tasks/{frame}/frame.png")   # 视频吃的是刚出的首帧
    comfy.finish("p2", "clip.mp4")
    w.tick()
    assert repo.get(store, "segment", sample["seg"])["video_path"] == f"outputs/tasks/{video}/clip.mp4"


def test_only_one_task_in_flight_per_host(store, sample, world):
    w, comfy, _ = world
    a = plan_first_frame(store, sample["seg"], seed=1)
    b = plan_first_frame(store, sample["seg"], seed=2)
    w.tick()
    w.tick()
    assert (_state(store, a), _state(store, b)) == ("queued", "pending") and len(comfy.submitted) == 1
    comfy.finish("p1", "a.png")
    w.tick()     # 这一轮收完 a,机器空了,接着派 b
    assert (_state(store, a), _state(store, b)) == ("done", "queued")


def test_tunnel_down_parks_tasks_as_waiting_host(store, sample, world):
    w, comfy, tunnel = world
    tid = plan_first_frame(store, sample["seg"], seed=1)
    w.tick()
    tunnel.up, comfy.reachable = False, False
    log = w.tick()
    assert _state(store, tid) == "waiting_host" and any("隧道未通" in line for line in log)


def test_port_change_rebuilds_own_tunnel(store, host_id, world):
    w, _, tunnel = world
    w.tick()
    set_ssh_port(store, host_id, 22222)
    log = w.tick()
    assert tunnel.stopped == [host_id] and any("已停掉旧隧道" in line for line in log)


def test_port_change_with_adopted_tunnel_tells_the_human(store, host_id, world):
    w, _, tunnel = world
    tunnel.owned = False
    w.tick()
    set_ssh_port(store, host_id, 22222)
    assert any("请手工结束" in line for line in w.tick())


def test_cli_requires_a_mode(tmp_path):
    with pytest.raises(SystemExit):
        main(["--db", str(tmp_path / "x.db"), "--host-id", "1", "--data-root", str(tmp_path)])
