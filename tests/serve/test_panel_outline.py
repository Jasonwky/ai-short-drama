# -*- coding: utf-8 -*-
"""大纲面板的脑爆入口:标注节拍、节拍条与节奏提示、单集变体(spec 2026-10-23-剧集脑爆系统-design.md 第 2 节)。"""
from tools.drama import repo


def _beat(**kw):
    return {"hook": "有", "reversal": "无", "emotion": "压", "paywall": "否", "quote": "x", **kw}


def test_outline_page_has_the_annotate_beats_button(client, sample):
    page = client.get(f"/drama/{sample['drama']}/outline").text
    assert "data-llm-beats" in page and f"/llm/start/beats/{sample['drama']}" in page
    assert "data-beat=" not in page                                    # 还没标过


def test_beats_bar_and_pacing_hints_show_when_beats_exist(client, store, sample):
    import json
    repo.update(store, "episode", sample["episode"], outline="第一集大纲", beats_json=json.dumps(_beat(hook="无"), ensure_ascii=False))
    page = client.get(f"/drama/{sample['drama']}/outline").text
    assert page.count("data-beat=") == 1 and 'data-beat="1"' in page
    assert "data-pacing-hint" in page and "只提示,不拦" in page


def test_variants_form_only_for_episodes_with_outline_and_no_script(client, store, sample):
    own = sample["drama"]
    repo.update(store, "episode", sample["episode"], outline="有大纲有剧本")          # 样例集有剧本
    free = repo.add_episode(store, own, 2, "novel", outline="只有大纲")
    page = client.get(f"/drama/{own}/outline").text
    assert page.count("data-llm-variants") == 1 and f"/llm/start/outline_variants/{free}" in page
    assert f"/llm/start/outline_variants/{sample['episode']}" not in page
    assert 'name="n"' in page and "这集已有剧本,改大纲要先清剧本" in page
