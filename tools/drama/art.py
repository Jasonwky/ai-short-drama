# -*- coding: utf-8 -*-
"""剧级美术设定(spec 2026-10-28-成片质量闸门-第一期-design.md 第 2 节)。

`drama.art_json` = {"era": 时代 / 世界观, "costume": 服饰总则, "banned": [禁用词]},空串 = 没填。
画风沿用 `drama.style`(`styles.STYLES`)。这里只有纯函数:解析、拼进提示词的句子、禁用词匹配 ——
**由代码拼**,LLM / 人写的提示词不必记得写(第 1 集画风跳、服饰年代错的根因就是没人拼)。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from tools.drama.styles import Style


@dataclass(frozen=True)
class Art:
    era: str = ""
    costume: str = ""
    banned: tuple[str, ...] = ()


def parse(raw: str) -> Art:
    if not (raw or "").strip():
        return Art()
    d = json.loads(raw)
    return Art(d.get("era", "").strip(), d.get("costume", "").strip(),
               tuple(w.strip() for w in d.get("banned", []) if w.strip()))


def dumps(art: Art) -> str:
    return json.dumps({"era": art.era, "costume": art.costume, "banned": list(art.banned)}, ensure_ascii=False)


def of(drama) -> Art:
    return parse(drama["art_json"])


def split_words(text: str) -> tuple[str, ...]:
    """表单里的禁用词:逗号、顿号、空白、换行都算分隔,去重保序。"""
    return tuple(dict.fromkeys(w for w in re.split(r"[,,、;;\s]+", text or "") if w))


def banned_hits(text: str, banned: tuple[str, ...]) -> list[str]:
    """text 里出现了哪些禁用词。英文按整词、不分大小写(uniform 不误伤 uniformly 以外的词);中文按子串。"""
    hits = []
    for w in banned:
        if re.fullmatch(r"[A-Za-z][A-Za-z \-']*", w):
            if re.search(rf"\b{re.escape(w)}\b", text or "", re.IGNORECASE):
                hits.append(w)
        elif w in (text or ""):
            hits.append(w)
    return hits


def asset_suffix(art: Art, table: str) -> str:
    """拼在资产图 / 四视图的 final_prompt 之后。没填时代就什么都不拼。"""
    if not art.era:
        return ""
    costume = f" Costume: {art.costume}." if table == "character" and art.costume else ""
    return f" Setting: {art.era}.{costume}"


def frame_suffix(style: Style, art: Art) -> str:
    """拼在首帧 / 尾帧 / 关键帧提示词之后:画风(原来首帧一个风格词都没有)+ 时代 + 不要什么。"""
    era = f" Setting: {art.era}." if art.era else ""
    return f" {style.prefix}.{era} Avoid: {style.avoid}."


def video_phrase(style: Style, art: Art) -> str:
    """拼在视频提示词末尾(H3 吃中文)。"""
    return f"画面风格:{style.zh}。" + (f"时代:{art.era}。" if art.era else "")


def problems(text: str, art: Art, where: str) -> list[str]:
    return [f"{where}含本剧禁用词「{w}」(剧的美术设定里禁用)" for w in banned_hits(text, art.banned)]
