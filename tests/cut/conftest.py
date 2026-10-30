# -*- coding: utf-8 -*-
"""成片测试的夹具:真 ffmpeg(本机 4.4)生成的小样片 + 走 task 表的生成记录(溯源要靠它)。"""
import pytest

from tests.drama.conftest import give_line_audio, sample, store  # noqa: F401  —— 复用 P1 的夹具
from tools.cut import checklist, lipsync
from tools.drama import repo, rules
from tools.drama.tasks import create_task, set_task_state
from tools.gen import tts
from tools.serve.dispatch import sha256_file
from tools.serve.fakes import sample_mp4


SRT = "1\n00:00:00,200 --> 00:00:00,900\n这块玉佩，是我母亲留给我的。\n\n"
# 段落 2 的台词在后半段(整集 1.6–1.95 s):1.2–1.5 s 是一段没台词的区间,配乐满音量、底部没字 —— 配乐 / 字幕测试拿它当对照
SRT2 = "1\n00:00:00,600 --> 00:00:00,950\n我会一直带着它。\n\n"


@pytest.fixture(scope="session")
def mp4_bytes():
    return sample_mp4()


def done_task(store, root, seg_id, kind, rel, data, refs=()):  # noqa: F811
    """把 data 写到 root/rel,再记一条已完成的生成任务(与 worker 收取时同样的字段)。"""
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(data)
    writes = {"video": "video_path", "image": "first_frame_path", "align": "subtitle_path"}[kind]
    prompt = (repo.get(store, "segment", seg_id)["description"] if kind == "video"       # 视频提示词带着当时的台词
              else f"{kind} prompt")
    params = {"writes": writes, **({"video": refs[0]} if kind == "align" else {})}    # 对齐认的是哪条视频
    if kind == "video":         # 台本快照:当时的台词与钉进去的台词音频(spec 2026-10-30 第 5 节;导出核对它)
        seg = repo.get(store, "segment", seg_id)
        params["script"] = {"lines": [list(x) for x in rules.dialogue_lines(seg["description"])],
                            "line_audio": tts.current_audio(store, seg)}
    t = create_task(store, kind=kind, target_type="segment", target_id=seg_id, model=f"m-{kind}",
                    params=params, seed=7, prompt=prompt, refs=list(refs))
    set_task_state(store, t, "queued", external_id=f"p{t}")
    set_task_state(store, t, "done", result_path=rel, result_sha256=sha256_file(root / rel))
    repo.update(store, "segment", seg_id, **{writes: rel})
    if kind == "video" and params["script"]["line_audio"]:     # 与 worker 一样,视频出来就量口型(样片里没有脸 → 每句无法判定)
        spans = root / params["script"]["line_audio"]["path"]
        spans = spans.with_name("tts.json")
        if not spans.is_file():
            spans.parent.mkdir(parents=True, exist_ok=True)
            spans.write_text('{"spans": [[0.2, 0.9]]}', encoding="utf-8")
        lipsync.check(root, rel, [[0.2, 0.9]])
    return t


def speech_checked(store, root, seg_id, video):
    """没台词的段落:记一条通过的语音检查(spec 2026-10-28 第 3 节,没过的拦导出)。"""
    t = create_task(store, kind="align", target_type="segment", target_id=seg_id, model="m-align",
                    params={"writes": "speech_check", "video": video, "lines": []}, seed=0, prompt="", refs=[video])
    rel = f"outputs/tasks/{t}/align.json"
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text('{"lines": [], "chars": [], "envelope": [], "extra": []}', encoding="utf-8")
    set_task_state(store, t, "queued", external_id=f"p{t}")
    set_task_state(store, t, "done", result_path=rel, result_sha256=sha256_file(root / rel))
    return t


@pytest.fixture
def two_segments(store, sample, tmp_path, mp4_bytes):  # noqa: F811
    """两段都有带音轨的视频、首帧 → 视频的生成链完整;两段都有台词(用户 2026-10-29:每段都必须有台词)、
    都配了台词音频(视频钉着它)与对齐出的字幕(0.2–0.9 s)。"""
    root = tmp_path
    seg1 = sample["seg"]
    seg2 = repo.add_segment(store, sample["episode"], 2, scene_id=sample["yard"], duration_sec=8.0,
                            description="【镜头1】林小雨把玉佩收进怀里。【镜头2】林小雨说：「我会一直带着它。」")
    repo.bind_characters(store, seg2, [sample["lin"]])
    repo.update(store, "segment", seg1, first_frame_path=None, video_path=None)
    for n, seg in ((1, seg1), (2, seg2)):
        give_line_audio(store, seg)
        done_task(store, root, seg, "image", f"outputs/tasks/f{n}/frame.png", b"png-%d" % n)
        done_task(store, root, seg, "video", f"outputs/tasks/v{n}/clip.mp4", mp4_bytes,
                  refs=[f"outputs/tasks/f{n}/frame.png"])
    done_task(store, root, seg1, "align", "outputs/tasks/a1/subtitles.srt", SRT.encode("utf-8"),
              refs=["outputs/tasks/v1/clip.mp4"])
    done_task(store, root, seg2, "align", "outputs/tasks/a2/subtitles.srt", SRT2.encode("utf-8"),
              refs=["outputs/tasks/v2/clip.mp4"])
    for seg, n in ((seg1, 1), (seg2, 2)):           # 看片确认(v13):人看过才许导
        repo.add_review(store, seg, video_path=f"outputs/tasks/v{n}/clip.mp4",
                        sha256=sha256_file(root / f"outputs/tasks/v{n}/clip.mp4"), checks=list(checklist.MANUAL))
    return {**sample, "root": root, "seg1": seg1, "seg2": seg2}
