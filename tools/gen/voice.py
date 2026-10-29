# -*- coding: utf-8 -*-
"""角色试听音:按「声音描述」让 Qwen3-TTS VoiceDesign 念一句(spec 2026-10-30-台词配音与口型闸门-design.md 第 3 节)。

试听句取这个角色剧本(段落描述)里最长的一句台词 —— 定稿后它就是克隆时的参考文本,句子长一些音色更稳;
角色还没台词就用固定句。种子由调用方给(面板一次排 3 个)。参数规划时定死。
"""
from __future__ import annotations

from tools.drama import repo, rules
from tools.drama.db import Store
from tools.drama.tasks import create_task
from tools.gen.capability import QWEN3_TTS

FALLBACK_TEXT = "我听见了,你放心,这件事交给我。"
MAX_CHARS = 40          # 参考音频 3–10 秒为宜:再长的台词不当试听句


def sample_text(store: Store, character_id: int) -> str:
    ch = repo.get(store, "character", character_id)
    names = [r["name"] for r in store.conn.execute("SELECT name FROM character WHERE drama_id = ?", (ch["drama_id"],))]
    lines = []
    for seg in store.conn.execute("SELECT s.description FROM segment s JOIN episode e ON e.id = s.episode_id"
                                  " WHERE e.drama_id = ? ORDER BY e.number, s.number", (ch["drama_id"],)):
        lines += [line for who, line in rules.dialogue_lines(seg["description"], names)
                  if who == ch["name"] and len(line) <= MAX_CHARS]
    return max(lines, key=len) if lines else FALLBACK_TEXT


def plan_voice(store: Store, character_id: int, *, seed: int) -> int:
    ch = repo.get(store, "character", character_id)
    desc = ch["voice_desc"].strip()
    if not desc:
        raise ValueError(f"角色「{ch['name']}」还没有声音描述,先到资产面板填")
    params = {"workflow": "qwen3_tts_design", "instruct": desc, "text": sample_text(store, character_id),
              "language": QWEN3_TTS.language, "writes": "voice_ref_path", "expect_ext": ".wav"}
    return create_task(store, kind="voice", target_type="character", target_id=character_id, model=QWEN3_TTS.name,
                       params=params, seed=seed, prompt=desc, refs=[])
