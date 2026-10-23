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


def test_every_threshold_names_its_source_and_tier():
    for t in pacing.THRESHOLDS.values():
        assert t["source"] and t["tier"] in ("A", "B") and t["text"]
