# -*- coding: utf-8 -*-
"""分镜的可量化规则 —— 由代码判卷,不交给 LLM 自觉。

规则来自行业实践(huobao-drama 分镜规范),但 huobao 只把它们写在提示词里,
代码侧 `duration = LLM给的 || 10` 静默填空。这里把它们落成断言(设计 §6.3):

  总时长       = 剧本字数 ÷ 500 字/分钟
  段落数       ≈ 总时长 ÷ 12 秒, ±20%
  段落时长     ∈ 视频后端 Capability 声明的训练区间(H3: 124–362 帧)
  段落时长下限 = 台词字数 ÷ 4.5 字/秒 + 2 秒表演余量
  子镜头数     ∈ [2, 4](按 description 里的【镜头N】计)

description 的写法约定:按【镜头1】【镜头2】…逐子镜头写;
台词写成「角色名说：「台词」」,旁白写成「旁白：内容」。
"""
from __future__ import annotations

import math
import re

from tools.gen.capability import Capability

CHARS_PER_MINUTE = 500
SEGMENT_TARGET_SEC = 12
SEGMENT_COUNT_TOLERANCE = 0.2
SPEECH_CHARS_PER_SEC = 4.5
PERFORMANCE_PAD_SEC = 2.0
SUBSHOTS_MIN, SUBSHOTS_MAX = 2, 4

_PUNCT = set("，。！？、；：,.!?;:\"'“”‘’「」『』（）()【】《》…—- \t\r\n")
_SPEECH = re.compile(r"说[：:]\s*「([^」]*)」")
_SPEAKER = re.compile(r"([^\s【】：:「」，。！？]+?)说[：:]\s*「([^」]*)」")
_NARRATION = re.compile(r"旁白[：:]\s*([^【\n]*)")
_SUBSHOT = re.compile(r"【镜头\s*\d+】")


def count_chars(text: str) -> int:
    """计字数:去掉标点和空白。"""
    return sum(1 for ch in text if ch not in _PUNCT)


def dialogue_chars(description: str) -> int:
    speech = sum(count_chars(s) for s in _SPEECH.findall(description))
    narration = sum(count_chars(s) for s in _NARRATION.findall(description))
    return speech + narration


def _speaker(raw: str, names) -> str:
    """「说：」前那串字常连着动作描写(「声音冷冽如冰萧珩」);以已知角色名结尾就取最长的那个名字,否则照写。"""
    return max((n for n in names if raw.endswith(n)), key=len, default=raw)


def dialogue_by_speaker(description: str, names=()) -> dict[str, int]:
    """每个说话人的台词字数。给了角色名就按名字认说话人(见 `_speaker`),调用方再按已知角色名前缀匹配。"""
    out: dict[str, int] = {}
    for raw, line in _SPEAKER.findall(description):
        name = _speaker(raw, names)
        out[name] = out.get(name, 0) + count_chars(line)
    return out


def dialogue_lines(description: str, names=()) -> list[tuple[str, str]]:
    """按出现顺序的 (说话人, 台词原文)。旁白不在内 —— 它不在画面里说,由配音/字幕环节处理。"""
    return [(_speaker(raw, names), line) for raw, line in _SPEAKER.findall(description)]


def stale_lines(description: str, said: list) -> list[str]:
    """描述里现在有、当前视频当时没说的台词 —— 台词改过、视频还是旧的(局部改写不删段落,用户 2026-10-23 定)。
    said:视频任务台本快照里的 [[说话人, 台词], …]。"""
    old = {line for _, line in said}
    return [line for _, line in dialogue_lines(description) if line not in old]


def dropped_lines(description: str, said: list) -> list[str]:
    """当前视频说了、描述里现在已经没有的台词(删台词的另一半:视频里还留着旧话)。"""
    now = {line for _, line in dialogue_lines(description)}
    return [line for _, line in said if line not in now]


def min_duration_sec(description: str) -> float:
    return dialogue_chars(description) / SPEECH_CHARS_PER_SEC + PERFORMANCE_PAD_SEC


def subshot_count(description: str) -> int:
    return len(_SUBSHOT.findall(description))


def check_segment(description: str, duration_sec: float | None, cap: Capability) -> list[str]:
    """返回问题清单,空表示通过。每条都是给人看的话。"""
    problems: list[str] = []
    if duration_sec is None:
        problems.append("缺少时长 —— 不许用默认值填空")
    else:
        problems += cap.check_duration(duration_sec)
        need = min_duration_sec(description)
        if duration_sec < need:
            problems.append(f"台词 {dialogue_chars(description)} 字需 ≥{need:.1f} 秒,当前 {duration_sec:g} 秒")
    n = subshot_count(description)
    if not SUBSHOTS_MIN <= n <= SUBSHOTS_MAX:
        problems.append(f"子镜头 {n} 个,应为 {SUBSHOTS_MIN}–{SUBSHOTS_MAX} 个(按【镜头N】计)")
    return problems


def expected_segment_range(script: str) -> tuple[int, int]:
    total_sec = count_chars(script) / CHARS_PER_MINUTE * 60
    n = total_sec / SEGMENT_TARGET_SEC
    lo = max(1, math.floor(n * (1 - SEGMENT_COUNT_TOLERANCE)))
    hi = max(1, math.ceil(n * (1 + SEGMENT_COUNT_TOLERANCE)))
    return lo, hi


def check_episode(script: str, n_segments: int) -> list[str]:
    lo, hi = expected_segment_range(script)
    if lo <= n_segments <= hi:
        return []
    total_sec = count_chars(script) / CHARS_PER_MINUTE * 60
    return [f"剧本 {count_chars(script)} 字(约 {total_sec:.0f} 秒),段落数应在 {lo}–{hi},当前 {n_segments}"]
