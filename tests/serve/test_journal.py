# -*- coding: utf-8 -*-
from tools.serve import journal


def test_append_stamps_and_tail_reads_back(tmp_path):
    out = journal.append(tmp_path, ["task 1: pending → queued", "task 1 告警: x"], prefix="host=1 ")
    assert len(out) == 2 and out[0].endswith("host=1 task 1: pending → queued")
    assert journal.tail(tmp_path) == out


def test_empty_append_writes_nothing(tmp_path):
    assert journal.append(tmp_path, []) == []
    assert not (tmp_path / journal.LOG).exists() and journal.tail(tmp_path) == []


def _log(tmp_path, *lines):
    (tmp_path / "journal").mkdir(exist_ok=True)
    (tmp_path / journal.LOG).write_text("".join(f"{x}\n" for x in lines), encoding="utf-8")


def test_read_filters_by_time_and_prefix(tmp_path):
    _log(tmp_path, "2026-10-09T01:00:00+00:00 host=1 task 3: pending → queued p1",
         "2026-10-09T02:00:00+00:00 console 工程导出 drama 1 → x.zip",
         "2026-10-09T03:00:00+00:00 host=2 task 4: queued → done",
         "2026-10-09T04:00:00+00:00 runtime export 1 开始:episode 1")
    r = journal.read(tmp_path, since="2026-10-09T01:30:00+00:00", until="2026-10-09T03:30:00+00:00")
    assert [x.split(" ", 2)[1] for x in r.lines] == ["host=2", "console"] and r.more == 0   # 新的在前
    assert [x[26:32] for x in journal.read(tmp_path, prefixes=["host="]).lines] == ["host=2", "host=1"]


def test_read_streams_the_whole_file_and_says_how_many_more(tmp_path):
    """不能只读最后若干行 —— 那样更早的时间段永远筛不到;最多显示 limit 条,多的写明还有几条。"""
    _log(tmp_path, *[f"2026-10-0{1 + i // 1000}T00:00:00+00:00 host=1 line {i}" for i in range(3000)])
    r = journal.read(tmp_path, until="2026-10-01T23:59:59+00:00", limit=5)
    assert r.lines[0].endswith("line 999") and len(r.lines) == 5 and r.more == 995


def test_mentions_match_whole_ids_only(tmp_path):
    _log(tmp_path, "2026-10-09T01:00:00+00:00 host=1 task 1: pending → queued p1",
         "2026-10-09T01:00:01+00:00 host=1 task 12: pending → queued p2",
         "2026-10-09T01:00:02+00:00 workflows 改参重跑:task 1 → 新任务 13")
    assert len(journal.read(tmp_path, mentions=("task", 1)).lines) == 2
    assert journal.read(tmp_path, mentions=("task", 12)).lines[0].endswith("p2")


def test_missing_log_reads_empty(tmp_path):
    r = journal.read(tmp_path)
    assert r.lines == [] and r.more == 0


def test_links_split_a_line_into_text_and_hrefs():
    parts = journal.links("host=1 task 12: queued → done;llm_run 3 意外失败;export 2 开始")
    assert [h for _, h in parts if h] == ["/task/12", "/llm/run/3", "/trace/export/2"]
    assert "".join(t for t, _ in parts) == "host=1 task 12: queued → done;llm_run 3 意外失败;export 2 开始"
