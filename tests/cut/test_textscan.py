# -*- coding: utf-8 -*-
from tools.cut import textscan

LINE = "谁准你们动本王的王妃？"


class FakeReader:
    """frame -> [(box, text, conf)];竖条框交给 read_column。"""
    def __init__(self, by_frame, columns=None):
        self.by_frame, self.columns, self.calls = by_frame, columns or {}, 0

    def read(self, frame):
        self.calls += 1
        return self.by_frame.get(frame, [])

    def read_column(self, frame, box):
        return self.columns[frame]


WIDE = [(0, 1100), (700, 1100), (700, 1150), (0, 1150)]       # 底部横条
TALL = [(600, 300), (680, 300), (680, 900), (600, 900)]        # 右侧竖条


def test_dialogue_burned_at_the_bottom_is_a_hit():
    r = FakeReader({"f2": [(WIDE, "谁准你们动本王的王妃", 0.9)]})
    (hit,) = textscan.hits([(0.5, "f1"), (1.0, "f2")], [LINE], r)
    assert (hit.t, hit.matched) == (1.0, 10) and hit.ratio == 1.0


def test_scenery_text_is_not_a_hit():
    # 实拍孔府厅堂:匾额、对联本来就有字 —— 与台词对不上就不算
    r = FakeReader({"f1": [(WIDE, "麟趾呈祥", 0.9), (TALL, "名宗衍庆宜室宜家", 0.8)]},
                   columns={"f1": "名宗衍庆宜室宜家"})
    assert textscan.hits([(0.5, "f1")], [LINE], r) == []


def test_vertical_subtitle_is_read_char_by_char():
    # 实战第 6 段第二轮:竖排字幕,整条识别只出一个「谁」;逐字切开后对上一半
    r = FakeReader({"f1": [(TALL, "谁", 0.1)]}, columns={"f1": "谁准价动本击壬妃"})
    (hit,) = textscan.hits([(5.5, "f1")], [LINE], r)
    assert hit.text == "谁准价动本击壬妃" and hit.matched == 5


def test_a_few_coincidental_chars_are_not_a_hit():
    r = FakeReader({"f1": [(WIDE, "王府", 0.9)]})
    assert textscan.hits([(0.5, "f1")], [LINE], r) == []


def test_low_confidence_text_is_ignored():
    r = FakeReader({"f1": [(WIDE, "谁准你们动本王的王妃", 0.1)]})
    assert textscan.hits([(0.5, "f1")], [LINE], r) == []


def test_scan_is_cached_by_video_hash(tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"v1")
    monkeypatch.setattr(textscan, "_frames", lambda path, out: [(1.0, "f1")])
    r = FakeReader({"f1": [(WIDE, "谁准你们动本王的王妃", 0.9)]})
    assert textscan.check(tmp_path, "v.mp4", [LINE], reader=r)
    assert textscan.check(tmp_path, "v.mp4", [LINE], reader=r) and r.calls == 1     # 第二次读缓存
    assert textscan.cached(tmp_path, "v.mp4", [LINE])[0].t == 1.0
    video.write_bytes(b"v2")                                                          # 换了视频:缓存作废
    assert textscan.cached(tmp_path, "v.mp4", [LINE]) is None
    textscan.check(tmp_path, "v.mp4", [LINE], reader=r)
    assert r.calls == 2


def test_problem_names_segment_second_and_text():
    (hit,) = textscan.hits([(5.5, "f1")], [LINE], FakeReader({"f1": [(WIDE, "谁准你们动本王的王妃", 0.9)]}))
    msg = textscan.problem(6, [hit])
    assert "段落 6" in msg and "5.5" in msg and "谁准你们动本王的王妃" in msg and "重出" in msg


PROMPT = "@萧珩 猛地睁眼，目光如刀扫向前方，冷声开口；床边的@沈青禾 微微一怔，背景是@靖王府正堂"


def test_prompt_words_burned_in_are_a_hit():
    # 烧字幕对照(种子 20261021,不加「画面中没有任何文字」):画面里出现的是提示词里的「冷声开口」「萧珩」,不是台词
    r = FakeReader({"f1": [(WIDE, "冷声开口", 0.9)], "f2": [(WIDE, "萧珩", 0.9)]})
    found = textscan.hits([(1.0, "f1"), (1.5, "f2")], [LINE], r, context=PROMPT)
    assert [(h.t, h.text) for h in found] == [(1.0, "冷声开口"), (1.5, "萧珩")]


def test_scenery_text_is_not_a_hit_against_the_prompt_either():
    r = FakeReader({"f1": [(WIDE, "麟趾呈祥", 0.9), (WIDE, "王", 0.9), (WIDE, "瑞府", 0.9)]})
    assert textscan.hits([(0.5, "f1")], [LINE], r, context=PROMPT) == []
