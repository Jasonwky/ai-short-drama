# -*- coding: utf-8 -*-
from tools.serve.hosts import get_host
from tools.serve.tunnel import BACKOFF_CAP, TunnelManager, command_for


class FakeProc:
    def __init__(self):
        self.alive = True
        self.terminated = False

    def poll(self):
        return None if self.alive else 255

    def terminate(self):
        self.terminated = True
        self.alive = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.alive = False


class World:
    """可控的外部世界:端口转没转发、时钟、拉起过哪些进程。"""
    def __init__(self):
        self.forwarding = False
        self.t = 0.0
        self.spawned = []

    def spawn(self, cmd):
        p = FakeProc()
        self.spawned.append((cmd, p))
        return p

    def manager(self):
        return TunnelManager(spawn=self.spawn, forwarding=lambda h: self.forwarding, clock=lambda: self.t)


def test_command_forwards_local_port_to_remote_comfy(store, host_id):
    cmd = command_for(get_host(store, host_id))
    assert cmd[:2] == ["ssh", "-N"]
    assert "ExitOnForwardFailure=yes" in cmd and "BatchMode=yes" in cmd
    assert cmd[cmd.index("-L") + 1] == "8188:127.0.0.1:8188"
    assert cmd[cmd.index("-p") + 1] == "14138"
    assert cmd[-1] == "root@connect.example.com"


def test_existing_forward_is_adopted_not_duplicated(store, host_id):
    w = World()
    w.forwarding = True   # 控制面重启前起的那条 ssh 还在转发
    m = w.manager()
    assert m.ensure(get_host(store, host_id)) is True
    assert w.spawned == [] and m.owns(host_id) is False


def test_spawns_once_and_waits_for_it(store, host_id):
    w = World()
    m = w.manager()
    h = get_host(store, host_id)
    assert m.ensure(h) is False and len(w.spawned) == 1
    assert m.ensure(h) is False and len(w.spawned) == 1   # 它还活着,只是还没通
    w.forwarding = True
    assert m.ensure(h) is True and m.owns(host_id)


def test_backoff_doubles_and_caps_while_host_is_down(store, host_id):
    w = World()
    m = w.manager()
    h = get_host(store, host_id)
    spawn_times = []
    for sec in range(1500):                 # 模拟 1500 秒,云机一直不可达
        w.t = sec
        for _, p in w.spawned:
            p.alive = False                 # 连不上的 ssh 很快就退出
        before = len(w.spawned)
        m.ensure(h)
        if len(w.spawned) > before:
            spawn_times.append(sec)
    gaps = [b - a for a, b in zip(spawn_times, spawn_times[1:])]
    assert gaps[:6] == [5, 10, 20, 40, 80, 160]
    assert set(gaps[6:]) == {BACKOFF_CAP}


def test_recovery_resets_backoff(store, host_id):
    w = World()
    m = w.manager()
    h = get_host(store, host_id)
    m.ensure(h)
    w.forwarding = True
    m.ensure(h)
    w.forwarding = False
    w.spawned[-1][1].alive = False
    m.ensure(h)              # 恢复后第一次掉线应立刻重拉,而不是沿用旧的退避
    assert len(w.spawned) == 2


def test_stop_terminates_our_own_tunnel(store, host_id):
    w = World()
    m = w.manager()
    m.ensure(get_host(store, host_id))
    proc = w.spawned[0][1]
    m.stop(host_id)
    assert proc.terminated and not m.owns(host_id)
