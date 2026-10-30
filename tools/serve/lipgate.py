# -*- coding: utf-8 -*-
"""口型闸门(spec 2026-10-30-台词配音与口型闸门-design.md 第 6.3、6.4 节;用户 2026-10-30 选「画外音不算合格」):

- 视频任务一完成,worker 就量**这条视频本身**的口型(`cut/lipsync`,本机 CPU 约 20 秒,结果按 sha256 缓存);
- 有一句「不过」→ 同一份台词音频、新种子、提示词加 `ON_CAMERA` 自动重出一条;**每份台词音频最多自动重出 RETRIES 次**,
  之后停下交给人(视频卡片上看得到每句结论)。重出的视频进历史,当前视频不自动替换(写回只填空位,与平常一样);
- 「无法判定」(句子太短、这段时间没有正脸)不算不过,不重出,靠看片确认;
- 导出:当前视频有「不过」的句子 → 拦(`problem`)。

自动重出不看 `flow_auto`:这是用户在 2026-10-30 明确选的(「1」:不过就自动换种子重出)。
"""
from __future__ import annotations

import json
import random
import sqlite3
from pathlib import Path

from tools.cut import lipsync
from tools.drama.db import Store
from tools.serve import ttser

RETRIES = 2


def spans_of(data_root: Path, video_task: sqlite3.Row) -> list | None:
    """这条视频钉的台词音频里每句的起止(音频钉在第 0 帧,视频时间轴与它一致);没钉 / 没记录 → None。"""
    used = json.loads(video_task["params_json"]).get("script", {}).get("line_audio")
    check = ttser.check_of(data_root, used["path"]) if used else None
    return check["spans"] if check else None


def retries_used(store: Store, segment_id: int, audio_sha: str) -> int:
    return store.conn.execute(
        "SELECT COUNT(*) FROM task WHERE kind = 'video' AND target_id = ? AND json_extract(params_json, '$.lip_retry') = 1"
        " AND json_extract(params_json, '$.script.line_audio.sha256') = ?", (segment_id, audio_sha)).fetchone()[0]


def after_video(store: Store, data_root: Path, task: sqlite3.Row) -> list[str]:
    """视频任务刚完成时调用:量口型,不过就自动重出(有上限)。返回给 worker 日志的几行。"""
    spans = spans_of(data_root, task)
    if spans is None or not task["result_path"]:
        return []
    try:
        lines = lipsync.check(data_root, task["result_path"], spans)
    except Exception as e:          # 量不了(模型文件缺、视频坏):如实记下,不当作通过、也不重出
        return [f"task {task['id']} 口型没量成:{type(e).__name__}: {e}"]
    bad = [x for x in lines if x.verdict == "不过"]
    log = [f"task {task['id']} 口型:" + ";".join(f"第 {x.n} 句 {x.verdict}" + (f" {x.r:.2f}" if x.r is not None else "")
                                              for x in lines)]
    if not bad:
        return log
    sha = json.loads(task["params_json"])["script"]["line_audio"]["sha256"]
    used = retries_used(store, task["target_id"], sha)
    if used >= RETRIES:
        return log + [f"task {task['id']} 口型不过,同一份台词音频已自动重出 {used} 次,停下交给人看"]
    from tools.gen.planner import plan_video      # planner → tts → … 不依赖本模块;放这里免得 serve 启动时多一层导入
    try:
        new = plan_video(store, task["target_id"], seed=random.randrange(1, 2**31), lip_retry=True)
    except ValueError as e:
        return log + [f"task {task['id']} 口型不过,想自动重出但规划被拒:{e}"]
    return log + [f"task {task['id']} 口型不过(第 {'、'.join(str(x.n) for x in bad)} 句),自动重出 task {new}"
                  f"(第 {used + 1} / {RETRIES} 次)"]


def problem(store: Store, data_root: Path, seg: sqlite3.Row, video_task: sqlite3.Row | None) -> str:
    """导出前:当前视频的口型。有「不过」的句子 → 说明;没量过 → 说明;都过 / 无法判定 → 空。"""
    if video_task is None:
        return ""
    n = seg["number"]
    if not json.loads(video_task["params_json"]).get("script", {}).get("line_audio"):
        return ""                   # 没钉台词音频的另有闸门(tts.video_audio_problem)
    spans = spans_of(data_root, video_task)
    if spans is None:               # 钉了却找不到逐句时间:量不了口型,不静默放过
        return f"段落 {n} 当前视频钉的台词音频没有逐句时间记录(tts.json 不在本机),量不了口型 —— 重新合成台词音频、重出视频"
    lines = lipsync.cached(data_root, seg["video_path"], spans)
    if lines is None:
        return f"段落 {n} 的当前视频还没量口型(在「视频」面板点「量口型」)"
    bad = [x for x in lines if x.verdict == "不过"]
    if bad:
        return (f"段落 {n} 口型不过:" + ";".join(f"第 {x.n} 句 {x.why}" for x in bad)
                + " —— 说话的人要在说话时正脸入镜、嘴随声动;重出后在历史里「调用」")
    return ""
