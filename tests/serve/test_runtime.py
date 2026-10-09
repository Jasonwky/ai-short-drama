# -*- coding: utf-8 -*-
import time

from tools.drama import repo
from tools.gen.planner import plan_first_frame
from tools.serve import journal
from tools.serve.fakes import FakeComfy, FakeTunnel, fake_backend
from tools.serve.hosts import get_host
from tools.serve.runtime import Backend, Runtime, probe


def _backend(tunnel_up=True, comfy_up=True):
    comfy = FakeComfy()
    comfy.reachable = comfy_up
    return Backend("测试", lambda h: comfy, FakeTunnel(up=tunnel_up), lambda h: None)


def test_probe_tells_tunnel_down_apart_from_comfy_down(store, host_id):
    h = get_host(store, host_id)
    assert "隧道未通" in probe(h, _backend(tunnel_up=False)).message
    st = probe(h, _backend(comfy_up=False))
    assert st.tunnel_ok and not st.comfy_ok and "ComfyUI 没响应" in st.message
    st = probe(h, _backend())
    assert st.comfy_ok and st.message.startswith("正常")


def test_no_host_is_reported_not_assumed(store, tmp_path):
    backend, _ = fake_backend()
    ok, msg = Runtime(store, tmp_path, backend).any_host_ready()
    assert not ok and "还没有登记云机" in msg


def test_tick_all_journals_and_updates_status(store, host_id, tmp_path, sample):
    for rel in ("assets/lin.png", "assets/yard.png", "assets/jade.png"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"png")
    repo.update(store, "segment", sample["seg"], first_frame_path=None)
    plan_first_frame(store, sample["seg"], seed=1)
    backend, _ = fake_backend()
    rt = Runtime(store, tmp_path, backend)
    rt.tick_all()
    assert rt.status[host_id].comfy_ok and rt.any_host_ready()[0]
    assert any("pending → queued" in line for line in journal.tail(tmp_path))


def test_background_thread_survives_and_reports_a_bad_round(store, host_id, tmp_path):
    def broken_client(host):
        raise RuntimeError("云机返回了看不懂的东西")
    rt = Runtime(store, tmp_path, Backend("测试", broken_client, FakeTunnel(), lambda h: None), poll_sec=0.05)
    rt.start()
    try:
        deadline = time.time() + 5
        while not rt.last_error and time.time() < deadline:
            time.sleep(0.05)
    finally:
        rt.stop()
    assert "云机返回了看不懂的东西" in rt.last_error
    assert any("worker 本轮异常" in line for line in journal.tail(tmp_path))


def test_start_fails_llm_runs_left_running_by_a_restart(store, tmp_path):
    from tools.llm import runs
    rid = runs.start(store, task="voice", target_type="character", target_id=1, model="m", input_sha="x",
                     llm_mode="real")
    backend, _ = fake_backend()
    rt = Runtime(store, tmp_path, backend, poll_sec=0.05)
    rt.start()
    rt.stop()
    assert runs.get(store, rid)["state"] == "failed"
    assert any("上次有 1 次 LLM 运行没跑完,已标失败" in line for line in journal.tail(tmp_path))
