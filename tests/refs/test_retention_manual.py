# -*- coding: utf-8 -*-
import pytest

from tools.refs import manual, retention, settings, works

BODY = "青州叶家少年看着眼前主事平静说道" * 30


@pytest.fixture
def r(refs, tmp_path):
    settings.ensure(refs)
    settings.update(refs, tmp_path, "manual", {"retention_days": "10"})
    return refs, tmp_path


def _rows(refs):
    return [dict(x) for x in refs.conn.execute("SELECT * FROM ref_content ORDER BY id")]


def test_manual_text_needs_an_origin(r):
    refs, root = r
    with pytest.raises(ValueError, match="来源说明"):
        manual.add_text(refs, root, title="梗概", text=BODY, origin=" ")


def test_manual_text_is_judged_and_fingerprinted(r):
    refs, root = r
    cid = manual.add_text(refs, root, title="我整理的梗概", text=BODY, origin="自己看完第 1–5 集手记")
    row = _rows(refs)[0]
    assert row["id"] == cid and row["verdict"] == "ok" and row["kind"] == "manual"
    assert works.get(refs, row["work_id"])["source"] == "manual"


def test_not_purged_before_double_retention_in_1a(r):
    refs, root = r
    manual.add_text(refs, root, title="t", text=BODY, origin="o")
    row = _rows(refs)[0]
    assert retention.purge(refs, root, now=row["due_at"]) == ([], [])          # 到 due_at 了,但还没拆解 → 顺延
    done, errors = retention.purge(refs, root, now=row["hard_due_at"])
    assert done == [row["id"]] and errors == []
    assert not (root / row["path"]).exists() and _rows(refs)[0]["purged_at"]
    assert refs.conn.execute("SELECT COUNT(*) FROM ref_fingerprint").fetchone()[0] > 0     # 指纹留着


def test_blocked_work_is_purged_now(r):
    refs, root = r
    manual.add_text(refs, root, title="t", text=BODY, origin="o")
    row = _rows(refs)[0]
    works.set_flag(refs, row["work_id"], blocked=True)
    assert retention.purge(refs, root, now=row["fetched_at"])[0] == [row["id"]]


def test_missing_file_is_reported_not_swallowed(r):
    refs, root = r
    manual.add_text(refs, root, title="t", text=BODY, origin="o")
    row = _rows(refs)[0]
    (root / row["path"]).unlink()
    done, errors = retention.purge(refs, root, now=row["hard_due_at"])
    assert done == [] and "找不到" in errors[0]


def test_row_purged_by_another_thread_is_not_an_error(r, monkeypatch):
    from pathlib import Path
    refs, root = r
    manual.add_text(refs, root, title="t", text=BODY, origin="o")
    row = _rows(refs)[0]
    real = Path.unlink
    def raced(self, *a, **k):                        # 另一个线程抢先删了文件、置了 purged_at
        real(self)
        with refs.write() as c:
            c.execute("UPDATE ref_content SET purged_at = 'x' WHERE id = ?", (row["id"],))
        raise FileNotFoundError(str(self))
    monkeypatch.setattr(Path, "unlink", raced)
    assert retention.purge(refs, root, now=row["hard_due_at"]) == ([], [])


def test_manual_text_for_a_missing_work_writes_nothing(r):
    refs, root = r
    with pytest.raises(KeyError):
        manual.add_text(refs, root, title="t", text=BODY, origin="o", work_id=999)
    assert not (root / "content").exists() and _rows(refs) == []
