# -*- coding: utf-8 -*-
"""留痕:倒查树、LLM 来源、同目标的其它尝试、任务详情、成片快照对比(设计 §2.2–2.3、§3.5–3.6)。"""
import json

import pytest

from tests.cut.conftest import done_task, mp4_bytes, two_segments  # noqa: F401  —— 复用成片测试的生成链夹具
from tools.cut import provenance
from tools.drama import repo
from tools.drama.db import now
from tools.drama.tasks import create_task, set_task_state
from tools.llm import runs, voices
from tools.serve import journal, trace_views
from tools.serve.dispatch import SUBMITTED

VOICE = {"age": "young_adult", "gender": "female", "timbre": "清亮", "pace": "slow"}


def _approved(store, task, target_type, target_id, output):
    rid = runs.start(store, task=task, target_type=target_type, target_id=target_id, model="m", input_sha=task,
                     llm_mode="real")
    runs.propose(store, rid, output, "j")
    runs.approve(store, rid)
    return rid


def test_tree_marks_each_node(store, two_segments):
    s, root = two_segments, two_segments["root"]
    video = repo.get(store, "segment", s["seg1"])["video_path"]
    tree = trace_views.product(store, root, video)
    assert tree["status"] == "ok" and tree["task"]["kind"] == "video" and tree["target"] == "第 1 集段落 1"
    frame = tree["inputs"][0]
    assert frame["status"] == "ok" and frame["path"] == "outputs/tasks/f1/frame.png"
    (root / frame["path"]).write_bytes(b"replaced")                      # 文件被替换过
    (root / "outputs/tasks/v1/clip.mp4").unlink()                        # 文件不在本机
    tree = trace_views.product(store, root, video)
    assert tree["status"] == "missing" and tree["inputs"][0]["status"] == "mismatch"
    (root / "assets").mkdir(exist_ok=True)
    (root / "assets/lin.png").write_bytes(b"png")
    assert trace_views.product(store, root, "assets/lin.png")["status"] == "manual"


def test_paths_outside_the_data_root_are_refused(store, two_segments):
    with pytest.raises(KeyError, match="素材根"):
        trace_views.product(store, two_segments["root"], "../secret.txt")


def test_voice_source_is_proven_by_the_composed_text(store, sample):
    """批准时写回角色的是 voices.compose(输出) —— 拿它去比提示词;对上 = 确证,对不上才按时间推断。"""
    rid = _approved(store, "voice", "character", sample["lin"], VOICE)
    sb = _approved(store, "storyboard", "episode", sample["episode"], {"segments": []})
    t = create_task(store, kind="video", target_type="segment", target_id=sample["seg"], model="m", params={},
                    seed=1, prompt=f"…林小雨的声音:{voices.compose(VOICE)}。", refs=[])
    got = {(s["run_id"], s["how"]) for s in trace_views.llm_sources(store, repo.get(store, "task", t))}
    assert got == {(rid, "确证"), (sb, "按时间推断")}
    edited = create_task(store, kind="video", target_type="segment", target_id=sample["seg"], model="m", params={},
                         seed=2, prompt="…林小雨的声音:人改过的描述。", refs=[])        # 人在批准后手改过
    assert {s["how"] for s in trace_views.llm_sources(store, repo.get(store, "task", edited))
            if s["task"] == "voice"} == {"按时间推断"}                               # 对不上,退回推断


def test_only_runs_approved_before_the_task_count(store, sample):
    t = create_task(store, kind="image", target_type="prop", target_id=sample["jade"], model="m", params={},
                    seed=1, prompt="p", refs=[])
    late = _approved(store, "describe_prop", "prop", sample["jade"], {"a": 1})
    store.conn.execute("UPDATE llm_run SET decided_at = '9999-01-01T00:00:00+00:00' WHERE id = ?", (late,))  # 晚于任务
    rejected = runs.start(store, task="describe_prop", target_type="prop", target_id=sample["jade"], model="m",
                          input_sha="r", llm_mode="real")
    runs.propose(store, rejected, {"a": 1}, "j")
    runs.reject(store, rejected)
    assert trace_views.llm_sources(store, repo.get(store, "task", t)) == []
    store.conn.execute("UPDATE llm_run SET decided_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (late,))
    [src] = trace_views.llm_sources(store, repo.get(store, "task", t))
    assert src["task"] == "describe_prop" and src["how"] == "按时间推断"


def test_image_nodes_do_not_list_voice(store, sample):
    """声音只进视频提示词,与图片无关。"""
    _approved(store, "voice", "character", sample["lin"], VOICE)
    t = create_task(store, kind="image", target_type="character", target_id=sample["lin"], model="m", params={},
                    seed=1, prompt=f"林小雨的声音:{voices.compose(VOICE)}", refs=[])
    assert trace_views.llm_sources(store, repo.get(store, "task", t)) == []


def test_siblings_list_failed_and_replaced_attempts(store, two_segments):
    s, root = two_segments, two_segments["root"]
    old = repo.get(store, "segment", s["seg1"])["video_path"]
    newer = done_task(store, root, s["seg1"], "video", "outputs/tasks/v9/clip.mp4", b"v9")
    bad = create_task(store, kind="video", target_type="segment", target_id=s["seg1"], model="m",
                      params={"writes": "video_path"}, seed=3, prompt="p", refs=[])
    set_task_state(store, bad, "failed", error="ComfyUI 执行失败")
    sib = {x["id"]: x["note"] for x in trace_views.siblings(store, repo.get(store, "task", newer))}
    first = provenance.node(store, root, old)["task"]["id"]
    assert sib == {first: "旧版本", bad: "失败"}


def test_siblings_of_a_keyframe_are_only_that_keyframe(store, two_segments):
    """关键帧任务都挂在段落上:同一段另一张关键帧的尝试不算「同目标」。"""
    seg = two_segments["seg1"]
    k1, k2 = repo.add_keyframe(store, seg, 3.0), repo.add_keyframe(store, seg, 6.0)
    ids = [create_task(store, kind="image", target_type="segment", target_id=seg, model="m",
                       params={"writes": "key_frame_path", "keyframe_id": k}, seed=1, prompt="p", refs=[])
           for k in (k1, k1, k2)]
    sib = trace_views.siblings(store, repo.get(store, "task", ids[0]))
    assert [x["id"] for x in sib] == [ids[1]] and sib[0]["note"] == "在途"


def test_task_view_has_everything_for_one_task(store, two_segments):
    s, root = two_segments, two_segments["root"]
    tid = provenance.node(store, root, repo.get(store, "segment", s["seg1"])["video_path"])["task"]["id"]
    (root / "outputs/tasks" / str(tid)).mkdir(parents=True, exist_ok=True)
    (root / "outputs/tasks" / str(tid) / SUBMITTED).write_text(json.dumps({"submitted": {"1": {}}, "names": {}}),
                                                                encoding="utf-8")
    journal.append(root, [f"task {tid}: queued → done", f"task {tid}0: 别的任务"], prefix="host=1 ")
    v = trace_views.task_view(store, root, tid)
    assert v["row"]["id"] == tid and v["params"] == {"writes": "video_path"}
    assert v["refs"] == [{"path": "outputs/tasks/f1/frame.png", "exists": True}]
    assert v["submitted"].startswith("{") and len(v["journal"].lines) == 1
    with pytest.raises(KeyError):
        trace_views.task_view(store, root, 99999)


def _export(store, root, s):
    videos = [{"segment_id": s["seg1"], "number": 1, "video_path": repo.get(store, "segment", s["seg1"])["video_path"]}]
    report = provenance.build(store, root, s["episode"], produce_id="ASD-1", producer="P", videos=videos)
    (root / "outputs/exports/x").mkdir(parents=True)
    (root / "outputs/exports/x/provenance.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    with store.write() as c:
        return c.execute("INSERT INTO export (episode_id, produce_id, content_producer, inputs_json, state, labeled_path,"
                         " plain_path, report_path, created_at, finished_at) VALUES (?, 'ASD-1', 'P', ?, 'done',"
                         " 'outputs/exports/x/labeled.mp4', 'outputs/exports/x/plain.mp4',"
                         " 'outputs/exports/x/provenance.json', ?, ?)",
                         (s["episode"], json.dumps(videos), now(), now())).lastrowid


def test_export_shows_the_snapshot_and_what_changed_since(store, two_segments):
    """成片不重新追:展示导出时的溯源报告,再标出之后换过的。"""
    s, root = two_segments, two_segments["root"]
    eid = _export(store, root, s)
    v = trace_views.export_view(store, root, eid)
    assert v["report"]["produce_id"] == "ASD-1" and v["changed"] == []
    done_task(store, root, s["seg1"], "video", "outputs/tasks/v9/clip.mp4", b"v9")      # 段落 1 换了视频
    (root / "outputs/tasks/f1/frame.png").write_bytes(b"replaced")                      # 首帧文件被替换
    changed = trace_views.export_view(store, root, eid)["changed"]
    assert "段落 1 的视频之后换成了 outputs/tasks/v9/clip.mp4" in changed
    assert "outputs/tasks/f1/frame.png 的内容与导出时不同" in changed
    assert trace_views.export_for_path(store, "outputs/exports/x/plain.mp4") == eid
    assert trace_views.export_for_path(store, "outputs/tasks/v9/clip.mp4") is None



def _with_music(root, eid):
    """给导出 eid 的溯源报告补一条配乐(与 provenance._music 同形),返回音乐文件。"""
    song = root / "assets/music/song.wav"
    song.parent.mkdir(parents=True, exist_ok=True)
    song.write_bytes(b"song")
    rep = root / "outputs/exports/x/provenance.json"
    report = json.loads(rep.read_text(encoding="utf-8"))
    sha = provenance.cached_sha256(song)
    report["music"] = [{"path": "assets/music/song.wav", "sha256": sha, "recorded_sha256": sha, "license": "自购曲库 #42",
                        "start_sec": 1.0, "end_sec": 9.0, "gain_db": -14.0, "duck_db": -10.0, "fade_in": 1.0,
                        "fade_out": 1.0, "source": "人工提供,非 AI 生成"}]
    rep.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return song


def test_export_snapshot_flags_a_swapped_or_missing_music_track(store, two_segments):
    """配乐是人工提供的:文件之后被换 / 删了,成片快照要标出来。"""
    s, root = two_segments, two_segments["root"]
    eid = _export(store, root, s)
    song = _with_music(root, eid)
    assert trace_views.export_view(store, root, eid)["changed"] == []
    song.write_bytes(b"other song")
    assert "配乐 assets/music/song.wav 的内容与导出时不同" in trace_views.export_view(store, root, eid)["changed"]
    song.unlink()
    assert "配乐 assets/music/song.wav 不在本机了" in trace_views.export_view(store, root, eid)["changed"]
