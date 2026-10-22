# -*- coding: utf-8 -*-
"""字幕纯函数。能量曲线是真 H3 片的 0.1 s 窗 RMS(云机实测那几条片,本机从音轨算出);
逐字时间:单句片按 `spike-results/2026-09-26-字幕对齐云机实测.md` §2.2 原样,
两句片的句首 / 句尾按同文档附 2 的实测值,中间各字时刻是插值(只影响「第二个命中字」落在哪,不影响判定)。"""
from tools.cut import subtitles as st
from tools.cut.subtitles import Cue

# 动漫单句片 h3_00002_:前 5 秒底噪,5.1 s 开口,7.9 s 收声
ANIME_ENV = [1, 1, 1, 1, 1, 1069, 1089, 850, 706, 574, 768, 728, 639, 553, 520, 1239, 1177, 988, 810, 678, 1636, 1362,
             1037, 831, 778, 1856, 961, 674, 604, 600, 1433, 1052, 742, 595, 644, 1309, 959, 733, 612, 552, 1027, 787,
             541, 438, 1033, 1159, 892, 719, 615, 574, 555, 4471, 11817, 3547, 13116, 13979, 12108, 2535, 11235, 10568,
             2326, 357, 356, 340, 635, 5753, 10072, 15422, 17166, 15973, 7025, 10859, 17810, 11846, 10394, 12729, 11537,
             4929, 2594, 156]
ANIME_CHARS = [("这", 0.0, 0.0), ("块", 0.0, 5.5), ("玉", 5.5, 5.9), ("佩", 5.5, 5.9), ("是", 5.9, 6.7), ("我", 5.9, 6.7),
               ("母", 6.7, 7.0), ("亲", 7.0, 7.2), ("留", 7.2, 7.4), ("给", 7.4, 7.5), ("我", 7.5, 7.8), ("的", 7.5, 7.8)]

# 两句片 two_lines_fourview_00001_:1.5–3.86 s 第 1 句,4.89–8.0 s 第 2 句
TWO_ENV = [2, 4, 4, 4, 28, 30, 92, 115, 77, 25, 7, 5, 4, 4, 828, 10689, 3311, 12323, 13996, 7364, 4408, 12698, 6216, 632,
           5, 1365, 5861, 14202, 14189, 10958, 3498, 9704, 13796, 9338, 9960, 10047, 5793, 1749, 1008, 6, 5, 3, 3, 4, 5,
           5, 6, 5, 833, 6859, 9859, 3315, 9816, 13319, 14728, 6034, 4892, 8082, 4438, 6434, 11362, 8640, 4480, 443, 23,
           39, 23, 254, 5830, 4631, 6170, 6073, 8853, 6344, 10312, 7275, 2433, 1975, 1319, 9]
TWO_LINES = ["这块玉佩，是我母亲留给我的。", "可她从来没告诉过我，它是从哪里来的。"]
TWO_CHARS = ([("这", 0.0, 0.0), ("块", 0.0, 1.9), ("玉", 1.9, 2.3), ("佩", 1.9, 2.3), ("是", 2.3, 2.6), ("我", 2.6, 2.8),
              ("母", 2.8, 3.0), ("亲", 3.0, 3.1), ("留", 3.1, 3.3), ("给", 3.3, 3.4), ("我", 3.4, 3.6), ("的", 3.6, 3.8)]
             + [("可", 3.8, 5.0), ("她", 5.0, 5.2), ("从", 5.2, 5.4), ("来", 5.4, 5.6), ("没", 5.6, 5.8), ("告", 5.8, 6.0),
                ("诉", 6.0, 6.1), ("过", 6.1, 6.2), ("我", 6.2, 6.8), ("她", 6.8, 7.0), ("是", 7.0, 7.1), ("从", 7.1, 7.3),
                ("哪", 7.3, 7.4), ("里", 7.4, 7.6), ("来", 7.6, 7.7), ("的", 7.7, 7.84)])


def test_single_line_start_snaps_to_voice_onset():
    """whisper 把「这块」标成 0.0–5.5 s(拉进了前 5 秒的静音);句首吸附到能量起点 5.1 s,句尾用 whisper 的 7.8 s。"""
    cues, problems = st.build(["这块玉佩，是我母亲留给我的。"], ANIME_CHARS, ANIME_ENV, 8.0)
    assert problems == []
    assert cues == [Cue("这块玉佩，是我母亲留给我的。", 5.1, 8.0)]     # 句尾多留 0.3 s,但不超片长


def test_two_lines_in_one_clip():
    """一片两句:第 2 句首字被 whisper 标成 3.8 s(接着第 1 句句尾),实际 4.89 s 才开口 —— 吸附到 4.9 s。
    识别错字「它是 → 她是」不影响:15/16 字命中。"""
    cues, problems = st.build(TWO_LINES, TWO_CHARS, TWO_ENV, 8.0)
    assert problems == []
    assert cues == [Cue(TWO_LINES[0], 1.5, 4.1), Cue(TWO_LINES[1], 4.9, 8.0)]


def test_align_counts_hits_and_does_not_reuse_chars():
    a = st.align(TWO_LINES, TWO_CHARS)
    assert [(x["hit"], x["total"]) for x in a] == [(12, 12), (15, 16)]
    assert a[1]["first"] == 12                  # 第 2 句从第 1 句用剩的字里找,不会回头匹配第 1 句的「我」「是」


# 2026-09-29 真链路(第 4 层,锚定版 H3 片):人物先把提示词里的「林小雨用普通话说」念了出来(3.7–4.8 s),停顿后才说台词(5.6 s 起)
STAGE_ENV = [1, 1, 295, 334, 300, 856, 893, 678, 521, 541, 592, 530, 497, 614, 655, 562, 484, 601, 1188, 961, 903, 850,
             727, 643, 576, 1289, 716, 488, 403, 876, 681, 469, 340, 635, 516, 370, 288, 7889, 16174, 15891, 9987, 5701,
             11217, 6047, 9935, 3657, 10202, 9654, 1844, 645, 528, 457, 371, 345, 386, 286, 3330, 8845, 10400, 11386,
             10758, 1882, 11168, 5798, 290, 207, 204, 2595, 9793, 13809, 17426, 5156, 5529, 14310, 10777, 8616, 9745,
             3282, 1142, 394]
STAGE_CHARS = [("林", 0.0, 0.0), ("用", 0.0, 4.0), ("普", 4.0, 4.18), ("通", 4.18, 4.3), ("话", 4.3, 4.46),
               ("说", 4.46, 4.76), ("这", 4.76, 5.74), ("块", 5.74, 5.9), ("玉", 5.9, 6.08), ("佩", 6.08, 6.3),
               ("是", 6.3, 6.92), ("我", 6.3, 6.92), ("母", 6.92, 7.16), ("亲", 7.16, 7.32), ("留", 7.32, 7.46),
               ("给", 7.46, 7.58), ("我", 7.58, 7.86), ("的", 7.58, 7.86)]


def test_speech_that_is_not_the_line_is_not_swallowed_into_its_subtitle():
    """句首只在「本句第一个命中字之前那个识别字」之后找:前面多念出来的话不算这句台词的开口。"""
    cues, problems = st.build(["这块玉佩，是我母亲留给我的。"], STAGE_CHARS, STAGE_ENV, 8.0)
    assert problems == []
    assert cues == [Cue("这块玉佩，是我母亲留给我的。", 5.6, 8.0)]
    # 台词首字听错(「这」→「哲」)时,它前面那个识别字仍是界线,不会把听错的首字当成「多念的话」
    misheard = [("哲", s, e) if c == "这" else (c, s, e) for c, s, e in STAGE_CHARS]
    cues, _ = st.build(["这块玉佩，是我母亲留给我的。"], misheard, STAGE_ENV, 8.0)
    assert cues[0].start == 5.6


def test_low_hit_rate_is_a_problem_not_a_guess():
    cues, problems = st.build(["今天天气真不错。"], ANIME_CHARS, ANIME_ENV, 8.0)
    assert cues == []
    assert "只对上" in problems[0] and "需人工校" in problems[0]


def test_end_beyond_clip_is_a_problem():
    late = [(c, s, e + 1.0) for c, s, e in ANIME_CHARS]
    cues, problems = st.build(["这块玉佩，是我母亲留给我的。"], late, ANIME_ENV, 8.0)
    assert cues == [] and "超出片长" in problems[0]
    # 实战第 6 段:人物先做动作、4.3 s 才开口,台词被片尾截断 —— 判卷要说出该怎么办,不只报数字
    assert "加长" in problems[0]


def test_onset_needs_a_rising_edge():
    """上一句的尾音还没落下去时,不能把 lo 本身当成下一句的开口。"""
    env = [0, 0, 9, 9, 9, 9, 0, 0, 9, 9]
    assert st.onset(env, 0.4, 0.9) == 0.8
    assert st.onset(env, 0.0, 0.9) == 0.2
    assert st.onset([0] * 10, 0.0, 0.9) is None


def test_silent_envelope_falls_back_to_whisper_start():
    cues, problems = st.build(["这块玉佩，是我母亲留给我的。"], ANIME_CHARS, [0.0] * 80, 8.0)
    assert problems == []
    assert cues[0].start == 0.0                 # 没有能量信息就只能信 whisper(界面上照样可以人工校)


def test_padding_keeps_min_show_and_never_overlaps():
    cues = st._pad([Cue("甲", 1.0, 1.2), Cue("乙", 1.6, 3.0), Cue("丙", 5.0, 7.9)], 8.0)
    assert cues == [Cue("甲", 1.0, 1.6), Cue("乙", 1.6, 3.3), Cue("丙", 5.0, 8.0)]


def test_srt_round_trip_and_episode_offsets():
    a = [Cue("第一句", 1.5, 4.1), Cue("第二句", 4.9, 8.0)]
    b = [Cue("第三句", 0.25, 2.0)]
    text = st.srt(a)
    assert text.startswith("1\n00:00:01,500 --> 00:00:04,100\n第一句\n\n2\n00:00:04,900 --> 00:00:08,000\n")
    assert st.parse_srt(text) == a
    ep = st.parse_srt(st.episode_srt([(a, 0.0), (b, 8.0)]))
    assert ep[2] == Cue("第三句", 8.25, 10.0)
    assert st.episode_srt([(a, 0.0), (b, 8.0)]).count("\n3\n") == 1   # 编号连续


def test_line_width_fits_the_frame():
    # 实测(tmp/subwidth/probe.py):竖屏 768x1344 每字约 35 px,25 字就贴边被裁;横屏每字约 20 px
    assert st.max_chars(768, 1344) == 16
    assert st.max_chars(1344, 768) == 16      # 横屏放得下更多,但一行再长就读不完


def test_long_line_is_split_at_punctuation_and_time_shared_by_length():
    line = "王妃？不过是个冲喜的祭品。王爷都快咽气了，你还想翻身？"      # 实战第 2 段,26 字一行冲出画面
    out = st.fit_screen([Cue(line, 1.2, 6.7)], 16)
    assert [c.text for c in out] == ["王妃？不过是个冲喜的祭品。", "王爷都快咽气了，你还想翻身？"]
    assert out[0].start == 1.2 and out[-1].end == 6.7
    assert out[0].end == out[1].start                     # 首尾相接,不留空档
    assert 3.0 < out[0].end < 4.0                         # 按念的字数分:11 字 / 12 字


def test_clause_longer_than_the_line_is_cut_evenly():
    out = st.fit_screen([Cue("甲" * 20, 0.0, 4.0)], 16)
    assert [len(c.text) for c in out] == [10, 10]
    assert all(len(c.text) <= 16 for c in out)


def test_short_lines_are_untouched():
    cues = [Cue("都闭嘴。", 0.5, 1.5), Cue("王爷不是病，是中毒。", 1.6, 3.0)]
    assert st.fit_screen(cues, 16) == cues


# 2026-10-22 实战第 7 段(task 128):「杖毙」听成同音的「障蔽」、「李福」听成「礼服」—— 人念对了,识别选错字
HOMO_LINES = ["王、王爷饶命！都是李福的主意！", "拖出去，杖毙。"]
HOMO_CHARS = [("王", 0.0, 0.0), ("王", 0.06, 1.76), ("爷", 1.76, 1.94), ("饶", 1.94, 2.16), ("命", 2.16, 2.42),
              ("都", 2.42, 3.2), ("是", 2.42, 3.2), ("礼", 3.2, 3.44), ("服", 3.44, 3.6), ("的", 3.6, 3.76),
              ("主", 3.76, 3.96), ("意", 3.96, 4.22), ("拖", 4.22, 4.86), ("出", 4.86, 5.22), ("去", 4.86, 5.22),
              ("障", 5.52, 6.04), ("蔽", 6.04, 6.32)]
HOMO_ENV = [6, 195, 185, 174, 377, 26, 1, 930, 7676, 2295, 251, 312, 164, 104, 13, 330, 11385, 9448, 5137, 9402, 11954,
            9564, 3696, 5599, 955, 227, 862, 187, 122, 4679, 17769, 1954, 10193, 10639, 6765, 1451, 6742, 8988, 3218,
            7424, 7307, 14036, 1527, 63, 72, 127, 275, 3894, 12981, 4728, 2944, 1546, 250, 13, 1, 1, 1, 2, 5957, 10632,
            649, 1134, 10329, 1039, 9]


def test_same_length_misheard_chars_count_for_a_short_line():
    """等长替换(≤ 2 字一段)算识别错字:5 字的「拖出去,杖毙」严格只对上 3 个,以前判卷不过。"""
    cues, problems = st.build(HOMO_LINES, HOMO_CHARS, HOMO_ENV, 6.584)
    assert problems == []
    assert [c.text for c in cues] == HOMO_LINES and cues[1].end >= 6.32      # 句尾落在「蔽」的结束时刻上
    a = st.align(HOMO_LINES, HOMO_CHARS)
    assert (a[1]["hit"], a[1]["subst"], a[1]["total"]) == (3, 2, 5)


def test_substitutions_alone_do_not_pass_a_line():
    """严格对上的字不到一半,不靠替换凑数:念的是别的话照样拦。"""
    chars = [("拖", 0.0, 0.5), ("下", 0.5, 1.0), ("去", 1.0, 1.4), ("放", 1.4, 1.8), ("了", 1.8, 2.2)]
    _, problems = st.build(["拉出去，杖毙。"], chars, [0] + [5000] * 30, 3.0)
    assert problems and "只对上" in problems[0]
