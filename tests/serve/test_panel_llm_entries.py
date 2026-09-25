# -*- coding: utf-8 -*-
from tools.drama import repo


def test_panels_offer_the_llm_entry_points(client, store, sample):
    assert "data-llm-voice" in client.get(f"/drama/{sample['drama']}/assets").text
    ep = repo.add_episode(store, sample["drama"], 2, "script", script="林小雨说：「你来了。」")
    assert "data-llm-synopsis" in client.get(f"/drama/{sample['drama']}/script").text
    assert "data-llm-storyboard" in client.get(f"/episode/{ep}/storyboard").text


def test_novel_episode_offers_adaptation(client, store, sample):
    repo.add_episode(store, sample["drama"], 2, "novel", outline="大纲", source_text="原文")
    assert "data-llm-adapt" in client.get(f"/drama/{sample['drama']}/script").text


def test_segmented_episode_offers_rewrite_with_a_warning(client, sample):
    page = client.get(f"/drama/{sample['drama']}/script").text
    assert "已经拆了 1 个段落" in page and "data-llm-synopsis" in page    # 有段落也能重写,但写明代价


def test_panels_list_their_llm_runs(client, runtime, store, sample):
    repo.update(store, "character", sample["lin"], appearance="二十出头,长发")
    rid, _ = runtime.start_llm("voice", sample["lin"], {})
    runtime.wait_llm()
    assert f'data-llm-run="{rid}"' in client.get(f"/drama/{sample['drama']}/assets").text
