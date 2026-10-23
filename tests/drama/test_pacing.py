# -*- coding: utf-8 -*-
"""节奏统计(spec 2026-10-23-剧集脑爆系统-design.md 第 2a 节):只提示不拦,阈值标出处与档位。"""
from tools.drama import pacing


def _b(hook="有", reversal="无", emotion="平", paywall="否"):
    return {"hook": hook, "reversal": reversal, "emotion": emotion, "paywall": paywall, "quote": "q"}


def test_stats_cover_hooks_reversal_gaps_paywall_and_longest_press():
    beats = {1: _b(reversal="大"), 2: _b(hook="无", emotion="压"), 3: _b(emotion="压"), 4: _b(emotion="压"),
             5: _b(reversal="小", paywall="是"), 6: _b()}
    s = pacing.stats(beats)
    assert s["hook_coverage_first10"] == 5 / 6
    assert s["reversal_gaps"] == [4]                    # 第 1 集到第 5 集
    assert s["paywall_episodes"] == [5]
    assert s["longest_press"] == 3


def test_hints_compare_against_sourced_thresholds_only():
    beats = {n: _b(reversal="大" if n in (1, 8) else "无") for n in range(1, 11)}
    beats[3]["hook"] = "无"
    got = pacing.hints(beats)
    assert any("第 3 集" in h and "钩子" in h for h in got)
    assert any("反转" in h and "7 集" in h for h in got)      # 第 1 → 8 集隔了 7 集,超过 5
    assert any("付费" in h for h in got)                      # 第 9–10 集没有卡点候选
    assert all("出处" in h for h in got)


def test_end_gaps_count_from_the_start_and_to_the_last_episode():
    none = {n: _b() for n in range(1, 21)}
    assert pacing.stats(none)["leading_gap"] == 20 and pacing.stats(none)["trailing_gap"] is None
    assert any("已标注的 20 集里一次反转都没有" in h for h in pacing.hints(none))   # 只标了一部分时不能说成「全剧」
    late = {n: _b(reversal="大" if n == 15 else "无") for n in range(1, 21)}
    s = pacing.stats(late)
    assert s["reversal_gaps"] == [] and s["leading_gap"] == 15 and s["trailing_gap"] == 5
    got = pacing.hints(late)
    assert any("开头到第 15 集都没有反转" in h for h in got)
    assert not any("之后到第" in h for h in got)                 # 尾部 5 集不超过阈值
    tail = {n: _b(reversal="大" if n in (1, 5) else "无") for n in range(1, 21)}
    assert any("第 5 集之后到第 20 集都没有反转" in h for h in pacing.hints(tail))


def test_hook_coverage_is_none_without_early_episodes():
    assert pacing.stats({12: _b()})["hook_coverage_first10"] is None


def test_every_threshold_names_its_source_and_tier():
    for t in pacing.THRESHOLDS.values():
        assert t["source"] and t["tier"] in ("A", "B") and t["text"]
