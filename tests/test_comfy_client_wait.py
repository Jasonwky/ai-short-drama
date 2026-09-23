# -*- coding: utf-8 -*-
"""ComfyClient.wait 的断线容忍:闸门 0-4 真机上一次瞬时抖动让执行器崩掉、产物成了孤儿。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.gen import comfy_client as cc  # noqa: E402


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(cc.time, "time", c.time)
    monkeypatch.setattr(cc.time, "sleep", c.sleep)
    return c


def make_client(script):
    """script: 每次 history() 调用依次返回/抛出的东西。"""
    client = cc.ComfyClient("x:1")
    it = iter(script)

    def history(pid):
        item = next(it)
        if isinstance(item, Exception):
            raise item
        return item

    client.history = history
    client.queue_counts = lambda: (1, 0)
    return client


def test_transient_disconnect_does_not_abandon_task(clock):
    done = {"status": {"status_str": "success", "completed": True}, "outputs": {}}
    client = make_client([cc.ComfyUnreachable("reset"), cc.ComfyUnreachable("timeout"), None, done])
    assert client.wait("p", poll_sec=5) is done


def test_disconnect_beyond_budget_fails_loud_and_says_how_to_recover(clock):
    client = make_client([cc.ComfyUnreachable("down")] * 1000)
    with pytest.raises(cc.ComfyUnreachable, match="接管"):
        client.wait("p", poll_sec=30)
    assert clock.t > cc.UNREACHABLE_BUDGET_SEC


def test_transient_socket_errors_become_unreachable(monkeypatch):
    def boom(*a, **k):
        raise TimeoutError("read timed out")
    monkeypatch.setattr(cc.urllib.request, "urlopen", boom)
    with pytest.raises(cc.ComfyUnreachable, match="TimeoutError"):
        cc.ComfyClient("x:1").system_stats()


def test_history_does_not_swallow_unreachable_as_none(monkeypatch):
    """「查不到」和「连不上」是两回事 —— 以前 history() 把连不上吞成 None,会被误读成还没跑完。"""
    client = cc.ComfyClient("x:1")
    def req(path, payload=None):
        raise cc.ComfyUnreachable("down")
    client._request = req
    with pytest.raises(cc.ComfyUnreachable):
        client.history("p")
