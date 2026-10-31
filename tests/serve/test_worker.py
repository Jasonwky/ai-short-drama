# -*- coding: utf-8 -*-
import pytest

from tools.drama import repo
from tools.gen.planner import plan_first_frame, plan_video
from tools.serve.fakes import FakeComfy, FakeTunnel
from tools.serve.hosts import set_ssh_port
from tools.serve.worker import Worker, main
from tests.drama.conftest import give_line_audio


@pytest.fixture(autouse=True)
def _line_audio(request):
    """视频规划要台词音频(spec 2026-10-30 第 5 节):用到样例的测试,给段落补一条已合成的。"""
    if "sample" in request.fixturenames:
        give_line_audio(request.getfixturevalue("store"), request.getfixturevalue("sample")["seg"])


def _no_ssh(host):
    def run(args, stdin):
        raise AssertionError("不该触发远端预检")
    return run


@pytest.fixture
def world(store, sample, host_id, tmp_path):
    for rel in ("audio/lines_1.wav", "assets/lin.png", "assets/lin_4v.png", "assets/yard.png", "assets/jade.png"):
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
    assert comfy.uploaded[-3].endswith(f"outputs/tasks/{frame}/frame.png")   # 视频吃的是刚出的首帧(之后是四视图、台词音频)
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


def test_queue_depth_keeps_several_tasks_in_the_comfy_queue(store, sample, world, host_id):
    """v22:共用云机被别人插队时调大深度,在途少于深度就接着交(用户 2026-10-28 定)。"""
    from tools.serve.hosts import set_queue_depth
    w, comfy, _ = world
    set_queue_depth(store, host_id, 2)
    a, b, c = (plan_first_frame(store, sample["seg"], seed=n) for n in (1, 2, 3))
    w.tick()
    assert [_state(store, t) for t in (a, b, c)] == ["queued", "queued", "pending"] and len(comfy.submitted) == 2
    comfy.finish("p1", "a.png")
    w.tick()
    assert [_state(store, t) for t in (a, b, c)] == ["done", "queued", "queued"]
    with pytest.raises(ValueError):
        set_queue_depth(store, host_id, 0)


def test_voice_tasks_run_through_the_tts_and_align_runners(store, sample, host_id, tmp_path):
    from tools.gen.voice import plan_voice
    from tools.serve.fakes import FakeAligner, FakeVoice
    tts, asr, comfy = FakeVoice(), FakeAligner("这块玉佩是我母亲留给我的"), FakeComfy()
    w = Worker(store, host_id, data_root=tmp_path, client_for=lambda h: comfy, tunnel=FakeTunnel(), run_for=_no_ssh,
               align_for=lambda h: asr, tts_for=lambda h: tts, idx_for=lambda h: tts)
    tid = plan_voice(store, sample["lin"], seed=1)
    w.tick()
    assert repo.get(store, "task", tid)["state"] == "done" and len(tts.calls) == 1 and len(asr.calls) == 1


def test_voice_tasks_wait_while_our_own_tasks_are_in_flight(store, sample, host_id, tmp_path):
    """试听音同步跑、占显存:机器上还有我们在途的任务时不挑它(与字幕对齐同规则)。"""
    from tools.drama.tasks import set_task_state
    from tools.gen.voice import plan_voice
    from tools.serve.dispatch import pick_next
    first = plan_voice(store, sample["lin"], seed=1)
    set_task_state(store, first, "queued", external_id="x", host_id=host_id)
    plan_voice(store, sample["lin"], seed=2)
    assert pick_next(store, host_id, allow_align=False) is None


def test_tts_tasks_run_through_the_tts_and_align_runners(store, sample, host_id, tmp_path):
    from tools.gen.tts import plan_tts
    from tools.serve.fakes import FakeAligner, fake_tts_runner
    (tmp_path / "v").mkdir()
    (tmp_path / "v/lin.wav").write_bytes(b"RIFF")
    repo.update(store, "character", sample["lin"], voice_ref_path="v/lin.wav", voice_ref_text="试听句")
    store.conn.execute("INSERT INTO asset_approval (table_name, row_id, slot, path, sha256, created_at)"
                       " VALUES ('character', ?, 'voice', 'v/lin.wav', 'x', 't')", (sample["lin"],))
    from tests.drama.conftest import give_voice_template
    give_voice_template(store, sample["lin"], tmp_path)
    tts, asr, comfy = fake_tts_runner(), FakeAligner("这块玉佩是我母亲留给我的"), FakeComfy()
    w = Worker(store, host_id, data_root=tmp_path, client_for=lambda h: comfy, tunnel=FakeTunnel(), run_for=_no_ssh,
               align_for=lambda h: asr, tts_for=lambda h: tts, idx_for=lambda h: tts)
    tid = plan_tts(store, sample["seg"], seed=1)
    w.tick()
    assert repo.get(store, "task", tid)["state"] == "done" and len(tts.lines.calls) == 1 and tts.voice.calls == []


def test_new_engine_line_audio_uses_the_indextts_interpreter(store, sample, host_id, tmp_path):
    """IndexTTS2 港片念法(spec 2026-10-31 §4):新引擎台词任务走 idx_for(indextts venv),不走 qwen-tts 的 tts_for。"""
    from tests.drama.conftest import give_voice_template
    from tools.gen.tts import plan_tts
    from tools.serve.fakes import FakeAligner, fake_tts_runner
    (tmp_path / "v").mkdir()
    (tmp_path / "v/lin.wav").write_bytes(b"RIFF")
    repo.update(store, "character", sample["lin"], voice_ref_path="v/lin.wav", voice_ref_text="试听句")
    store.conn.execute("INSERT INTO asset_approval (table_name, row_id, slot, path, sha256, created_at)"
                       " VALUES ('character', ?, 'voice', 'v/lin.wav', 'x', 't')", (sample["lin"],))
    give_voice_template(store, sample["lin"], tmp_path)
    qwen, idx = fake_tts_runner(), fake_tts_runner()
    w = Worker(store, host_id, data_root=tmp_path, client_for=lambda h: FakeComfy(), tunnel=FakeTunnel(), run_for=_no_ssh,
               align_for=lambda h: FakeAligner("这块玉佩是我母亲留给我的"), tts_for=lambda h: qwen, idx_for=lambda h: idx)
    tid = plan_tts(store, sample["seg"], seed=1)
    w.tick()
    assert repo.get(store, "task", tid)["state"] == "done"
    assert len(idx.lines.calls) == 1 and qwen.lines.calls == [] and qwen.voice.calls == []
