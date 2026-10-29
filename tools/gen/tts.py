# -*- coding: utf-8 -*-
"""段落台词音频:按说话人的**定稿音色**逐句克隆、拼成一条(spec 2026-10-30-台词配音与口型闸门-design.md 第 4 节)。

规划时定死:逐句台词(`rules.dialogue_lines`,按本段角色名认说话人 —— 与视频规划同口径)、每个说话人的定稿音色快照
(路径 + 定稿记录里的 sha256 + 参考文本)、停顿与种子。执行在 `serve/ttser.py`。
**过期**:现在的台词、或任一说话人现在的定稿音色,与当前台词音频那条任务的快照不同 —— 视频规划(第 3 期)据此拦。
"""
from __future__ import annotations

import json
import sqlite3

from tools.drama import approval, repo, rules
from tools.drama.db import Store
from tools.drama.tasks import create_task
from tools.gen.capability import QWEN3_TTS

GAP_SEC = 0.4       # 句间停顿
LEAD_SEC = 0.3      # 开头留白
TAIL_SEC = 0.5      # 结尾留白
ATTEMPTS = 3        # 回听不过换种子重合成,最多这么多次


def _lines(store: Store, segment_id: int) -> list[list[str]]:
    seg = repo.get(store, "segment", segment_id)
    names = [c["name"] for c in repo.segment_assets(store, segment_id)["characters"]]
    return [[who, line] for who, line in rules.dialogue_lines(seg["description"], names)]


def _voices(store: Store, segment_id: int, speakers) -> tuple[dict, list[str]]:
    """{说话人: 音色快照} 与问题清单。说话人必须是本段绑定的角色、且有定稿音色。"""
    chars = {c["name"]: c for c in repo.segment_assets(store, segment_id)["characters"]}
    voices, problems = {}, []
    for who in dict.fromkeys(speakers):
        ch = chars.get(who)
        if ch is None:
            problems.append(f"说话人「{who}」不是本段绑定的角色(到分镜里绑上,或改成角色名)")
            continue
        a = approval.current(store, "character", ch["id"], "voice")
        if a is None:
            problems.append(f"角色「{who}」还没有定稿的音色(到「资产图审」页底部「角色声音」生成试听并定稿)")
            continue
        voices[who] = {"path": a["path"], "sha256": a["sha256"], "ref_text": ch["voice_ref_text"] or ""}
        if not voices[who]["ref_text"]:
            problems.append(f"角色「{who}」的定稿音色不知道念的是哪句(手工放的音频),克隆需要参考文本 —— 用试听任务重新生成")
    return voices, problems


def tts_problems(store: Store, segment_id: int) -> list[str]:
    lines = _lines(store, segment_id)
    if not lines:
        return ["这段没有台词"]
    return _voices(store, segment_id, [who for who, _ in lines])[1]


def plan_tts(store: Store, segment_id: int, *, seed: int) -> int:
    problems = tts_problems(store, segment_id)
    if problems:
        raise ValueError(";".join(problems))
    lines = _lines(store, segment_id)
    voices, _ = _voices(store, segment_id, [who for who, _ in lines])
    params = {"workflow": "qwen3_tts_clone", "lines": lines, "voices": voices, "language": QWEN3_TTS.language,
              "gap_sec": GAP_SEC, "lead_sec": LEAD_SEC, "tail_sec": TAIL_SEC, "attempts": ATTEMPTS,
              "writes": "line_audio_path", "expect_ext": ".wav"}
    return create_task(store, kind="tts", target_type="segment", target_id=segment_id, model=QWEN3_TTS.name,
                       params=params, seed=seed, prompt="\n".join(f"{who}:{line}" for who, line in lines),
                       refs=[v["path"] for v in voices.values()])


def producer(store: Store, segment: sqlite3.Row) -> sqlite3.Row | None:
    """产出当前台词音频的那条 tts 任务。"""
    return store.conn.execute("SELECT * FROM task WHERE kind = 'tts' AND target_id = ? AND state = 'done'"
                              " AND result_path = ? ORDER BY id DESC LIMIT 1",
                              (segment["id"], segment["line_audio_path"])).fetchone()


def stale(store: Store, segment: sqlite3.Row) -> list[str]:
    """当前台词音频为什么过期了;没有台词音频、或没过期 → 空。"""
    if not segment["line_audio_path"]:
        return []
    task = producer(store, segment)
    if task is None:
        return ["当前台词音频不是由台词音频任务生成的,重新合成"]
    p = json.loads(task["params_json"])
    out = []
    lines = _lines(store, segment["id"])
    if lines != p["lines"]:
        out.append("台词改过了,台词音频还是旧台词")
    now, _ = _voices(store, segment["id"], [who for who, _ in lines])
    for who, v in p["voices"].items():
        if who in now and (now[who]["path"], now[who]["sha256"]) != (v["path"], v["sha256"]):
            out.append(f"角色「{who}」换了定稿音色")
    return out
