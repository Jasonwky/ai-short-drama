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


NIGHT_WORDS = ("夜", "黄昏", "傍晚", "入夜", "晚")


def is_night(scene) -> bool:
    """按场景的「时间」字段分昼 / 夜(港片画风选光线用);没有场景 = 白天。"""
    return scene is not None and any(w in (scene["time"] or "") for w in NIGHT_WORDS)


def asset_prefix(style: Style, table: str) -> str:
    return style.asset_prefix[table] if style.asset_prefix else style.prefix


def frame_suffix(style: Style, art: Art, scene=None) -> str:
    """拼在首帧 / 尾帧 / 关键帧提示词之后:画风(原来首帧一个风格词都没有)+ 时代 + 不要什么。
    港片画风(有 frame_look):质感 + 按场景昼夜选光线,**不写 Avoid**(CFG 1 下否定词会被当描述,spec 2026-10-31 港片画风 §1)。"""
    era = f" Setting: {art.era}." if art.era else ""
    if style.frame_look:
        light = style.light_night if is_night(scene) else style.light_day
        return f" {style.frame_look}, {light}.{era}"
    return f" {style.prefix}.{era} Avoid: {style.avoid}."


def video_phrase(style: Style, art: Art, scene=None) -> str:
    """视频画风。老预设:中文,拼在提示词末尾(H3 吃中文);港片:英文开场句,调用方放在提示词最前(H3 指南)。"""
    if style.video_en:
        return style.video_en["night" if is_night(scene) else "day"] + (f" Setting: {art.era}." if art.era else "")
    return f"画面风格:{style.zh}。" + (f"时代:{art.era}。" if art.era else "")


def problems(text: str, art: Art, where: str) -> list[str]:
    return [f"{where}含本剧禁用词「{w}」(剧的美术设定里禁用)" for w in banned_hits(text, art.banned)]
