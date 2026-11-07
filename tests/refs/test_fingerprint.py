# -*- coding: utf-8 -*-
from tools.refs import fingerprint


def _content(refs, text):
    with refs.write() as c:
        c.execute("INSERT OR IGNORE INTO ref_source (key) VALUES ('jjwxc')")
        c.execute("INSERT OR IGNORE INTO ref_work (id, source, external_id, first_seen) VALUES (1,'jjwxc','1','t')")
        cid = c.execute("INSERT INTO ref_content (work_id, kind, path, sha256, origin, verdict, fetched_at, due_at, hard_due_at)"
                        " VALUES (1,'chapter','p','s','o','ok','t','t','t')").lastrowid
    fingerprint.add(refs, cid, text)
    return cid


def test_windows_skip_punctuation_and_spaces():
    assert fingerprint.normalize("青州,叶家。 abc 123") == "青州叶家abc123"
    assert len(fingerprint.windows("一二三四五六七八九十甲乙丙")) == 2       # 13 字 → 2 个 12 字窗口


def test_overlap_found_for_twelve_identical_chars(refs):
    cid = _content(refs, "一名十六七岁的少年看着眼前仙宝阁的主事赵修平静道")
    hits = fingerprint.overlaps(refs, "他说:少年看着眼前仙宝阁的主事赵修,然后走了")
    assert hits and hits[0]["content_id"] == cid and hits[0]["length"] >= 12


def test_eleven_chars_is_not_an_overlap(refs):
    _content(refs, "一名十六七岁的少年看着眼前仙宝阁的主事赵修平静道")
    assert fingerprint.overlaps(refs, "看着眼前仙宝阁的主事赵") == []
