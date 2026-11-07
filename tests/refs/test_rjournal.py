from tools.refs import rjournal


def test_append_and_read_newest_first_with_filters(tmp_path):
    rjournal.append(tmp_path, "fetch", source="jjwxc", url="u1", status=200)
    rjournal.append(tmp_path, "fetch", source="k17", url="u2", status=200, work_id=3)
    rjournal.append(tmp_path, "setting", source="jjwxc", field="mode", old="C", new="B")
    assert [e["event"] for e in rjournal.read(tmp_path)] == ["setting", "fetch", "fetch"]
    assert [e["url"] for e in rjournal.read(tmp_path, source="jjwxc", event="fetch")] == ["u1"]
    assert [e["url"] for e in rjournal.read(tmp_path, work_id=3)] == ["u2"]
    assert len(list((tmp_path / "journal" / "refs").glob("*.jsonl"))) == 1


def test_read_empty(tmp_path):
    assert rjournal.read(tmp_path) == []


def test_half_written_last_line_of_newest_file_is_skipped(tmp_path):
    import json
    rjournal.append(tmp_path, "fetch", source="jjwxc", url="u1")
    d = tmp_path / "journal" / "refs"
    (d / "2000-01-01.jsonl").write_text(json.dumps({"event": "old"}) + "\n", encoding="utf-8")
    newest = sorted(d.glob("*.jsonl"))[-1]
    with newest.open("a", encoding="utf-8") as f:
        f.write('{"at": "x", "event": "fet')                 # 后台正在写一半
    assert [e["event"] for e in rjournal.read(tmp_path)] == ["fetch", "old"]
    (d / "2000-01-01.jsonl").write_text('{"event": "old"\n', encoding="utf-8")   # 别处的坏行:跳过,带一条 corrupt 记录,不抛
    got = rjournal.read(tmp_path)
    assert [e["event"] for e in got] == ["fetch", "corrupt"]
    assert got[1] == {"event": "corrupt", "file": "2000-01-01.jsonl", "line": 1}


def test_any_bad_line_is_skipped_and_reported_even_under_filters(tmp_path):
    import json
    d = tmp_path / "journal" / "refs"
    d.mkdir(parents=True)
    good = json.dumps({"at": "t", "event": "fetch", "source": "k17", "work_id": 3}).encode()
    (d / "2000-01-01.jsonl").write_bytes(b"\xff\xfe not utf-8\n[1, 2]\n" + good + b"\n{bad\n" + good + b"\n")
    got = rjournal.read(tmp_path, source="jjwxc", work_id=9)       # 筛选不掉坏行:人要看得见日志坏了
    assert got == [{"event": "corrupt", "file": "2000-01-01.jsonl", "line": n} for n in (4, 2, 1)]
    assert [e["event"] for e in rjournal.read(tmp_path, source="k17")] == ["fetch", "corrupt", "fetch", "corrupt", "corrupt"]
    assert [e["event"] for e in rjournal.read(tmp_path, event="fetch")] == ["fetch", "fetch"]
