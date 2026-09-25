# -*- coding: utf-8 -*-
"""大纲面板:原文先进分批页,每批带自己的原文与建议集数;已有集时写明接在哪一集之后。"""
from tools.drama import repo
from tools.llm import outline


def test_long_novel_is_split_into_batches_with_suggested_counts(client, store, monkeypatch):
    monkeypatch.setattr(outline, "MAX_NOVEL_CHARS", 30)
    d = repo.create_drama(store, "长篇", "9:16", "realistic")
    novel = "\n".join(["一" * 20, "二" * 20, "三" * 10])
    page = client.post(f"/drama/{d}/outline/batches", data={"novel": novel, "n_episodes": "5"}).text
    assert page.count("data-batch-start") == 3 and "共 3 批" in page
    assert ">" + "一" * 20 + "</textarea>" in page                            # 每批表单只带自己那段原文
    assert 'name="n_episodes" value="2"' in page and 'name="n_episodes" value="1"' in page
    assert "约第 1–2 集" in page and 'target="_blank"' in page


def test_existing_episodes_continue_numbering(client, sample):
    page = client.get(f"/drama/{sample['drama']}/outline").text
    assert "接在第 1 集之后" in page and "data-outline-form" in page
    novel = "\n".join("段落" * 60 for _ in range(3))
    page = client.post(f"/drama/{sample['drama']}/outline/batches", data={"novel": novel, "n_episodes": "2"}).text
    assert "约第 2–3 集" in page and "已有 1 集" in page


def test_too_few_episodes_for_the_batches_is_a_readable_400(client, store, monkeypatch):
    monkeypatch.setattr(outline, "MAX_NOVEL_CHARS", 30)
    d = repo.create_drama(store, "长篇", "9:16", "realistic")
    r = client.post(f"/drama/{d}/outline/batches", data={"novel": "\n".join(["一" * 20] * 3), "n_episodes": "2"})
    assert r.status_code == 400 and "至少要 3 集" in r.text
