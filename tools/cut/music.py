# -*- coding: utf-8 -*-
"""配乐:人上传的**有许可的**音乐,导出时按 cue 混进成片(specs/2026-10-20-配乐白屏与替换神情-design.md 一)。

不做 AI 生成:MusicGen 权重 CC-BY-NC(许可证红线),且要开卡。一集可挂多条 cue,每条是成片时间轴上的一段。

增益包络(成片时间轴上的线性幅度,纯函数,可单测):
- cue 之外为 0;cue 之内 = 10^((gain_db + 压低) / 20) × 淡入淡出;
- 台词区间里再压低 duck_db,台词前后各 RAMP 秒过渡(按 dB 线性);两句间隔不足 2×RAMP 的连成一段 —— 不在句间来回抽;
- 淡入 / 淡出按幅度线性。
同一个包络既生成 ffmpeg 的音量表达式,也画面板上的曲线 —— 两边算的是同一个东西。
"""
from __future__ import annotations

import hashlib
import json
import math
import tempfile
from pathlib import Path

from tools.cut import media, subtitles
from tools.drama import repo
from tools.drama.db import Store
from tools.serve.dispatch import sha256_file

RAMP = 0.3                      # 台词前后的过渡(秒)
EXTS = (".mp3", ".wav", ".m4a", ".flac", ".ogg")
MAX_BYTES = 200 * 1024 * 1024
NOTE = "人工提供,非 AI 生成"
FIELDS = ("start_sec", "end_sec", "gain_db", "duck_db", "fade_in", "fade_out")


# ── 纯函数:包络 ──────────────────────────────────────────────

def merge_speech(speech: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for a, b in sorted(speech):
        if out and a - out[-1][1] < 2 * RAMP:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _duck(cue, spans: list[tuple[float, float]], t: float) -> float:
    d = cue["duck_db"]
    for a, b in spans:
        if a - RAMP < t < b + RAMP:
            if t < a:
                return d * (t - (a - RAMP)) / RAMP
            if t > b:
                return d * (b + RAMP - t) / RAMP
            return d
    return 0.0


def db_at(cue, speech: list[tuple[float, float]], t: float) -> float:
    """t 时刻的 dB 部分(音量 + 压低),不含淡入淡出。"""
    return cue["gain_db"] + _duck(cue, merge_speech(speech), t)


def _fade(cue, t: float) -> float:
    f = 1.0
    if cue["fade_in"] > 0:
        f = min(f, (t - cue["start_sec"]) / cue["fade_in"])
    if cue["fade_out"] > 0:
        f = min(f, (cue["end_sec"] - t) / cue["fade_out"])
    return max(0.0, min(1.0, f))


def gain_at(cue, speech: list[tuple[float, float]], t: float) -> float:
    """t 时刻(成片时间)这条音乐的线性增益。"""
    if not cue["start_sec"] <= t < cue["end_sec"]:
        return 0.0
    return 10 ** (db_at(cue, speech, t) / 20) * _fade(cue, t)


def breakpoints(cue, speech: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """dB 部分的折线拐点 [(t, dB)],覆盖 [起点, 终点];拐点之间线性,与 db_at 逐点一致。"""
    s, e = cue["start_sec"], cue["end_sec"]
    spans = merge_speech(speech)
    ts = {s, e} | {x for a, b in spans for x in (a - RAMP, a, b, b + RAMP) if s < x < e}
    return [(t, cue["gain_db"] + _duck(cue, spans, t)) for t in sorted(ts)]


def filter_expr(cue, speech: list[tuple[float, float]]) -> str:
    """ffmpeg volume 滤镜的表达式(eval=frame,t = 成片时间)。dB 折线写成「区间指示 × 线性插值」之和,不嵌套。"""
    def f(x: float) -> str:
        return f"{x:.4f}"
    pts = breakpoints(cue, speech)
    terms = [f"gte(t,{f(t0)})*lt(t,{f(t1)})*({f(g0)}+({f(g1 - g0)})*(t-{f(t0)})/{f(t1 - t0)})"
             for (t0, g0), (t1, g1) in zip(pts, pts[1:]) if t1 > t0]
    s, e = cue["start_sec"], cue["end_sec"]
    fade = "1"
    if cue["fade_in"] > 0:
        fade = f"min({fade},(t-{f(s)})/{f(cue['fade_in'])})"
    if cue["fade_out"] > 0:
        fade = f"min({fade},({f(e)}-t)/{f(cue['fade_out'])})"
    return f"pow(10,({'+'.join(terms) or f(cue['gain_db'])})/20)*clip({fade},0,1)*gte(t,{f(s)})*lt(t,{f(e)})"


def speech_of(parts: list[tuple[list[subtitles.Cue], float]]) -> list[tuple[float, float]]:
    """台词区间(成片时间):各段已对齐字幕的起止 + 段落在成片里的偏移。"""
    return [(round(off + c.start, 3), round(off + c.end, 3)) for cues, off in parts for c in cues]


def curve_db(cue, speech: list[tuple[float, float]], floor_db: float = -50.0, step: float = 0.05) -> list[tuple[float, float]]:
    """画曲线用:cue 内每 step 秒一个点的实际增益(dB,低于 floor_db 的按 floor_db 画)。"""
    s, e = cue["start_sec"], cue["end_sec"]
    n = max(1, math.ceil((e - s) / step))
    out = []
    for i in range(n + 1):
        t = min(e - 1e-6, s + i * step)
        g = gain_at(cue, speech, t)
        out.append((t, max(floor_db, 20 * math.log10(g)) if g > 0 else floor_db))
    return out


def validate(cue, license: str) -> None:
    """人填的数字与许可说明。所有原因一次报全。"""
    why = []
    if not license.strip():
        why.append("许可说明是★必填:写清这首音乐的来源与许可(如曲库名 + 授权编号)")
    if cue["start_sec"] < 0:
        why.append("起点不能为负")
    if cue["end_sec"] <= cue["start_sec"]:
        why.append("终点要晚于起点")
    if cue["gain_db"] > 0:
        why.append("音量最高 0 dB(再高会削波)")
    if cue["duck_db"] > 0:
        why.append("台词期间的压低量要 ≤ 0 dB")
    if cue["fade_in"] < 0 or cue["fade_out"] < 0 or cue["fade_in"] + cue["fade_out"] > cue["end_sec"] - cue["start_sec"]:
        why.append("淡入淡出不能为负,两者之和不能超过这条配乐的时长")
    if why:
        raise ValueError(";".join(why))


# ── 读回自检用的区间(纯函数)──────────────────────────────

OUTSIDE_MARGIN = 0.2            # cue 两端各让开这么多再量「cue 外」:AAC 一帧约 21 ms,前后帧会互相渗一点
MIN_GAP = 0.3                   # 短于它的 cue 外区间不量


def plateau(cue, speech: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """cue 里音乐处在满音量(= gain_db)的区间:去掉淡入淡出、台词及其前后过渡。读回自检在这里量音乐在不在。"""
    pieces = [(cue["start_sec"] + cue["fade_in"], cue["end_sec"] - cue["fade_out"])]
    for a, b in merge_speech(speech):
        cut = []
        for x, y in pieces:
            cut += [p for p in ((x, min(y, a - RAMP)), (max(x, b + RAMP), y)) if p[1] > p[0]]
        pieces = cut
    return [(round(x, 3), round(y, 3)) for x, y in pieces if y > x]


def outside(cues, total: float) -> list[tuple[float, float]]:
    """没有任何 cue 碰到的区间(两端各让开 OUTSIDE_MARGIN)。"""
    out, t = [], 0.0
    for c in sorted(cues, key=lambda c: c["start_sec"]):
        if c["start_sec"] - OUTSIDE_MARGIN - t >= MIN_GAP:
            out.append((round(t, 3), round(c["start_sec"] - OUTSIDE_MARGIN, 3)))
        t = max(t, c["end_sec"] + OUTSIDE_MARGIN)
    if total - t >= MIN_GAP:
        out.append((round(t, 3), round(total, 3)))
    return out


# ── 本机:上传、挂 cue、检查、混音、试听、读回 ─────────────────

def stage(data_root: Path, data: bytes, filename: str) -> str:
    """校验并落盘到 `assets/music/<sha256 前 16 位><扩展名>`,返回相对 data_root 的路径。ffprobe 读不出音轨就不收、不留文件。"""
    ext = Path(filename).suffix.lower()
    if ext not in EXTS:
        raise ValueError(f"只收 {' / '.join(e[1:] for e in EXTS)} 音乐文件(收到 {filename!r})")
    if len(data) > MAX_BYTES:
        raise ValueError(f"音乐文件太大({len(data) / 1024 / 1024:.1f} MB,上限 {MAX_BYTES // 1024 // 1024} MB)")
    rel = f"assets/music/{hashlib.sha256(data).hexdigest()[:16]}{ext}"
    path = Path(data_root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f"staging-{path.name}")          # 扩展名留着:ffprobe 认格式要用
    part.write_bytes(data)
    try:
        media.audio(part)
    except media.CutError as e:
        part.unlink()
        raise ValueError(f"ffprobe 读不出音轨,不收这个文件:{e}") from e
    part.replace(path)
    return rel


def add(store: Store, data_root: Path, episode_id: int, rel: str, license: str, *, start_sec: float,
        end_sec: float | None = None, until: float | None = None, **fields) -> int:
    """挂一条 cue。终点不填 = 起点 + 音乐时长,不超过 until(成片时长,已知时);淡入淡出不填 = 各 1 秒,短 cue 按时长的 1/4。"""
    f = Path(data_root) / rel
    if end_sec is None:
        end_sec = round(start_sec + media.audio(f)[0], 3)
        if until is not None and start_sec < until:
            end_sec = min(end_sec, until)
    fade = round(min(1.0, (end_sec - start_sec) / 4), 2)
    row = {"start_sec": start_sec, "end_sec": end_sec, "gain_db": -14.0, "duck_db": -10.0, "fade_in": fade,
           "fade_out": fade, **fields}
    validate(row, license)
    return repo.add_music_cue(store, episode_id, path=rel, sha256=sha256_file(f), license=license.strip(), **row)


def edit(store: Store, cue_id: int, license: str, **fields) -> None:
    row = {**{k: repo.get(store, "music_cue", cue_id)[k] for k in FIELDS}, **fields}
    validate(row, license)
    repo.update(store, "music_cue", cue_id, license=license.strip(), **fields)


def snapshot(store: Store, episode_id: int) -> list[dict]:
    """导出时快照的配乐(记进导出记录,之后改 cue 不影响这次导出)。"""
    return [{k: c[k] for k in ("id", "path", "sha256", "license", *FIELDS)} for c in repo.music_cues(store, episode_id)]


def problems(data_root: Path, cues, total: float | None) -> list[str]:
    """导出前的轻量检查。total = 成片时长(段落视频不齐时为 None,不判终点)。"""
    out = []
    for c in cues:
        f = Path(data_root) / c["path"]
        if not f.is_file():
            out.append(f"配乐 #{c['id']} 的文件不在本机:{c['path']}")
            continue
        if sha256_file(f) != c["sha256"]:
            out.append(f"配乐 #{c['id']} 的文件内容与挂上时记的哈希对不上(文件被替换过):{c['path']}")
            continue
        if total is not None and c["end_sec"] > total + 0.05:
            out.append(f"配乐 #{c['id']} 的终点 {c['end_sec']:g} 秒超出成片时长 {total:.2f} 秒")
        dur = media.audio(f)[0]
        if dur < c["end_sec"] - c["start_sec"] - 0.05:
            out.append(f"配乐 #{c['id']} 的音乐只有 {dur:.2f} 秒,短于设定的 {c['end_sec'] - c['start_sec']:g} 秒"
                       "(后面会是静音):把终点往前挪")
    return out


def timeline(store: Store, data_root: Path, episode_id: int) -> tuple[float, list[tuple[float, float]], bool]:
    """(成片时长, 台词区间, 是否精确)。段落有视频按视频实长;没有的按设定时长估(不精确,面板上标出来)。"""
    root = Path(data_root)
    parts, off, exact = [], 0.0, True
    for s in store.conn.execute("SELECT * FROM segment WHERE episode_id = ? ORDER BY number", (episode_id,)):
        if s["video_path"] and (root / s["video_path"]).is_file():
            dur = media.probe(root / s["video_path"]).duration
        else:
            dur, exact = s["duration_sec"] or 0.0, False
        sub = root / s["subtitle_path"] if s["subtitle_path"] else None
        parts.append((subtitles.parse_srt(sub.read_text(encoding="utf-8")) if sub and sub.is_file() else [], off))
        off += dur
    return round(off, 3), speech_of(parts), exact


def mixes(data_root: Path, cues, speech: list[tuple[float, float]]) -> list[media.Music]:
    return [media.Music(Path(data_root) / c["path"], c["start_sec"], c["end_sec"], filter_expr(c, speech))
            for c in cues]


def _clips(store: Store, data_root: Path, episode_id: int) -> list[Path]:
    segs = store.conn.execute("SELECT number, video_path FROM segment WHERE episode_id = ? ORDER BY number",
                              (episode_id,)).fetchall()
    missing = [s["number"] for s in segs if not s["video_path"] or not (Path(data_root) / s["video_path"]).is_file()]
    if missing:
        raise ValueError(f"段落 {missing} 还没有视频 —— 没法按成片时间轴试听")
    if not segs:
        raise ValueError("这一集还没有段落")
    return [Path(data_root) / s["video_path"] for s in segs]


def preview_path(store: Store, cue, speech: list[tuple[float, float]]) -> str:
    """试听文件的路径:按 cue 参数 + 各段视频 + 台词区间取哈希 —— 改了任何一样就是新文件,不会把旧的当新的听。"""
    segs = [s[0] for s in store.conn.execute("SELECT video_path FROM segment WHERE episode_id = ? ORDER BY number",
                                             (cue["episode_id"],))]
    key = json.dumps([cue["sha256"], [cue[k] for k in FIELDS], segs, speech])
    return f"outputs/preview/music-{cue['id']}-{hashlib.sha256(key.encode()).hexdigest()[:12]}.m4a"


def preview(store: Store, data_root: Path, cue_id: int) -> str:
    """试听:本机 ffmpeg 只混音频(不碰视频),截出这条 cue 的那一段。进 outputs/preview/,不入库。"""
    cue = repo.get(store, "music_cue", cue_id)
    clips = _clips(store, data_root, cue["episode_id"])
    _, speech, _ = timeline(store, data_root, cue["episode_id"])
    rel = preview_path(store, cue, speech)
    out = Path(data_root) / rel
    if not out.is_file():
        media.mix_audio(clips, mixes(data_root, [cue], speech), out, trim=(cue["start_sec"], cue["end_sec"]))
    return rel


# 读回自检的阈值:标定见 spike-results/2026-10-20-配乐读回自检阈值标定.md
SR = 16000                # 读回用的采样率(单声道)
WANT_MARGIN_DB = 6.0      # cue 内:差异电平不低于「按包络独立算出的音乐电平」减这么多
OUTSIDE_BELOW_DB = 25.0   # cue 外:差异电平要比原音轨低这么多
FLOOR_DB = -80.0          # 绝对下限(dBFS):原音轨本身近乎无声时用它


def _db(sq: float, n: int) -> float:
    return 10 * math.log10(sq / n) - 20 * math.log10(32768) if n and sq > 0 else -math.inf


def readback(data_root: Path, clips: list[Path], cues, speech: list[tuple[float, float]],
             final: Path) -> tuple[bool, str]:
    """对成片文件实测配乐。参照 = 同一条音频链、不混配乐把原音轨重编一遍:cue 之前与成片逐样本相同,
    cue 之后只剩 AAC 码率分配带来的小差异(实测比原音轨低 38 dB 以上)。
    - cue 内(满音量区间,台词与淡入淡出扣掉):成片 − 参照 的电平 ≥ 按包络独立算出的音乐电平 − 6 dB → 音乐在;
    - cue 外:成片 − 参照 的电平 ≤ 原音轨电平 − 25 dB(或低于 −80 dBFS)→ 没漏混、原音轨没被压低。"""
    if not cues:
        return True, "本集无配乐"
    with tempfile.TemporaryDirectory() as d:
        ref = Path(d) / "ref.m4a"
        media.mix_audio(clips, [], ref)
        r = media.pcm(ref)
    x = media.pcm(final)
    n = min(len(x), len(r))
    ok, notes = True, []
    for c in cues:
        pieces = plateau(c, speech)
        if sum(b - a for a, b in pieces) < 0.3:              # 整条几乎都在台词里:量整条,按包络算预期
            pieces = [(c["start_sec"], c["end_sec"])]
        song = media.pcm(Path(data_root) / c["path"], duration=c["end_sec"] - c["start_sec"])
        s0 = round(c["start_sec"] * SR)
        steps = [gain_at(c, speech, c["start_sec"] + (i + 0.5) / 100)            # 每 10 ms 一个增益
                 for i in range(math.ceil((c["end_sec"] - c["start_sec"]) * 100))]
        dsq = wsq = 0.0
        cnt = 0
        for a, b in pieces:
            for k in range(round(a * SR), min(round(b * SR), n)):
                j = k - s0
                w = song[j] * steps[min(j * 100 // SR, len(steps) - 1)] if 0 <= j < len(song) else 0.0
                dsq += (x[k] - r[k]) ** 2
                wsq += w * w
                cnt += 1
        got, want = _db(dsq, cnt), _db(wsq, cnt)
        span = f"{sum(b - a for a, b in pieces):.1f} 秒"
        if want < FLOOR_DB:
            ok = False
            notes.append(f"配乐 #{c['id']}:量的 {span} 里音乐本身近乎无声({want:.1f} dBFS),确认不了音乐在"
                         " —— 调整起止避开静音段")
        elif got < want - WANT_MARGIN_DB:
            ok = False
            notes.append(f"配乐 #{c['id']}:非台词区间 {span} 成片与原音轨的差异 {got:.1f} dBFS,"
                         f"低于预期音乐电平 {want:.1f} − {WANT_MARGIN_DB:g} dB —— 音乐没混进去")
        else:
            notes.append(f"配乐 #{c['id']}:非台词区间 {span} 差异 {got:.1f} dBFS(预期音乐 {want:.1f})")
    gaps = outside(cues, n / SR)
    worst = -math.inf
    for a, b in gaps:
        ks = range(round(a * SR), min(round(b * SR), n))
        got = _db(sum((x[k] - r[k]) ** 2 for k in ks), len(ks))
        limit = max(_db(sum(r[k] * r[k] for k in ks), len(ks)) - OUTSIDE_BELOW_DB, FLOOR_DB)
        if got > limit:
            ok = False
            notes.append(f"cue 外 {a:.1f}–{b:.1f} 秒:成片与原音轨差异 {got:.1f} dBFS,超过上限 {limit:.1f}"
                         " —— 原音轨被改动,或配乐漏到了 cue 外")
        worst = max(worst, got - limit)
    if not gaps:
        notes.append("配乐覆盖全片,没有 cue 外区间可比")
    elif worst <= 0:
        notes.append(f"cue 外 {len(gaps)} 段:差异都在上限之下" + (f"(最近的差 {-worst:.1f} dB)" if worst > -math.inf
                                                          else "(逐样本相同)"))
    return ok, ";".join(notes)
