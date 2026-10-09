# -*- coding: utf-8 -*-
"""留痕:失败归类、事件归属、时间线(设计 specs/2026-10-09-操作留痕查看-design.md §三)。"""
import pytest

from tools.serve.fakes import FakeAligner, FakeComfy, sample_mp4
from tools.cut import export
from tools.drama import repo
from tools.drama.db import now
from tools.drama.tasks import create_task, record_local, set_task_state
from tools.gen.align import plan_align
from tools.gen.planner import plan_first_frame
from tools.llm import runs
from tools.serve import aligner, trace
from tools.serve.dispatch import dispatch
from tools.serve.hosts import get_host

# 真实报错原文(2026-09-29 第 4 层,outputs/accept/20260929-231234/e2e/drama.db 的 task.error;验收库被 gitignore,抄在这里)
REAL_OOM = ("云机对齐脚本失败(退出码 3):ess 1307 has 19.58 GiB memory in use. Including non-PyTorch memory, "
            "this process has 3.85 GiB memory in use. Of the allocated memory 3.42 GiB is allocated by PyTorch")


@pytest.fixture
def root(tmp_path, sample):
    for rel in ("assets/lin.png", "assets/yard.png", "assets/jade.png", "frames/s1.png"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"png")
    return tmp_path


def _dispatch(store, host_id, root, tid, comfy):
    return dispatch(store, comfy, get_host(store, host_id), repo.get(store, "task", tid), data_root=root,
                    run=lambda a, s: (0, ""))


def test_failure_keys_group_real_messages_from_the_code(store, sample, host_id, root):
    """样本是代码真实失败路径现场产生的报错 + 1 条真实原文;同类归一类、不同类不合并。"""
    errors = []
    for reason in ("unet_name not in list", "Required input is missing: images"):
        comfy = FakeComfy()
        comfy.reject_next_submit = reason
        errors.append(_dispatch(store, host_id, root, plan_first_frame(store, sample["seg"], seed=1), comfy).message)
    (root / "assets/jade.png").unlink()
    errors.append(_dispatch(store, host_id, root, plan_first_frame(store, sample["seg"], seed=2), FakeComfy()).message)
    (root / "videos").mkdir()
    (root / "videos/s1.mp4").write_bytes(sample_mp4())
    errors.append(aligner.run(store, FakeComfy(), get_host(store, host_id),
                              repo.get(store, "task", plan_align(store, sample["seg"])), data_root=root,
                              run=FakeAligner("今天天气真不错")).message)
    rid = runs.start(store, task="voice", target_type="character", target_id=sample["lin"], model="m", input_sha="x",
                     llm_mode="real")
    runs.fail_stale(store)
    errors.append(runs.get(store, rid)["error"])
    errors.append(REAL_OOM)

    keys = [trace.failure_key(e) for e in errors]
    assert keys[0] == keys[1] == "ComfyUI 拒收"
    assert keys[2:] == ["参考图不存在", "判卷不过", "控制台重启时仍在运行,这次运行中断了", "云机对齐脚本失败(退出码 #)"]


def test_failure_key_replaces_windows_paths_before_cutting():
    """先换路径再截断:否则会在盘符的冒号处断掉。"""
    assert trace.failure_key(r"D:\work\x.png 不存在:请重新上传") == "<路径> 不存在"
    assert trace.failure_key("视频不存在 —— outputs/tasks/12/a.mp4") == "视频不存在"
    assert trace.failure_key("") == "(没有写原因)"


def _second_drama(store):
    d = repo.create_drama(store, "别的剧", "9:16")
    return d, repo.add_character(store, d, "沈一", turnaround_path="assets/other.png")


def test_timeline_events_use_only_recorded_times(store, sample, host_id, root):
    done = plan_first_frame(store, sample["seg"], seed=1)
    set_task_state(store, done, "queued", external_id="p1", host_id=host_id)
    set_task_state(store, done, "running")
    set_task_state(store, done, "done", result_path="frames/new.png", result_sha256="x")
    quick = plan_first_frame(store, sample["seg"], seed=2)          # 两次轮询之间就跑完:没有开跑时刻
    set_task_state(store, quick, "queued", external_id="p2", host_id=host_id)
    set_task_state(store, quick, "failed", error="ComfyUI 执行失败:OOM")
    local = record_local(store, kind="image", target_type="segment", target_id=sample["seg"], model="ffmpeg:last-frame",
                         params={"writes": "first_frame_path"}, prompt="", refs=[], result_path="frames/t.png",
                         result_sha256="y")
    ok = runs.start(store, task="voice", target_type="character", target_id=sample["lin"], model="m", input_sha="a",
                    llm_mode="real")
    runs.propose(store, ok, {"timbre": "清亮"}, "j")
    runs.approve(store, ok)
    bad = runs.start(store, task="storyboard", target_type="episode", target_id=sample["episode"], model="m",
                     input_sha="b", llm_mode="real")
    runs.fail(store, bad, "GateFailed: 4 轮都没过判卷")
    with store.write() as c:
        c.execute("INSERT INTO workflow_preset (category, version, diff_json, created_at) VALUES ('frame', 1, '{}', ?)",
                  (now(),))

    events, total = trace.timeline(store, trace.Filter())
    got = {(e.kind, e.ref, e.action) for e in events}
    assert {("task", done, "创建"), ("task", done, "开跑"), ("task", done, "完成")} <= got
    assert ("task", quick, "开跑") not in got and ("task", quick, "失败") in got
    assert [e.action for e in events if e.kind == "task" and e.ref == local] == ["本机生成"]      # 三个时刻相同,合并
    assert {("llm", ok, "发起"), ("llm", ok, "批准"), ("llm", bad, "失败")} <= got
    assert ("llm", bad, "发起") not in got        # 失败没有单独时间戳:记在发起时刻,只出一条
    preset = next(e for e in events if e.kind == "preset")
    assert preset.is_global and preset.drama_id is None
    assert total == len(events) and [e.at for e in events] == sorted((e.at for e in events), reverse=True)
    failed = next(e for e in events if e.ref == quick and e.action == "失败")
    assert failed.failed and failed.error == "ComfyUI 执行失败:OOM" and failed.href == f"/task/{quick}"


def test_filters_by_drama_episode_host_kind_and_failure(store, sample, host_id):
    mine = plan_first_frame(store, sample["seg"], seed=1)
    d2, shen2 = _second_drama(store)
    other = create_task(store, kind="image", target_type="character", target_id=shen2, model="m", params={},
                        seed=1, prompt="p", refs=[])
    set_task_state(store, other, "failed", error="参考图不存在:x")
    with store.write() as c:
        c.execute("INSERT INTO workflow_preset (category, version, diff_json, created_at) VALUES ('video', 1, '{}', ?)",
                  (now(),))

    refs = lambda f: {(e.kind, e.ref) for e in trace.timeline(store, f)[0]}   # noqa: E731
    assert ("task", other) not in refs(trace.Filter(drama_id=sample["drama"]))
    assert ("task", mine) in refs(trace.Filter(drama_id=sample["drama"]))
    assert any(k == "preset" for k, _ in refs(trace.Filter(drama_id=d2)))           # 全局事件哪部剧都显示
    assert ("task", mine) in refs(trace.Filter(episode_id=sample["episode"]))
    assert refs(trace.Filter(episode_id=sample["episode"], kinds=("preset",))) == set()
    assert refs(trace.Filter(failed_only=True)) == {("task", other)}
    set_task_state(store, mine, "queued", external_id="p1", host_id=host_id)
    assert {k for k, _ in refs(trace.Filter(host_id=host_id))} == {"task"}          # 只有任务经云机
    assert ("task", other) not in refs(trace.Filter(host_id=host_id))
    stamp = repo.get(store, "task", mine)["created_at"]
    assert refs(trace.Filter(until="2000-01-01T00:00:00+00:00")) == set()
    assert ("task", mine) in refs(trace.Filter(since=stamp))


def test_tasks_of_deleted_segments_are_traced_back_to_their_drama(store, sample, root):
    """重拆分镜删段落、保留生成历史:顺着参考图追到角色 / 场景 / 道具(它们不会被删)定剧;追不到的不硬塞。"""
    kept = plan_first_frame(store, sample["seg"], seed=1)                # 参考图里有 assets/lin.png(林小雨的正面图)
    orphan = create_task(store, kind="image", target_type="segment", target_id=sample["seg"], model="m",
                         params={}, seed=1, prompt="p", refs=["somewhere/else.png"])
    repo.delete_segments(store, sample["episode"])
    assert trace.attribute(store, "task", kept) == (sample["drama"], None)          # 集定不了,不猜
    assert trace.attribute(store, "task", orphan) == (None, None)
    in_drama = {e.ref for e in trace.timeline(store, trace.Filter(drama_id=sample["drama"]))[0] if e.kind == "task"}
    assert kept in in_drama and orphan not in in_drama
    everything = trace.timeline(store, trace.Filter())[0]
    assert next(e for e in everything if e.ref == orphan).unattributed


def test_failures_are_grouped_with_counts_and_latest(store, sample):
    for i, msg in enumerate(["ComfyUI 拒收:/prompt 返回 HTTP 400:a", "ComfyUI 拒收:/prompt 返回 HTTP 400:b",
                             "参考图不存在:x"]):
        t = create_task(store, kind="image", target_type="character", target_id=sample["lin"], model="m",
                        params={}, seed=i, prompt="p", refs=[])
        set_task_state(store, t, "failed", error=msg)
    rid = runs.start(store, task="voice", target_type="character", target_id=sample["lin"], model="m", input_sha="x",
                     llm_mode="real")
    runs.fail(store, rid, "GateFailed: 3 轮都没过判卷")
    groups = trace.failures(store, trace.Filter(drama_id=sample["drama"]))
    assert [(g.key, g.count) for g in groups] == [("ComfyUI 拒收", 2), ("GateFailed", 1), ("参考图不存在", 1)]
    assert groups[0].latest.error.endswith(":b") and len(groups[0].events) == 2


def test_pages_of_100(store, sample):
    for i in range(130):
        create_task(store, kind="image", target_type="character", target_id=sample["lin"], model="m",
                    params={}, seed=i, prompt="p", refs=[])
    first, total = trace.timeline(store, trace.Filter(), page=1)
    second, _ = trace.timeline(store, trace.Filter(), page=2)
    assert (len(first), len(second), total) == (100, 30, 130)
