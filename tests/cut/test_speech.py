# -*- coding: utf-8 -*-
"""拼音比对。例句来自云机实测(spike-results/2026-10-29-台词与口型匹配方案调研.md 第 4.2 节)。"""
from tools.cut.speech import hit, syllables


def test_syllables_drop_punctuation_and_tones():
    assert syllables("好,我去。") == ["hao", "wo", "qu"]


def test_homophones_and_traditional_count_as_heard():
    # Whisper 把「伙夫」听成「霍夫」、输出繁体 —— 念的是对的
    assert hit("三日后营中比武!伙夫,你来当靶子!", "三日后营中比武霍夫 你来当靶子") == 1.0
    assert hit("新来的,记住,这营里我说了算!", "新來的記住這營裡我說了算") == 1.0
    assert hit("老栓叔,我就是碰巧。", "老双叔,我就是碰巧。") > 0.85


def test_wrong_words_lower_the_hit():
    assert hit("老栓叔,我就是碰巧。", "我不知道") < 0.5
    assert hit("好,我去。", "") == 0.0
