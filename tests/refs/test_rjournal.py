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
    import pytest
    rjournal.append(tmp_path, "fetch", source="jjwxc", url="u1")
    d = tmp_path / "journal" / "refs"
    (d / "2000-01-01.jsonl").write_text(json.dumps({"event": "old"}) + "\n", encoding="utf-8")
    newest = sorted(d.glob("*.jsonl"))[-1]
    with newest.open("a", encoding="utf-8") as f:
        f.write('{"at": "x", "event": "fet')                 # 后台正在写一半
    assert [e["event"] for e in rjournal.read(tmp_path)] == ["fetch", "old"]
    (d / "2000-01-01.jsonl").write_text('{"event": "old"\n', encoding="utf-8")   # 别处的坏行照样抛
    with pytest.raises(json.JSONDecodeError):
        rjournal.read(tmp_path)
