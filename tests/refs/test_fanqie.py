# -*- coding: utf-8 -*-
from pathlib import Path

import pytest

from tools.refs.sources import fanqie
from tools.refs.sources.base import AdapterError

FX = Path(__file__).parent / "fixtures"


def test_rank_ids_and_word_counts():
    items = fanqie.parse_rank((FX / "fanqie_rank.html").read_text(encoding="utf-8"))
    assert len(items) >= 10 and all(i.external_id.isdigit() for i in items)
    assert all(int(i.raw["wordNumber"]) > 0 for i in items)
    assert "abstract" not in items[0].raw            # 简介被字体加密,不收
    assert sum(i.raw["title_obfuscated"] for i in items) == 9     # 2026-11-07 快照:10 本里 9 本书名带私用区字符


def test_obfuscation_flag():
    assert fanqie.obfuscated("惹枝") and not fanqie.obfuscated("惹金枝")



@pytest.mark.parametrize("state", ['{"rank":{"book_list":[{"bookName":"a"}]}}', '{"other":1}', "[1]", "{bad"])
def test_state_with_missing_fields_is_an_adapter_error(state):
    with pytest.raises(AdapterError):
        fanqie.parse_rank(f"<script>window.__INITIAL_STATE__={state};</script>")
