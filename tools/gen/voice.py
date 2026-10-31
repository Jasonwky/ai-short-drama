# -*- coding: utf-8 -*-
"""角色试听音:按「声音描述」让 Qwen3-TTS VoiceDesign 念一句(spec 2026-10-30-台词配音与口型闸门-design.md 第 3 节)。

试听句取这个角色剧本(段落描述)里最长的一句台词 —— 定稿后它就是克隆时的参考文本,句子长一些音色更稳;
角色还没台词就用固定句。种子由调用方给(面板一次排 3 个)。参数规划时定死。
"""
from __future__ import annotations

from tools.drama import approval, repo, rules
from tools.drama.db import Store
from tools.drama.tasks import create_task
from tools.gen.capability import INDEX_TTS2, QWEN3_TTS

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


# 念法模板候选的情绪向量预设(IndexTTS2 的 8 维顺序:喜、怒、哀、惧、厌恶、忧郁、惊讶、平静)。
# 「喝令」「惊疑」取自 spike-results/2026-10-31-降低AI感调研.md §12 的实验值;其余按字面配,候选要过韵律门槛(cut/prosody)才能存。
EMO_PRESETS = {
    "喝令": [0, 0.8, 0, 0, 0.3, 0, 0, 0],
    "惊疑": [0, 0, 0, 0.35, 0, 0, 0.65, 0],
    "低声狡黠": [0.3, 0, 0, 0, 0.2, 0.2, 0, 0.3],
    "平述": [0, 0, 0, 0, 0, 0, 0, 1.0],
}


def plan_template(store: Store, character_id: int, *, preset: str, seed: int) -> int:
    """念法模板候选(spec 2026-10-31-IndexTTS2港片配音-design.md §3):用角色的定稿音色 + 情绪向量预设念他的试听句,
    同样去停顿 + 起伏放大;产物只进任务记录,人听了、过了韵律门槛才「存为模板」(不写回角色)。"""
    if preset not in EMO_PRESETS:
        raise ValueError(f"未知的念法预设 {preset!r},可选:{list(EMO_PRESETS)}")
    ch = repo.get(store, "character", character_id)
    a = approval.current(store, "character", character_id, "voice")
    if a is None:
        raise ValueError(f"角色「{ch['name']}」还没有定稿的音色 —— 念法模板要用定稿音色来念")
    params = {"workflow": "indextts2_template", "voice": {"path": a["path"], "sha256": a["sha256"]}, "preset": preset,
              "emo_vector": EMO_PRESETS[preset], "text": sample_text(store, character_id),
              "recipe": {"emo_alpha": INDEX_TTS2.emo_alpha, "pitch_widen": INDEX_TTS2.pitch_widen,
                         "squeeze_gap_sec": INDEX_TTS2.squeeze_gap_sec, "squeeze_db": INDEX_TTS2.squeeze_db},
              "sr_out": INDEX_TTS2.sr_out, "writes": "voice_template_candidate", "expect_ext": ".wav"}
    return create_task(store, kind="voice", target_type="character", target_id=character_id, model=INDEX_TTS2.name,
                       params=params, seed=seed, prompt=f"念法候选:{preset}", refs=[a["path"]])
