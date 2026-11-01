# -*- coding: utf-8 -*-
"""段落台词音频:按说话人的**定稿音色**逐句合成、拼成一条(spec 2026-10-30-台词配音与口型闸门-design.md 第 4 节)。

引擎 2026-11 起是 IndexTTS2 港片念法(spec 2026-10-31-IndexTTS2港片配音-design.md):克隆定稿音色 + **念法模板**
(情绪参考,段落逐句覆盖 > 说话人默认)+ 去句内停顿 + 音高起伏放大,配方在 `capability.INDEX_TTS2`。
规划时定死:逐句台词(`rules.dialogue_lines`,按本段角色名认说话人 —— 与视频规划同口径)、音色快照(路径 + 定稿 sha256)、
逐句模板快照(id + 路径 + sha256)、配方、停顿与种子。执行在 `serve/ttser.py`。
**过期**:台词、任一说话人的定稿音色、任一句的念法模板、配方,与当前台词音频那条任务的快照不同;旧引擎(Qwen3 克隆)出的一律过期。
"""
from __future__ import annotations

import json
import sqlite3

from tools.drama import approval, repo, rules
from tools.drama.db import Store
from tools.drama.tasks import create_task
from tools.gen.capability import INDEX_TTS2, MINIMAX_H3

ENGINE = "indextts2_hk"
LEAD_SEC = 0.3      # 开头留白
TAIL_SEC = 0.5      # 结尾留白
ATTEMPTS = 3        # 回听不过换种子重合成,最多这么多次


def recipe() -> dict:
    """当前配方(快照进任务;变了 = 过期)。"""
    return {"emo_alpha": INDEX_TTS2.emo_alpha, "pitch_widen": INDEX_TTS2.pitch_widen,
            "squeeze_gap_sec": INDEX_TTS2.squeeze_gap_sec, "squeeze_db": INDEX_TTS2.squeeze_db}


def _lines(store: Store, segment_id: int) -> list[list[str]]:
    seg = repo.get(store, "segment", segment_id)
    names = [c["name"] for c in repo.segment_assets(store, segment_id)["characters"]]
    return [[who, line] for who, line in rules.dialogue_lines(seg["description"], names)]


def line_starts(description: str, names, duration: float) -> list[float]:
    """台词按镜头对时(spec 2026-10-31-IndexTTS2港片配音-design.md §10):每句台词所在的【镜头k】(共 n 个)
    按分镜时长均分,起点 = (k-1)/n × 时长。同一镜头里的几句给同一个起点(拼接时顺延)。没有镜头号 → 全部 0。
    港片念法一口气连着说,不对时的话声音全挤在片头、说话的人却在后面的镜头才出现(第 1 集第 3、4、5 段实测)。"""
    blocks = [b for b in rules.SUBSHOT.split(description) if b.strip()] if rules.SUBSHOT.search(description) else [description]
    n = len(blocks)
    out = []
    for k, block in enumerate(blocks):
        out += [round(k / n * duration, 3)] * len(rules.dialogue_lines(block, names))
    return out if len(out) == len(rules.dialogue_lines(description, names)) else [0.0] * len(rules.dialogue_lines(description, names))


def _starts(store: Store, segment_id: int) -> list[float]:
    seg = repo.get(store, "segment", segment_id)
    names = [c["name"] for c in repo.segment_assets(store, segment_id)["characters"]]
    return line_starts(seg["description"], names, seg["duration_sec"] or 0.0)


def _voices(store: Store, segment_id: int, speakers) -> tuple[dict, list[str]]:
    """{说话人: 音色快照} 与问题清单。说话人必须是本段绑定的角色、且有定稿音色(IndexTTS2 只要音频,不要参考文本 —— V3)。"""
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
        voices[who] = {"path": a["path"], "sha256": a["sha256"]}
    return voices, problems


def _templates(store: Store, segment_id: int, lines) -> tuple[list[dict | None], list[str]]:
    """每句用哪个念法模板:段落逐句覆盖(`line_templates_json`,{句序号: 模板 id})> 说话人默认(V1)。"""
    seg = repo.get(store, "segment", segment_id)
    over = json.loads(seg["line_templates_json"] or "{}")
    chars = {c["name"]: c for c in repo.segment_assets(store, segment_id)["characters"]}
    out, problems = [], []
    for k, (who, _) in enumerate(lines, 1):
        tid = over.get(str(k)) or (chars[who]["voice_template_id"] if who in chars else None)
        if tid is None:
            if who in chars:
                problems.append(f"角色「{who}」还没选念法模板(到「资产图审 → 角色声音」给他选一个,或在本段逐句选)")
            out.append(None)
            continue
        t = repo.get(store, "voice_template", tid)
        out.append({"id": t["id"], "name": t["name"], "path": t["path"], "sha256": t["sha256"]})
    return out, list(dict.fromkeys(problems))


def tts_problems(store: Store, segment_id: int) -> list[str]:
    lines = _lines(store, segment_id)
    if not lines:
        return ["这段没有台词"]
    return _voices(store, segment_id, [who for who, _ in lines])[1] + _templates(store, segment_id, lines)[1]


def plan_tts(store: Store, segment_id: int, *, seed: int) -> int:
    problems = tts_problems(store, segment_id)
    if problems:
        raise ValueError(";".join(problems))
    lines = _lines(store, segment_id)
    voices, _ = _voices(store, segment_id, [who for who, _ in lines])
    templates, _ = _templates(store, segment_id, lines)
    params = {"workflow": ENGINE, "engine": ENGINE, "lines": lines, "voices": voices, "line_templates": templates,
              "recipe": recipe(), "starts": _starts(store, segment_id), "gap_sec": INDEX_TTS2.gap_sec, "lead_sec": LEAD_SEC, "tail_sec": TAIL_SEC,
              "attempts": ATTEMPTS, "sr_out": INDEX_TTS2.sr_out, "writes": "line_audio_path", "expect_ext": ".wav"}
    return create_task(store, kind="tts", target_type="segment", target_id=segment_id, model=INDEX_TTS2.name,
                       params=params, seed=seed, prompt="\n".join(f"{who}:{line}" for who, line in lines),
                       refs=[v["path"] for v in voices.values()] + [t["path"] for t in templates])


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
    if p.get("engine") != ENGINE:
        return ["配音引擎换了(Qwen3 克隆 → IndexTTS2 港片念法),重新合成"]
    out = []
    lines = _lines(store, segment["id"])
    if lines != p["lines"]:
        out.append("台词改过了,台词音频还是旧台词")
    now, _ = _voices(store, segment["id"], [who for who, _ in lines])
    for who, v in p["voices"].items():
        if who in now and (now[who]["path"], now[who]["sha256"]) != (v["path"], v["sha256"]):
            out.append(f"角色「{who}」换了定稿音色")
    if lines == p["lines"]:
        tpl, _ = _templates(store, segment["id"], lines)
        for k, (was, cur) in enumerate(zip(p["line_templates"], tpl), 1):
            if cur is None or (cur["id"], cur["sha256"]) != (was["id"], was["sha256"]):
                out.append(f"第 {k} 句的念法模板换了")
    if p["recipe"] != recipe():
        out.append("配音配方参数改了")
    if lines == p["lines"] and p.get("starts") != _starts(store, segment["id"]):
        out.append("镜头节奏改了(台词所在镜头或分镜时长变了),台词要按新镜头重新对时")
    return out


def current_audio(store: Store, segment: sqlite3.Row) -> dict | None:
    """视频能钉的台词音频:由 tts 任务产出、没过期、记了时长的当前那条 → {path, sha256, sec};否则 None(原因看 `audio_problems`)。"""
    task = producer(store, segment) if segment["line_audio_path"] else None
    if task is None or task["result_sec"] is None or stale(store, segment):
        return None
    return {"path": task["result_path"], "sha256": task["result_sha256"], "sec": task["result_sec"]}


def audio_problems(store: Store, segment: sqlite3.Row) -> list[str]:
    """出视频前台词音频这一关:没台词、没音频、过期、太长。空 = 可以出视频。"""
    if not _lines(store, segment["id"]):
        return ["这段没有台词 —— 每段都要有画面里的人说的话(spec 2026-10-30 第 6.1 节),到分镜里补一句"]
    if not segment["line_audio_path"]:
        return ["还没有台词音频 —— 先在本段「台词音频」点合成(视频按它对口型)"]
    reasons = stale(store, segment)
    if reasons:
        return [f"台词音频过期:{r},重新合成" for r in reasons]
    audio = current_audio(store, segment)
    if audio is None:
        return ["当前台词音频没记时长(第 3 期之前合成的),重新合成"]
    lo, hi = MINIMAX_H3.duration_range_sec()
    if audio["sec"] > hi:
        return [f"台词音频 {audio['sec']:.1f} 秒,超过视频上限 {hi:.1f} 秒 —— 拆段或删句(不压缩语速)"]
    return []


def video_audio_problem(store: Store, segment: sqlite3.Row, video_task: sqlite3.Row) -> str:
    """这条视频钉的台词音频(台本快照)是不是段落现在的那条;是 → 空,否则说明。
    第 3 期之前出的视频没钉台词音频(台词是 H3 自己编着念的,口型与内容都不受控)也算不是。"""
    used = json.loads(video_task["params_json"]).get("script", {}).get("line_audio")
    if used is None:
        return "当前视频没有钉台词音频(台词是模型自己念的,口型与内容都不受控)"
    now = current_audio(store, segment)
    if now is None or now["sha256"] != used["sha256"]:
        return "当前视频钉的不是段落现在的台词音频"
    return ""
