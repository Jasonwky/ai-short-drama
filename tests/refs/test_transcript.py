# -*- coding: utf-8 -*-
from tools.refs.transcript import sentences


def _chars(text, t0=0.0, step=0.1):
    return [[c, round(t0 + i * step, 2), round(t0 + (i + 1) * step, 2)] for i, c in enumerate(text)]


def test_empty_input_gives_no_sentences():
    assert sentences([]) == []


def test_breaks_on_gap_longer_than_threshold():
    a = _chars("你好吗")
    b = _chars("我很好", t0=1.5)          # 0.3 -> 1.5,间隔 1.2 秒
    s = sentences(a + b)
    assert [x["text"] for x in s] == ["你好吗", "我很好"]
    assert s[1]["start"] == 1.5 and s[0]["end"] == 0.3


def test_gap_equal_to_threshold_does_not_break():
    a = _chars("你好")
    b = _chars("吗", t0=0.8)             # 0.2 -> 0.8,间隔刚好 0.6
    assert len(sentences(a + b)) == 1


def test_breaks_after_sentence_end_marks_even_without_gap():
    s = sentences(_chars("走吧。别回头!真的?算了…好"))
    assert [x["text"] for x in s] == ["走吧。", "别回头!", "真的?", "算了…", "好"]


def test_pause_absorbed_into_a_long_char_starts_a_new_sentence():
    """真机:whisper 逐字时间首尾相接,停顿被算进停顿后那个字(「幸」0.9→7.0 秒)。"""
    chars = [["我", 0.0, 0.3], ["来", 0.3, 0.6], ["幸", 0.9, 7.0], ["好", 7.0, 7.2]]
    got = sentences(chars)
    assert [s["text"] for s in got] == ["我来", "幸好"]
    assert got[1]["start"] == 6.6                     # 从重新开口前一点算起,不是停顿开头


def test_very_long_runs_are_split():
    chars = [[str(i % 10), i * 0.1, i * 0.1 + 0.1] for i in range(70)]
    assert [len(s["text"]) for s in sentences(chars)] == [30, 30, 10]


def test_two_long_chars_in_a_row_stay_together():
    """whisper 常让相邻两字共用一段时长(真机「我」「是」各 1.28 秒):不拆成单字句。"""
    chars = [["你", 0.0, 0.1], ["好", 0.1, 0.2], ["我", 0.2, 1.48], ["是", 0.2, 1.48], ["谁", 1.48, 1.6]]
    assert [s["text"] for s in sentences(chars)] == ["你好", "我是谁"]
