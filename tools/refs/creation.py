# -*- coding: utf-8 -*-
"""拆解卡的创作视图(spec 2026-11-07-参考库-design.md 2.4):由代码从审阅视图生成,子项目 2 只读这个。

- 去掉所有引用(quote / 集号 / 剧评序号 / 原文写法),被驳回的假设、人「去掉」的可借鉴 / 不可借鉴不进;
- 名字按 `cast_types` 换成角色类型,**长名先换**(「沈清辞」不会被「沈清」截成半个);平台角色表 / 单元里出场的名字
  与 cast_types 的名字**同音**(拼音一样,`cut.speech.syllables`)的当别名,一起换成同一个类型(转写常出同音字);
  换的时候先换成占位符,换完查一遍有没有残留,有就拒绝(类型里恰好含名字不算残留)。查的名单:单元出场的名字、平台角色表、
  cast_types 的名字、作品名(及去掉标点的写法)—— 这些残留硬拦;
  **原文高频片段**(`frequent_fragments`:单元原文里反复出现、又不是常用词的片段)只是启发式,常用词也会中(真机:「将军」「找到」「瞎子」),
  而人设 / 角色类型没有「去掉」入口 —— 所以它是**提示**:列出来,人勾「看过了,不是专名」(`ack_fragments`)才放行;
- `gate`:创作视图里每段文字拿永久指纹比(`fingerprint.overlaps`),与库中原文连续 ≥ 12 字相同的片段列出来,有就拦批准;
  换名前的视图(`raw`)也比一遍,每个视图再把所有段落连起来比一次(拆成两段的照抄也拦);
- `sha`:格式本期定稳(`FORMAT`),子项目 2 快照全文与 sha256。
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter

from tools.cut import speech
from tools.refs import fingerprint, genres

FORMAT = "ref-creation-v1"

# ---------- 原文高频专名(启发式)----------
# 不是分词、不是命名实体识别:在单元原文里数 2–4 字的汉字片段,出现 ≥ config.PROPER_MIN 次、又不像常用词的,当「可能是专名」。
# 「不像常用词」= 不含下面的虚字 / 代词用字,不含下面的常用词,也不是闭集取值(题材 / 爽点等)的一部分。
# 词表只覆盖代词、虚词、常见动词短语、泛称,宁可漏(漏的由人在审阅页把那条「去掉」)也不追求全;
# 误报由人确认放行(`ack_fragments`),不硬拦 —— 人设 / 角色类型命中时没有别的出路。
STOP_CHARS = set("的了着过吗呢吧啊呀么嘛哦哈我你他她它们这那哪谁啥是在不也都就和与跟及或而且但之其此个些")
COMMON_WORDS = frozenset("""
自己 什么 怎么 为什么 没有 已经 知道 时候 现在 今天 明天 昨天 后来 以后 以前 刚才 一直 一下 一样 一起 一切 一定 一点 一些
可以 可能 应该 不能 能够 需要 觉得 认为 看到 看着 看见 听到 听见 说道 说话 问道 回答 开始 起来 出来 进来 回来 过来 下来 上来
出去 进去 回去 过去 下去 上去 还是 就是 只是 但是 所以 因为 如果 虽然 然后 于是 而是 不过 只要 只有 还有 或者 而且 并且
非常 十分 特别 真的 确实 当然 终于 突然 忽然 马上 立刻 赶紧 慢慢 渐渐 已然 仍然 依然 竟然 居然 果然 原来 其实 本来
东西 事情 地方 时间 样子 声音 眼睛 脸上 心里 手里 身上 身边 面前 门口 外面 里面 后面 前面 上面 下面 旁边 对面 中间
女人 男人 孩子 姑娘 小姐 少爷 公子 夫人 老爷 母亲 父亲 爹娘 女儿 儿子 哥哥 姐姐 弟弟 妹妹 丈夫 妻子 师父 师兄 师姐 师弟 师妹
王爷 皇上 陛下 殿下 大人 主子 奴婢 下人 丫鬟 嬷嬷 先生 老师 同学 老板 医生 警察
主角 女主 男主 反派 对手 观众 剧情 开场 结尾 第一 第二 第三 一个 两个 三个 几个 多少 很多 所有 每个
""".split())
_CJK = re.compile(r"[一-鿿]+")


def frequent_fragments(texts: list[str], *, min_count: int, exclude: list[str] = (), min_len: int = 2) -> list[str]:
    """单元原文里出现 ≥ min_count 次、不含虚字、不含常用词、不是 exclude 里某个取值的一部分的 min_len–4 字片段(按长到短、字面排序)。"""
    counts: Counter[str] = Counter()
    for t in texts:
        for run in _CJK.findall(t):
            for n in range(min_len, 5):
                for i in range(len(run) - n + 1):
                    counts[run[i:i + n]] += 1
    out = [g for g, c in counts.items()
           if c >= min_count and not (set(g) & STOP_CHARS) and not any(w in g for w in COMMON_WORDS)
           and not any(g in e for e in exclude)]
    return sorted(out, key=lambda g: (-len(g), g))


def _title_forms(title: str) -> list[str]:
    plain = re.sub(r"[^\w]", "", title or "")
    return [t for t in dict.fromkeys([(title or "").strip(), plain]) if len(t) >= 2]


def _renamer(review: dict, proper: list[str]):
    pairs = [(c["name"], c["type"]) for c in review["cast_types"]]
    known = {n for n, _ in pairs}
    by_sound = {tuple(speech.syllables(n)): t for n, t in pairs if speech.syllables(n)}
    for alias in dict.fromkeys([*review["roles"], *review["names"]]):       # 同音的别名换成同一个类型(审查 I2b)
        if alias and alias not in known and (t := by_sound.get(tuple(speech.syllables(alias)))) is not None:
            pairs.append((alias, t))
            known.add(alias)
    pairs.sort(key=lambda p: -len(p[0]))
    names = [n for n in dict.fromkeys([*review["names"], *review["roles"], *(p[0] for p in pairs),
                                       *_title_forms(review.get("title", ""))]) if n]
    left: set[str] = set()
    frags: set[str] = set()

    def rename(s: str) -> str:
        for i, (name, _) in enumerate(pairs):
            s = s.replace(name, f"\x00{i}\x00")
        left.update(n for n in names if n in s)
        frags.update(f for f in proper if f in s)
        for i, (_, kind) in enumerate(pairs):
            s = s.replace(f"\x00{i}\x00", kind)
        return s
    return rename, left, frags


def _build(review: dict, decision: dict, rename) -> dict:
    adopted = set(decision["adopt"])
    drop_t, drop_n = set(decision.get("reject_techniques", [])), set(decision.get("reject_not_borrowable", []))
    p = review["persona"]
    return {
        "format": FORMAT,
        "source": review["source"],
        "genre": review["genre"], "genre_premise": genres.TO_PREMISE[review["genre"]],
        "payoff": review["payoff"], "conflict": review["conflict"], "hook_type": review["hook_type"],
        "persona": {k: {"label": rename(p[k]["label"]), "contrast": rename(p[k]["contrast"])} for k in ("protagonist", "antagonist")},
        "cast_types": list(dict.fromkeys(rename(c["type"]) for c in review["cast_types"])),
        "hypotheses": [{"claim": rename(h["claim"]), "evidence": h["evidence"], "label": h["label"]}
                       for i, h in enumerate(review["hypotheses"]) if i in adopted],
        "techniques": [rename(t) for i, t in enumerate(review["techniques"]) if i not in drop_t],
        "not_borrowable": [rename(t) for t in [*(t for i, t in enumerate(review["not_borrowable"]) if i not in drop_n),
                                               *decision.get("extra_not_borrowable", [])]],
        "pacing": review["pacing"],
        "contrast": review["contrast"],
        "coverage": review["coverage"],
    }


def fragments(review: dict, decision: dict, proper: list[str] = ()) -> list[str]:
    """创作视图里出现的原文高频片段(长的在前),给审阅页列出来请人确认。被另一个命中片段包含的半截不单列(「蛟龙渡劫」不再带出「龙渡劫」「蛟龙」)。"""
    rename, _, frags = _renamer(review, list(proper))
    _build(review, decision, rename)
    return sorted((f for f in frags if not any(f != g and f in g for g in frags)), key=lambda f: (-len(f), f))


def view(review: dict, decision: dict, proper: list[str] = ()) -> dict:
    """审阅视图 + 人的选择 → 创作视图。有名字没换掉就 ValueError;出现原文高频片段(`proper`,由调用方从单元原文算)
    而人没勾「看过了,不是专名」(`decision["ack_fragments"]`)也 ValueError。"""
    rename, left, frags = _renamer(review, list(proper))
    out = _build(review, decision, rename)
    problems = []
    if left:
        problems.append(f"还有名字没换成角色类型:{'、'.join(sorted(left))} —— 驳回提到它的假设、"
                        f"在审阅页把那条可借鉴 / 不可借鉴「去掉」,或改掉补充里的写法再批准")
    if frags and not decision.get("ack_fragments"):
        problems.append(f"出现了原文里反复出现的片段(启发式,可能是地名 / 门派 / 道具名,也可能只是常用词):"
                        f"{'、'.join(fragments(review, decision, proper))} —— 是专名就驳回 / 去掉提到它的那条;"
                        f"不是专名就勾「看过了,这些不是专名」再批准")
    if problems:
        raise ValueError("创作视图里" + ";".join(problems))
    return out


def raw(review: dict, decision: dict) -> dict:
    """换名前的创作视图(只给重叠闸门比:名字换掉以后对不上原文的照抄,换名前照样能比出来)。"""
    return _build(review, decision, lambda s: s)


def _strings(x, path: str):
    if isinstance(x, str):
        yield path, x
    elif isinstance(x, dict):
        for k, v in x.items():
            yield from _strings(v, f"{path}.{k}" if path else k)
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from _strings(v, f"{path}[{i}]")


JOINED = "(全部连起来)"


def gate(refs, *views: dict) -> list[dict]:
    """创作视图里与库中原文连续 ≥ fingerprint.WINDOW 字相同的片段:[{field, text, content_id}]。
    每个视图每段文字单独比,再把这个视图所有段落连起来比一次(拆成两段的照抄);同一片段只报一次。"""
    out: list[dict] = []

    def add(field: str, s: str) -> None:
        t = fingerprint.normalize(s)
        for hit in fingerprint.overlaps(refs, s):
            text = t[hit["start"]:hit["start"] + hit["length"]]
            if not any(h["content_id"] == hit["content_id"] and (text in h["text"] or h["text"] in text) for h in out):
                out.append({"field": field, "text": text, "content_id": hit["content_id"]})
    for v in views:
        parts = list(_strings(v, ""))
        for field, s in parts:
            add(field, s)
        add(JOINED, "".join(s for _, s in parts))
    return out


def sha(v: dict) -> str:
    return hashlib.sha256(json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
