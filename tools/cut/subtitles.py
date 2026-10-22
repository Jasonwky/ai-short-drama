# -*- coding: utf-8 -*-
"""字幕:已知台词 + whisper 逐字时间戳 + 本地能量曲线 → 每句的起止时间 → SRT。纯函数,可单测。

2026-09-26 云机实测(`spike-results/2026-09-26-字幕对齐云机实测.md`):
- whisper token 级时间戳的**句尾可靠**(一片两句:句尾误差 0.06 s);
- **句首会被拉进前面的静音**(首字的时间段从上一个 token 的时刻算起):第 2 句被标成 3.80 s,实际 4.89 s 才开口 ——
  所以句首**吸附到能量曲线的发声起点**(0.1 s 窗 RMS 第一次越过峰值的一定比例);
- 识别会有错字(「它是」→「她是」)与字节级 token(「玉佩」),逐字对齐用 difflib,命中率低于 MIN_HIT 就不出这句的字幕。

用数据审:命中率、句首 < 句尾、各句单调不重叠、不超出片长 —— 不过就判失败,不「尽力出一版」。
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

PUNCT = set("，。！？、,.!?\"'“”「」：:；; …—")
MIN_HIT = 0.7            # 一句台词至少对上 70% 的字才算对齐可信
ONSET_RATIO = 0.15       # 能量越过峰值的 15% 算开口
STEP = 0.1               # 能量曲线的窗长(秒)
MIN_SHOW = 1.0           # 字幕至少显示 1 秒
TAIL = 0.3               # 句尾多留 0.3 秒(不越过下一句句首)


@dataclass(frozen=True)
class Cue:
    text: str
    start: float
    end: float


def _clean(s: str) -> str:
    return "".join(c for c in s if c not in PUNCT and not c.isspace())


def align(lines: list[str], chars: list[tuple[str, float, float]]) -> list[dict]:
    """每句台词按顺序在识别文字里找命中字(用过的字不再给后面的句子)。
    返回 [{line, hit, total, first, second, last, lead}],first/second/last 是命中字在 chars 里的下标(没命中为 None),
    lead 是台词开头没对上的字数(首字听错时,它前面那 lead 个识别字仍算这句台词)。"""
    heard = "".join(c for c, _, _ in chars)
    used = 0
    out = []
    for line in lines:
        want = _clean(line)
        sm = difflib.SequenceMatcher(None, want, heard[used:], autojunk=False)
        blocks = [blk for blk in sm.get_matching_blocks() if blk.size]
        hit = sorted(used + b + k for _, b, n in blocks for k in range(n))
        out.append({"line": line, "hit": len(hit), "total": len(want),
                    "first": hit[0] if hit else None, "second": hit[1] if len(hit) > 1 else (hit[0] if hit else None),
                    "last": hit[-1] if hit else None, "lead": blocks[0].a if blocks else 0})
        if hit:
            used = hit[-1] + 1
    return out


EXTRA_MIN_CHARS = 3     # 台词之外多说至少这么多字(去标点)才报:单个「嗯」「啊」不算


def extra_speech(lines: list[str], chars: list[tuple[str, float, float]],
                 envelope: list[float]) -> tuple[list[dict], str]:
    """台词之外有没有多说(路线图未决项 #7:人物把舞台指示念了出来)。返回 ([{text, start, end}], 不能判断的原因)。
    - 复用 align 的匹配:每句覆盖识别字 [first − lead, last];覆盖之外的连续识别字是候选;
    - 任一句台词命中率低于 MIN_HIT → 不判(否则一句没识别出来,所有识别字都成了「多说」);
    - **用数据审 whisper**:候选所在时间段的能量要达到开口门槛 —— whisper 会在静音尾巴上编出「字幕由……提供」。"""
    aligned = align(lines, chars)
    if any(a["total"] and a["hit"] / a["total"] < MIN_HIT for a in aligned):
        return [], "识别与台词对不上,无法判断是否多说"
    covered: set[int] = set()
    for a in aligned:
        if a["first"] is not None:
            covered |= set(range(max(0, a["first"] - a["lead"]), a["last"] + 1))
    groups, cur = [], []
    for i in range(len(chars)):
        if i in covered:
            if cur:
                groups.append(cur)
            cur = []
        else:
            cur.append(i)
    if cur:
        groups.append(cur)
    th = (max(envelope) if envelope else 0.0) * ONSET_RATIO
    runs = []
    for g in groups:
        text = _clean("".join(chars[i][0] for i in g))
        start, end = chars[g[0]][1], chars[g[-1]][2]
        voiced = th > 0 and any(envelope[k] >= th for k in range(round(start / STEP), min(len(envelope), round(end / STEP) + 1)))
        if len(text) >= EXTRA_MIN_CHARS and voiced:
            runs.append({"text": text, "start": start, "end": end})
    return runs, ""


def onset(envelope: list[float], lo: float, hi: float) -> float | None:
    """[lo, hi] 内能量第一个**上升沿**(前一窗低于、这一窗达到峰值的 ONSET_RATIO)的时刻;没有返回 None。
    要上升沿而不是「第一次高于」:lo 是上一句的句尾,上一句的尾音可能还没落下去。"""
    peak = max(envelope) if envelope else 0.0
    if peak <= 0:
        return None
    th = peak * ONSET_RATIO
    for i in range(round(lo / STEP), min(len(envelope), round(hi / STEP) + 1)):
        if envelope[i] >= th and (i == 0 or envelope[i - 1] < th):
            return round(i * STEP, 2)
    return None


def build(lines: list[str], chars: list[tuple[str, float, float]], envelope: list[float],
          duration: float) -> tuple[list[Cue], list[str]]:
    """返回 (字幕, 问题)。有问题就不该出字幕 —— 调用方据问题判失败。"""
    problems = []
    cues = []
    prev_end = 0.0
    for n, a in enumerate(align(lines, chars), 1):
        if a["total"] == 0:
            continue
        if a["hit"] / a["total"] < MIN_HIT:
            problems.append(f"第 {n} 句只对上 {a['hit']}/{a['total']} 字(低于 {int(MIN_HIT * 100)}%)—— 识别与台词对不上,需人工校")
            continue
        end = chars[a["last"]][2]
        guess = chars[a["second"]][2]                      # 第二个命中字的结束时刻:句首一定在它之前
        # 句首也一定在「这句台词之前那个识别字」之后:模型会多念出不属于台词的话(2026-09-29 真链路:
        # 人物先把提示词里的「林小雨用普通话说」念了出来),从上一句句尾找起会把它吸成这句的开口
        pre = a["first"] - a["lead"] - 1
        lo = max(prev_end, chars[pre][2]) if pre >= 0 else prev_end
        start = onset(envelope, lo, guess)
        if start is None:                                   # 找不到发声起点:退回 whisper 的首字时刻
            start = max(prev_end, chars[a["first"]][1])
        if not prev_end <= start < end:
            problems.append(f"第 {n} 句时间不合理:{start:.2f}–{end:.2f}(上一句到 {prev_end:.2f})")
            continue
        if end > duration + 0.1:
            # 实战(spike-results/2026-10-19-真实场景参考图调研.md):人物先做动作、开口太晚,句尾被片尾截断;
            # 加长 1.5 s 重出后一次通过。只给建议,不改时长规则(1 例失败,定不出公式)
            problems.append(f"第 {n} 句结束 {end:.2f}s 超出片长 {duration:.2f}s"
                            "(台词可能被挤到片尾截断:把本段加长 1.5 秒左右后重出视频)")
            continue
        cues.append(Cue(a["line"], round(start, 2), round(end, 2)))
        prev_end = end
    return _pad(cues, duration), problems


def _pad(cues: list[Cue], duration: float) -> list[Cue]:
    """句尾多留一点、太短的拉到 MIN_SHOW,但都不越过下一句句首与片长。"""
    out = []
    for i, c in enumerate(cues):
        limit = cues[i + 1].start if i + 1 < len(cues) else duration
        end = min(max(c.end + TAIL, c.start + MIN_SHOW), limit)
        out.append(Cue(c.text, c.start, round(max(end, c.end), 2)))
    return out


def _ts(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def srt(cues: list[Cue], offset: float = 0.0, first_index: int = 1) -> str:
    return "".join(f"{i}\n{_ts(c.start + offset)} --> {_ts(c.end + offset)}\n{c.text}\n\n"
                   for i, c in enumerate(cues, first_index))


def parse_srt(text: str) -> list[Cue]:
    cues = []
    for block in text.strip().split("\n\n"):
        rows = block.strip().splitlines()
        if len(rows) < 3:
            continue
        a, b = (x.strip() for x in rows[1].split("-->"))
        cues.append(Cue("\n".join(rows[2:]), _secs(a), _secs(b)))
    return cues


def _secs(ts: str) -> float:
    h, m, rest = ts.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


# 一行能放几个字(tmp/subwidth/probe.py 按 media.SUB_STYLE 实测):字宽 ≈ 画面高的 2.6%(竖屏 768x1344 每字 35 px,
# 25 字贴边被裁);只用画宽的 75%,两边留白;横屏放得下更多,但一行再长就读不完,封顶 16
CHAR_PER_HEIGHT = 35 / 1344
LINE_SHARE = 0.75
LINE_CAP = 16
_BREAK = re.compile(r"(?<=[，。！？；、…,.!?;])")


def max_chars(width: int, height: int) -> int:
    return min(LINE_CAP, int(width * LINE_SHARE / (height * CHAR_PER_HEIGHT)))


def _pieces(text: str, limit: int) -> list[str]:
    """先在标点后断句、能并就并成不超过 limit 的一块;单句超长就均分成几块。"""
    out: list[str] = []
    for clause in (c for c in _BREAK.split(text) if c):
        if out and len(out[-1]) + len(clause) <= limit:
            out[-1] += clause
        elif len(clause) <= limit:
            out.append(clause)
        else:
            n = -(-len(clause) // limit)
            size = -(-len(clause) // n)
            out += [clause[i:i + size] for i in range(0, len(clause), size)]
    return out


def fit_screen(cues: list[Cue], limit: int) -> list[Cue]:
    """一句台词一条字幕,中文没有空格 libass 不会折行,长句直接冲出画面(实战第 2 段 26 字)。
    超过 limit 字的拆成几条首尾相接的字幕,时间按各块念的字数(去标点)分 —— 不重新对齐,逐字时间戳没存。"""
    out = []
    for c in cues:
        parts = _pieces(c.text, limit) if len(c.text) > limit else [c.text]
        weights = [max(1, len(_clean(p))) for p in parts]
        t = c.start
        for i, (p, w) in enumerate(zip(parts, weights)):
            end = c.end if i == len(parts) - 1 else round(t + (c.end - c.start) * w / sum(weights), 2)
            out.append(Cue(p, t, end))
            t = end
    return out


def episode_srt(parts: list[tuple[list[Cue], float]]) -> str:
    """整集字幕:parts = [(本段字幕, 本段在成片里的起点秒)],编号连续。"""
    out, n = [], 1
    for cues, offset in parts:
        out.append(srt(cues, offset, n))
        n += len(cues)
    return "".join(out)
