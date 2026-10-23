# -*- coding: utf-8 -*-
"""成片导出:拼接 → 显式标识 → 隐式元数据 → 溯源报告 → **读回自检**。

一次导出出两个文件(设计 §6.8):
- `labeled.mp4`:带显式标识「AI 生成」(左上角、**全程**;标准下限是起始画面 ≥ 2 s);
- `plain.mp4`:无显式标识版(办法允许应用户申请提供),隐式标识照样写。
两者共用同一个 ProduceID(同一个作品),各自的 ReservedCode1 是各自视频流的 SHA-256。
两版都烧录字幕(各段对齐出的 SRT 按段落起点平移、拼成整集 `subtitles.srt`,随成片交付);
有台词的段落没有字幕 = 前提不满足,不开工。

配乐(人上传的有许可的音乐,cut/music.py)两版都按包络混入;begin 时快照进导出记录,之后改 cue 不影响这次导出。
白屏的人工确认放行也在 begin 时快照,记在 inputs_json 每段那一项里(与它绑定的视频 sha256 同处一项,run 只认快照)。

两态:`begin` 当场核对前提(★必答的服务提供者编码、每段有视频、尺寸一致、有音轨、溯源成立、
查过的段落没有烧进画面的字幕、没有白屏(人工确认放行过这条视频的除外)、配乐文件与哈希对得上且不超出成片),不满足就报原因不开工;`run` 在后台跑(一集 1–3 分钟),结束时整行锁定(触发器)。
**自检不过 = 导出失败**:文件留着给人查原因,但状态是 failed,界面标「不可发布」。
"""
from __future__ import annotations

import json
from pathlib import Path

from tools.cut import aigc, blankscan, media, music, provenance, review, subtitles, textscan
from tools.drama import repo, rules
from tools.drama.db import Store, now
from tools.serve.dispatch import sha256_file

SETTING = "content_producer"
COMMENT = "Powered by MiniMax H3"                     # MiniMax H3 许可证 III.3.a,随文件走
ALLOWED_TAGS = {"major_brand", "minor_version", "compatible_brands", "encoder", aigc.TAG, "comment"}


def saved_producer(store: Store) -> str:
    row = store.conn.execute("SELECT value FROM setting WHERE key = ?", (SETTING,)).fetchone()
    return row[0] if row else ""


def get(store: Store, export_id: int):
    row = store.conn.execute("SELECT * FROM export WHERE id = ?", (export_id,)).fetchone()
    if row is None:
        raise KeyError(f"导出 {export_id} 不存在")
    return row


def list_for(store: Store, episode_id: int) -> list:
    return store.conn.execute("SELECT * FROM export WHERE episode_id = ? ORDER BY id", (episode_id,)).fetchall()


def _segments(store: Store, episode_id: int) -> list:
    return store.conn.execute("SELECT * FROM segment WHERE episode_id = ? ORDER BY number", (episode_id,)).fetchall()


def _lines(seg) -> list[str]:
    return [line for _, line in rules.dialogue_lines(seg["description"])]


def _context(seg) -> str:
    """烧字幕检测的比对范围:描述(含台词、动作)+ 视频提示词 —— 画面里出现这些字就是模型把提示词画进去了。"""
    return f"{seg['description'] or ''}\n{seg['video_prompt'] or ''}"


def readiness(store: Store, data_root: Path, episode_id: int) -> list[str]:
    """面板上的轻量检查(不读视频内容):缺什么。空 = 可以点导出。"""
    segs = _segments(store, episode_id)
    problems = [] if segs else ["这一集还没有段落"]
    for s in segs:
        if not s["video_path"]:
            problems.append(f"段落 {s['number']} 还没有视频")
        elif not (Path(data_root) / s["video_path"]).is_file():
            problems.append(f"段落 {s['number']} 的视频文件不在本机:{s['video_path']}")
        if s["subtitle_path"]:
            if not (Path(data_root) / s["subtitle_path"]).is_file():
                problems.append(f"段落 {s['number']} 的字幕文件不在本机:{s['subtitle_path']}")
        elif rules.dialogue_lines(s["description"]):
            problems.append(f"段落 {s['number']} 有台词但还没有字幕(在「视频」面板点「对齐字幕」)")
        if s["video_path"] and (Path(data_root) / s["video_path"]).is_file():
            found = textscan.cached(data_root, s["video_path"], _lines(s), _context(s))       # 只读缓存:没查过的在导出时查
            if found:
                problems.append(textscan.problem(s["number"], found))
            blank = blankscan.cached(data_root, s["video_path"])                                # 同上:只读缓存
            if blank and blankscan.override_for(store, data_root, s) is None:                   # 人工确认放行过这条视频的不拦
                problems.append(blankscan.problem(s["number"], blank))
            r = review.review_for(store, data_root, s)
            if r is None or not review.complete(r):                                           # 人逐项看过才许导
                problems.append(review.problem(s["number"], r))
    try:
        media.require_ffmpeg()
    except ValueError as e:
        problems.append(str(e))
    else:
        videos = [Path(data_root) / s["video_path"] for s in segs if s["video_path"]]
        total = (sum(media.probe(v).duration for v in videos)
                 if segs and len(videos) == len(segs) and all(v.is_file() for v in videos) else None)
        problems += music.problems(data_root, repo.music_cues(store, episode_id), total)
    if not media.FONT.is_file():
        problems.append(f"找不到显式标识用的字体 {media.FONT}(Noto Sans SC,SIL OFL)")
    return problems


def begin(store: Store, data_root: Path, episode_id: int, producer: str) -> int:
    root = Path(data_root)
    producer = producer.strip()
    problems = aigc.producer_problems(producer) + readiness(store, root, episode_id)
    if problems:
        raise ValueError(";".join(problems))
    segs = _segments(store, episode_id)
    probes = {s["number"]: media.probe(root / s["video_path"]) for s in segs}
    sizes = {(p.width, p.height) for p in probes.values()}
    if len(sizes) != 1:
        problems.append(f"各段视频尺寸不一致:{ {n: (p.width, p.height) for n, p in probes.items()} }")
    problems += [f"段落 {n} 的视频没有音轨" for n, p in probes.items() if not p.has_audio]
    videos = [{"segment_id": s["id"], "number": s["number"], "video_path": s["video_path"],
               "sha256": sha256_file(root / s["video_path"]), "subtitle_path": s["subtitle_path"],
               "subtitle_sha256": sha256_file(root / s["subtitle_path"]) if s["subtitle_path"] else None,
               "blank_override": blankscan.snapshot(blankscan.override_for(store, root, s)),
               "review": review.snapshot(review.review_for(store, root, s))}
              for s in segs]
    cues = music.snapshot(store, episode_id)
    problems += provenance.problems(provenance.build(store, root, episode_id, produce_id="", producer=producer,
                                                     videos=videos, music=cues))
    if problems:
        raise ValueError(";".join(problems))
    with store.write() as c:
        c.execute("INSERT INTO setting (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                  (SETTING, producer))
        return c.execute("INSERT INTO export (episode_id, produce_id, content_producer, inputs_json, music_json,"
                         " created_at) VALUES (?, ?, ?, ?, ?, ?)",
                         (episode_id, aigc.new_produce_id(), producer, json.dumps(videos, ensure_ascii=False),
                          json.dumps(cues, ensure_ascii=False), now())).lastrowid


def _finish(store: Store, export_id: int, state: str, **fields) -> None:
    sets = {"state": state, "finished_at": now(), **fields}
    with store.write() as c:
        c.execute(f"UPDATE export SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                  (*sets.values(), export_id))


def fail(store: Store, export_id: int, error: str) -> None:
    if not error.strip():
        raise ValueError("失败必须写明原因")
    _finish(store, export_id, "failed", error=error)


def fail_stale(store: Store) -> int:
    """控制台重启时,上次没跑完的导出不会再有人接着跑:标失败,别让它永远显示「导出中」。"""
    ids = [r[0] for r in store.conn.execute("SELECT id FROM export WHERE state = 'running'")]
    for i in ids:
        fail(store, i, "控制台重启,这次导出中断了(产物不完整,不可发布)—— 请重新导出")
    return len(ids)


def run(store: Store, data_root: Path, export_id: int) -> None:
    row = get(store, export_id)
    root = Path(data_root)
    videos = json.loads(row["inputs_json"])
    clips = [root / v["video_path"] for v in videos]
    changed = [v["number"] for v, c in zip(videos, clips) if sha256_file(c) != v["sha256"]]
    if changed:
        raise media.CutError(f"段落 {changed} 的视频在点导出之后被换掉了 —— 请重新导出")
    changed = [v["number"] for v in videos
               if v.get("subtitle_path") and sha256_file(root / v["subtitle_path"]) != v["subtitle_sha256"]]
    if changed:
        raise media.CutError(f"段落 {changed} 的字幕在点导出之后被换掉了 —— 请重新导出")
    cues = json.loads(row["music_json"])
    changed = [c["id"] for c in cues if not (root / c["path"]).is_file() or sha256_file(root / c["path"]) != c["sha256"]]
    if changed:
        raise media.CutError(f"配乐 {changed} 的文件在点导出之后被换掉了 —— 请重新导出")
    burned = []
    for v in videos:                       # 白屏检测(ffmpeg,每段一两秒)+ 烧字幕检测(OCR,每段约半分钟),结果都缓存:命中就不导出
        blank = blankscan.check(root, v["video_path"])
        released = v.get("blank_override") and v["blank_override"]["sha256"] == v["sha256"]    # 只认 begin 时的快照
        if blank and not released:
            burned.append(blankscan.problem(v["number"], blank))
        seg = repo.get(store, "segment", v["segment_id"])
        found = textscan.check(root, v["video_path"], _lines(seg), _context(seg))
        if found:
            burned.append(textscan.problem(v["number"], found))
    if burned:
        raise media.CutError(";".join(burned))
    out = Path("outputs/exports") / row["produce_id"]
    first = media.probe(clips[0])
    label = media.Label.for_size(first.width, first.height)
    rel = {"labeled": (out / "labeled.mp4").as_posix(), "plain": (out / "plain.mp4").as_posix()}
    srt = episode_subtitles(root, videos)
    srt_path = None
    if srt:
        srt_path = root / out / "subtitles.srt"
        srt_path.parent.mkdir(parents=True, exist_ok=True)
        srt_path.write_text(srt, encoding="utf-8")
    mix = music.mixes(root, cues, music.speech_of([(c, o) for c, o, _ in _parts(root, videos)]))
    for key, lab in (("plain", None), ("labeled", label)):
        raw = root / out / f"{key}.raw.mp4"
        media.encode_episode(clips, raw, lab, subtitles=srt_path, music=mix)
        tag = aigc.build(row["content_producer"], row["produce_id"], media.stream_sha256(raw))
        media.write_tags(raw, root / rel[key], {aigc.TAG: tag, "comment": COMMENT})
        raw.unlink()
    report = provenance.build(store, root, row["episode_id"], produce_id=row["produce_id"],
                              producer=row["content_producer"], videos=videos, music=cues)
    report_rel = (out / "provenance.json").as_posix()
    (root / report_rel).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / out / "provenance.md").write_text(provenance.to_markdown(report), encoding="utf-8")
    checks = check(store, root, row, rel["labeled"], rel["plain"], report_rel, label)
    bad = [c["name"] for c in checks if not c["ok"]]
    _finish(store, export_id, "failed" if bad else "done", labeled_path=rel["labeled"], plain_path=rel["plain"],
            report_path=report_rel, checks_json=json.dumps(checks, ensure_ascii=False),
            error=f"自检不通过:{'、'.join(bad)}(不可发布)" if bad else None)


def _parts(root: Path, videos: list[dict]) -> list[tuple[list[subtitles.Cue], float, Path]]:
    """每段 (字幕, 本段在成片里的起点秒, 本段视频)。起点 = 前面各段视频时长之和(拼接就是按这个接的)。
    长台词按画幅拆成几条(`subtitles.fit_screen`),免得一行冲出画面。"""
    parts, offset = [], 0.0
    for v in videos:
        clip = root / v["video_path"]
        p = media.probe(clip)
        sub = v.get("subtitle_path")
        cues = subtitles.fit_screen(subtitles.parse_srt((root / sub).read_text(encoding="utf-8")),
                                    subtitles.max_chars(p.width, p.height)) if sub else []
        parts.append((cues, offset, clip))
        offset += p.duration
    return parts


def episode_subtitles(root: Path, videos: list[dict]) -> str:
    """整集 SRT;本集没有一句字幕时返回空串(不烧录)。"""
    parts = _parts(root, videos)
    return subtitles.episode_srt([(c, o) for c, o, _ in parts]) if any(c for c, _, _ in parts) else ""


def _subtitle_check(root: Path, videos: list[dict], plain: Path, report_rel: str) -> tuple[bool, str]:
    """整集 SRT 与按各段重拼的逐字相同;第一句字幕的中点,成片底部比原片多出字(真烧进去了)。"""
    parts = _parts(root, videos)
    want = subtitles.episode_srt([(c, o) for c, o, _ in parts]) if any(c for c, _, _ in parts) else ""
    if not want:
        return True, "本集没有台词,不烧字幕"
    saved = root / Path(report_rel).parent / "subtitles.srt"
    if not saved.is_file() or saved.read_text(encoding="utf-8") != want:
        return False, "整集字幕文件缺失,或与各段字幕按段落起点重拼的结果不同"
    cues, offset, clip = next(p for p in parts if p[0])
    mid = (cues[0].start + cues[0].end) / 2
    p = media.probe(plain)
    box = media.diff_bbox(media.gray_frame(plain, offset + mid, p.width, p.height),
                          media.gray_frame(clip, mid, p.width, p.height), p.width)
    if box is None or box[1] < p.height * 0.6:
        return False, f"{offset + mid:.2f}s 处成片底部看不到字幕(差异框 {box})"
    return True, f"{sum(len(c) for c, _, _ in parts)} 句;{offset + mid:.2f}s 处底部有字(差异框 {box})"


def _label_check(labeled: Path, plain: Path, label: media.Label) -> tuple[bool, str]:
    """显式标识:逐时刻比对两版画面,差异框 = 标识。要求:每个时刻都在、在左上角、字高(扣掉描边)≥ 最短边 5%。"""
    p = media.probe(labeled)
    w, h, short = p.width, p.height, min(p.width, p.height)
    times = sorted({0.0, min(1.0, p.duration / 2), p.duration / 2, max(0.0, p.duration - 0.1)})
    notes = []
    for t in times:
        box = media.diff_bbox(media.gray_frame(labeled, t, w, h), media.gray_frame(plain, t, w, h), w)
        if box is None:
            return False, f"{t:.1f}s 处看不到标识"
        x0, y0, x1, y1 = box
        glyph = (y1 - y0 + 1) - 2 * label.border
        if glyph < 0.05 * short:
            return False, f"{t:.1f}s 处字高 {glyph}px < 最短边 5%({0.05 * short:.1f}px)"
        if x1 >= w / 2 or y1 >= h / 4:
            return False, f"{t:.1f}s 处标识不在左上角:差异框 {box}"
        notes.append(f"{t:.1f}s 字高 {glyph}px")
    return True, f"{'、'.join(notes)}(最短边 5% = {0.05 * short:.1f}px)"


def check(store: Store, root: Path, row, labeled_rel: str, plain_rel: str, report_rel: str,
          label: media.Label) -> list[dict]:
    """读回自检。每项 PASS / FAIL 带依据;不看代码怎么写的,只看文件里实际有什么。"""
    out = []

    def add(name: str, ok: bool, detail: str) -> None:
        out.append({"name": name, "ok": ok, "detail": detail})

    for key, rel in (("带标识版", labeled_rel), ("无标识版", plain_rel)):
        f = root / rel
        tags = media.format_tags(f)
        probs = aigc.check(tags, producer=row["content_producer"], produce_id=row["produce_id"],
                           reserved1=media.stream_sha256(f))
        add(f"隐式标识(附录 E)· {key}", not probs, ";".join(probs) or "7 个键齐全,注 1 一致,ReservedCode1 = 视频流哈希")
        extra = sorted(set(tags) - ALLOWED_TAGS)
        add(f"原有元数据已清空 · {key}", not extra, f"多出 {extra}" if extra else f"只有 {sorted(tags)}")
    ok, detail = _label_check(root / labeled_rel, root / plain_rel, label)
    add("显式标识(5.4)", ok, detail)
    videos = json.loads(row["inputs_json"])
    ok, detail = _subtitle_check(root, videos, root / plain_rel, report_rel)
    add("字幕", ok, detail)
    cues = json.loads(row["music_json"])
    parts = _parts(root, videos)
    ok, detail = music.readback(root, [p for _, _, p in parts], cues, music.speech_of([(c, o) for c, o, _ in parts]),
                                root / plain_rel)
    add("配乐", ok, detail)
    want = sum(media.probe(root / v["video_path"]).duration for v in videos)
    got = media.probe(root / labeled_rel).duration
    tol = 0.1 * len(videos) + 0.1
    add("时长 = 各段之和", abs(got - want) <= tol, f"成片 {got:.2f}s,各段合计 {want:.2f}s(容差 {tol:.1f}s)")
    saved = json.loads((root / report_rel).read_text(encoding="utf-8"))
    fresh = provenance.build(store, root, row["episode_id"], produce_id=row["produce_id"],
                             producer=row["content_producer"], videos=videos, music=cues)
    probs = provenance.problems(saved)
    same = saved == fresh
    add("溯源报告与 task 表一致", same and not probs,
        ";".join(probs) or ("报告按库重算逐项相同,每个文件哈希与 task 记录一致" if same else "报告与按库重算的结果不同"))
    return out
