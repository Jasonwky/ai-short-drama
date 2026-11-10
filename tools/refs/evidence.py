# -*- coding: utf-8 -*-
"""拆解卡的原文与引用核对(spec 2.1、2.3),纯函数(只读文件、不写库)。

- 小说章节:逐字核对(`verbatim.restore`,全角半角、空白不算差别),返回原文写法;
- 视频转写:按拼音核对(`cut.speech.hit`)—— Whisper 常出同音字,按字比会误杀(spike-results/2026-10-29 第 4.2 节);
  在转写上滑窗找最像的一段,返回那段转写原文与它第一个字的秒数。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from tools.cut import speech
from tools.llm import verbatim

# 引用按拼音核对的合格线:沿用台词「念得对不对」的现有口径 —— tools/serve/voicer.py、ttser.py 的 HIT_OK = 0.9
# (实测念对的 10 条按拼音全是 1.0,spike-results/2026-10-29 第 4.2 节)。test_evidence 守住两边一致。
QUOTE_HIT = 0.9
# 引用最短几个字(小说:去标点空白后的字数;转写:音节数)。太短的引用(「好」「来了」)几乎哪儿都对得上,核对等于没核(审查 M2)。
QUOTE_MIN = 6


def quote_size(kind: str, quote: str) -> int:
    """引用的长度,口径同核对:转写按音节(拼音),其他(小说、剧评)按去标点空白后的字数。"""
    if kind == "transcript":
        return len(speech.syllables(quote))
    return len(re.findall(r"[一-鿿A-Za-z0-9]", quote))


def unit_text(refs, root, content_row) -> dict:
    """一个单元的原文:章节读 txt;转写读 json(全文 + 逐字时间 [[字, 起, 止], ...])。
    只收没清理、判过「收」的章节 / 转写,别的抛 ValueError。refs 暂不用(签名与编排一致,见计划 Task 2)。"""
    kind = content_row["kind"]
    if kind not in ("chapter", "transcript"):
        raise ValueError(f"内容 {content_row['id']} 是 {kind},拆解只看章节或转写")
    if content_row["verdict"] != "ok":
        raise ValueError(f"内容 {content_row['id']} 判过「不收」,不拆")
    if content_row["purged_at"]:
        raise ValueError(f"内容 {content_row['id']} 已在 {content_row['purged_at']} 到期清理,原文不在了")
    body = (Path(root) / content_row["path"]).read_text(encoding="utf-8")
    if kind == "chapter":
        return {"kind": "chapter", "text": body, "chars": None}
    data = json.loads(body)
    return {"kind": "transcript", "text": data["text"], "chars": data["chars"]}


def _best_window(chars: list, want: str, lengths: list[int]) -> tuple[float, int, int]:
    """转写上与 want 拼音最像的一段:(命中率, 起始下标, 长度)。同分取先试的长度、靠前的位置。"""
    best = (0.0, 0, 0)
    for n in lengths:
        for i in range(len(chars) - n + 1):
            r = speech.hit(want, "".join(c for c, _, _ in chars[i:i + n]))
            if r > best[0]:
                best = (r, i, n)
    return best


def check_quote(unit: dict, quote: str) -> tuple[bool, str | None, float | None]:
    """(对得上, 原文写法, 秒数)。小说:逐字,秒数为空;转写:按拼音命中 ≥ QUOTE_HIT,秒数是那段第一个字的起始时间。"""
    if unit["kind"] == "chapter":
        restored = verbatim.restore(quote, unit["text"])
        return (restored is not None, restored, None)
    n = len(speech.syllables(quote))
    if n == 0:
        return (False, None, None)
    chars = unit["chars"]
    r, i, size = _best_window(chars, quote, [n] + [m for m in (n - 1, n + 1) if m >= 1])
    if r < QUOTE_HIT:
        return (False, None, None)
    return (True, "".join(c for c, _, _ in chars[i:i + size]), chars[i][1])


def check_names(unit: dict, names: list[str]) -> list[str]:
    """不在原文里的名字(按给出的顺序)。小说逐字;转写按拼音(同音字算在)。空名字算不在。"""
    missing = []
    for name in names:
        if unit["kind"] == "chapter":
            ok = verbatim.restore(name, unit["text"]) is not None
        else:
            n = len(speech.syllables(name))
            ok = n > 0 and _best_window(unit["chars"], name, [n])[0] >= QUOTE_HIT
        if not ok:
            missing.append(name)
    return missing
