# -*- coding: utf-8 -*-
from tools.serve import journal


def test_append_stamps_and_tail_reads_back(tmp_path):
    out = journal.append(tmp_path, ["task 1: pending → queued", "task 1 告警: x"], prefix="host=1 ")
    assert len(out) == 2 and out[0].endswith("host=1 task 1: pending → queued")
    assert journal.tail(tmp_path) == out


def test_empty_append_writes_nothing(tmp_path):
    assert journal.append(tmp_path, []) == []
    assert not (tmp_path / journal.LOG).exists() and journal.tail(tmp_path) == []
