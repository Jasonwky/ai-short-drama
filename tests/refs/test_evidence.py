# -*- coding: utf-8 -*-
import json

import pytest

from tools.refs import evidence

CHAPTER = "第一章 雪夜\n\n沈清辞推开门,风雪灌进来。\n“你终于来了，”顾长风说，“我等了三年。”\n她没有回答。"


def _content(refs, root, kind, body, *, verdict="ok", purged=""):
    with refs.write() as c:
        if not refs.conn.execute("SELECT 1 FROM ref_source WHERE key = 's'").fetchone():
            c.execute("INSERT INTO ref_source (key) VALUES ('s')")
            c.execute("INSERT INTO ref_work (id, source, external_id, first_seen) VALUES (1, 's', '1', 't')")
        rel = f"c/{kind}-{len(body)}.{'txt' if kind == 'chapter' else 'json'}"
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8")
        cid = c.execute("INSERT INTO ref_content (work_id, kind, unit, path, sha256, origin, verdict, fetched_at, due_at,"
                        " hard_due_at, purged_at) VALUES (1, ?, 1, ?, 's', 'o', ?, 't', 't', 't', ?)",
                        (kind, rel, verdict, purged)).lastrowid
    return refs.conn.execute("SELECT * FROM ref_content WHERE id = ?", (cid,)).fetchone()


def _transcript(text, start=10.0, step=0.3):
    chars = [[ch, round(start + i * step, 2), round(start + (i + 1) * step, 2)] for i, ch in enumerate(text)]
    return json.dumps({"text": text, "chars": chars}, ensure_ascii=False)


def test_unit_text_reads_chapter_and_transcript(refs, tmp_path):
    u = evidence.unit_text(refs, tmp_path, _content(refs, tmp_path, "chapter", CHAPTER))
    assert u == {"kind": "chapter", "text": CHAPTER, "chars": None}
    u = evidence.unit_text(refs, tmp_path, _content(refs, tmp_path, "transcript", _transcript("谁派你来的")))
    assert u["kind"] == "transcript" and u["text"] == "谁派你来的" and u["chars"][0] == ["谁", 10.0, 10.3]


def test_unit_text_refuses_other_kinds_rejected_and_purged(refs, tmp_path):
    for kind, kw in (("review", {}), ("chapter", {"verdict": "rejected"}), ("chapter", {"purged": "2026-11-07"})):
        row = _content(refs, tmp_path, kind, CHAPTER + kind + str(kw), **kw)
        with pytest.raises(ValueError):
            evidence.unit_text(refs, tmp_path, row)


def test_chapter_quote_is_verbatim_with_width_and_whitespace_folded():
    u = {"kind": "chapter", "text": CHAPTER, "chars": None}
    # 原文用全角逗号,模型抄成半角;原文的换行也不算
    assert evidence.check_quote(u, "“你终于来了,”顾长风说") == (True, "“你终于来了，”顾长风说", None)
    assert evidence.check_quote(u, "风雪灌进来。 “你终于")[0]
    assert evidence.check_quote(u, "顾长风笑着说") == (False, None, None)
    assert evidence.check_quote(u, "") == (False, None, None)


TRANSCRIPT = "把你打开好了我冲着杏州的赶紧过来办事幸好我一直撞下是是谁把本王的球换成了陆西酒小姐出闯户跑啊好大的胆子谁派你来的"


def test_transcript_quote_matches_by_pinyin_and_gives_the_second():
    u = {"kind": "transcript", "text": TRANSCRIPT, "chars": json.loads(_transcript(TRANSCRIPT))["chars"]}
    at_char = TRANSCRIPT.index("好大的胆子")
    ok, restored, at = evidence.check_quote(u, "好大的胆子,谁派你来的")       # 带标点照抄
    assert ok and restored == "好大的胆子谁派你来的" and at == round(10.0 + at_char * 0.3, 2)
    ok, restored, at = evidence.check_quote(u, "是谁把本王的裘换成了")          # 同音字:球 / 裘,Whisper 常见
    assert ok and restored == "是谁把本王的球换成了" and at == round(10.0 + TRANSCRIPT.index("是谁把") * 0.3, 2)
    assert evidence.check_quote(u, "我要杀了你们全家老小") == (False, None, None)
    assert evidence.check_quote(u, "") == (False, None, None)


def test_quote_threshold_is_the_existing_speech_pass_line():
    from tools.serve import ttser, voicer
    assert evidence.QUOTE_HIT == voicer.HIT_OK == ttser.HIT_OK


def test_check_names_chapter_verbatim_transcript_by_pinyin():
    ch = {"kind": "chapter", "text": CHAPTER, "chars": None}
    assert evidence.check_names(ch, ["沈清辞", "顾长风", "林婉"]) == ["林婉"]
    tr = {"kind": "transcript", "text": TRANSCRIPT, "chars": json.loads(_transcript(TRANSCRIPT))["chars"]}
    # 转写里是「陆西酒」,人名写「陆惜久」同音 → 算在;「沈清辞」不在
    assert evidence.check_names(tr, ["陆惜久", "沈清辞", ""]) == ["沈清辞", ""]
