# -*- coding: utf-8 -*-
import pytest
from fastapi.testclient import TestClient

from tools.refs import heat, jobs, loginstate, rjournal, settings, works
from tools.refs.db import RefStore
from tools.serve.app import create_app
from tools.serve.fakes import fake_backend
from tools.serve.runtime import Runtime

BODY = "青州叶家少年看着眼前主事平静说道" * 30


@pytest.fixture
def rc(store, tmp_path):
    refs = RefStore(":memory:")
    settings.ensure(refs)
    rt = Runtime(store, tmp_path, fake_backend()[0], refs=refs, refs_root=tmp_path / "refs")
    return TestClient(create_app(rt)), refs, tmp_path / "refs"


def test_sources_page_marks_missing_star_fields(rc):
    c, _, _ = rc
    t = c.get("/refs/sources").text
    assert "data-ref-source" in t and "★ 必填" in t and "红果短剧" in t


def test_setting_mode_a_without_caps_is_a_400_with_reason(rc):
    c, _, _ = rc
    r = c.post("/refs/source/hongguo", data={"mode": "A", "retention_days": "30"})
    assert r.status_code == 400 and "每天自动抓几部" in r.text


def test_settings_saved_and_logged(rc):
    c, refs, root = rc
    c.post("/refs/source/k17", data={"retention_days": "30", "chapters_n": "10"})
    assert settings.get(refs, "k17")["chapters_n"] == 10
    log = [tuple(x) for x in refs.conn.execute("SELECT field, old, new FROM ref_setting_log WHERE source = 'k17' ORDER BY id")]
    assert log == [("retention_days", None, "30"), ("chapters_n", None, "10")]


def test_ranks_page_lists_today_and_search_says_its_scope(rc):
    c, refs, _ = rc
    from tools.refs import clock
    wid = works.upsert(refs, "jjwxc", "1", title="逃离玫瑰岛")
    heat.record(refs, wid, "topten7", clock.today(), 1, 4.5e10, {})
    t = c.get("/refs/ranks?source=jjwxc&board=topten7").text
    assert "data-ref-rank-row" in t and "逃离玫瑰岛" in t
    assert "只覆盖已见过的作品" in c.get("/refs/ranks?q=玫瑰").text


def test_add_by_link_queues_meta_and_opens_detail(rc):
    c, refs, _ = rc
    r = c.post("/refs/add", data={"url": "http://www.17k.com/book/3583700.html"}, follow_redirects=False)
    assert r.status_code == 303 and "/refs/work?id=" in r.headers["location"]
    assert jobs.recent(refs)[0]["kind"] == "meta"


def test_manual_text_shows_on_work_page(rc):
    c, refs, root = rc
    settings.update(refs, root, "manual", {"retention_days": "10"})
    wid = works.upsert(refs, "manual", "manual-1", title="我的梗概")
    c.post(f"/refs/work/{wid}/manual", data={"title": "梗概", "text": BODY, "origin": "自己看完整理"})
    t = c.get(f"/refs/work?id={wid}").text
    assert "data-ref-content" in t and "自己看完整理" in t


def test_block_purges_immediately(rc):
    c, refs, root = rc
    settings.update(refs, root, "manual", {"retention_days": "10"})
    wid = works.upsert(refs, "manual", "manual-1", title="x")
    c.post(f"/refs/work/{wid}/manual", data={"title": "梗概", "text": BODY, "origin": "o"})
    c.post(f"/refs/work/{wid}/block")
    assert refs.conn.execute("SELECT purged_at FROM ref_content").fetchone()[0]


def test_panels_say_not_configured_without_refs(client):
    for url in ("/refs/sources", "/refs/ranks", "/refs/work"):
        r = client.get(url)
        assert r.status_code == 200 and "未配置参考库" in r.text


def test_work_page_draws_curve_with_gap(rc):
    c, refs, root = rc
    wid = works.upsert(refs, "jjwxc", "9", title="有缺口")
    for day, v in (("2026-11-01", 10.0), ("2026-11-02", 20.0), ("2026-11-04", 15.0), ("2026-11-05", 30.0)):
        heat.record(refs, wid, "topten7", day, 3, v, {})
    t = c.get(f"/refs/work?id={wid}").text
    assert t.count("<polyline") == 2 and "缺 1 天" in t


def test_pull_ranks_queues_every_board_and_logs_on_sources_page(rc):
    c, refs, root = rc
    from tools.refs.sources import ADAPTERS
    assert c.post("/refs/source/jjwxc/ranks", follow_redirects=False).status_code == 303
    assert {jobs.params(j)["board"] for j in jobs.recent(refs) if j["kind"] == "ranks"} == set(ADAPTERS["jjwxc"].boards)
    settings.pause(refs, root, "jjwxc", "robots 禁止")
    assert "robots 禁止" in c.post("/refs/source/jjwxc/ranks").text
    assert "robots 禁止" in c.get("/refs/sources").text


def test_ranks_page_only_shows_the_chosen_source(rc):
    c, refs, _ = rc
    from tools.refs import clock
    a = works.upsert(refs, "jjwxc", "1", title="晋江的书")
    b = works.upsert(refs, "k17", "2", title="十七的书")
    heat.record(refs, a, "same", clock.today(), 1, 1.0, {})
    heat.record(refs, b, "same", clock.today(), 1, 1.0, {})
    t = c.get("/refs/ranks?source=jjwxc&board=same").text
    assert "晋江的书" in t and "十七的书" not in t


def test_meta_only_link_queues_nothing(rc):
    c, refs, _ = rc
    r = c.post("/refs/add", data={"url": "https://fanqienovel.com/page/7143038691944959011"}, follow_redirects=False)
    assert r.status_code == 303 and jobs.recent(refs) == []


def test_no_fetch_for_manual_or_meta_only_sources(rc):
    c, refs, root = rc
    for src in ("manual", "fanqie"):
        wid = works.upsert(refs, src, "x1", title="只有元数据")
        assert ">抓正文</button>" not in c.get(f"/refs/work?id={wid}").text
        r = c.post(f"/refs/work/{wid}/fetch")
        assert r.status_code == 400 and "不抓正文" in r.text
    assert jobs.recent(refs) == []


def test_pull_ranks_for_manual_is_a_400_with_reason(rc):
    c, refs, _ = rc
    assert "data-ref-pull-ranks" not in c.get("/refs/sources").text.split('data-ref-source="manual"')[1]
    r = c.post("/refs/source/manual/ranks")
    assert r.status_code == 400 and "没有榜单" in r.text and "'manual'" not in r.text


def test_manual_text_for_a_missing_work_is_404_without_orphans(rc):
    c, refs, root = rc
    settings.update(refs, root, "manual", {"retention_days": "10"})
    r = c.post("/refs/work/999/manual", data={"title": "t", "text": BODY, "origin": "o"})
    assert r.status_code == 404 and not (root / "content").exists()


def test_unknown_source_is_404_everywhere(rc):
    c, refs, root = rc
    for url in ("/refs/source/nope", "/refs/source/nope/reset", "/refs/source/nope/ranks"):
        assert c.post(url, data={"mode": "B"}).status_code == 404, url
    assert not any(e.get("source") == "nope" for e in rjournal.read(root))


def test_ranks_page_shows_only_todays_candidate_reason(rc):
    c, refs, _ = rc
    from tools.refs import clock
    old = works.upsert(refs, "jjwxc", "1", title="昨天的候选")
    new = works.upsert(refs, "jjwxc", "2", title="今天的候选")
    works.set_candidate(refs, old, "昨天第 1 名(前 2 名)", "2000-01-01")
    works.set_candidate(refs, new, "今天第 2 名(前 2 名)", clock.today())
    for w, rank in ((old, 1), (new, 2)):
        heat.record(refs, w, "topten7", clock.today(), rank, 1.0, {})
    t = c.get("/refs/ranks?source=jjwxc&board=topten7").text
    assert "今天第 2 名" in t and "昨天第 1 名" not in t


def _content(refs, wid, kind, unit, path, *, origin="o", verdict="ok", reason="", meta=None, purged=""):
    import json
    with refs.write() as c:
        return c.execute("INSERT INTO ref_content (work_id, kind, unit, title, path, sha256, origin, verdict, reason, fetched_at,"
                         " due_at, hard_due_at, purged_at, meta_json) VALUES (?, ?, ?, 't', ?, 'x', ?, ?, ?, '2026-11-07T10:00:00+08:00',"
                         " '2026-12-07T10:00:00+08:00', '2027-01-06T10:00:00+08:00', ?, ?)",
                         (wid, kind, unit, path, origin, verdict, reason, purged, json.dumps(meta or {}))).lastrowid


def _recorded(refs, root):
    """一部红果作品:第 1 集收(带第 3 秒关键帧、转写),第 2 集不收(带锁)。"""
    import json
    wid = works.upsert(refs, "hongguo", "7561390625552092222", title="掌上珠")
    (root / "content/hongguo/7561390625552092222").mkdir(parents=True)
    (root / "content/hongguo/7561390625552092222/ep1.webm").write_bytes(b"webm")
    (root / "content/hongguo/7561390625552092222/3.jpg").write_bytes(b"\xff\xd8jpeg")
    meta = {"expected_sec": 61.0, "duration": 60.5, "mean_volume_db": -18.3, "luma_mean": 97.4, "luma_sd": 40.2}
    rec = _content(refs, wid, "recording", 1, "content/hongguo/7561390625552092222/ep1.webm", meta=meta)
    kf = _content(refs, wid, "keyframe", 1, "content/hongguo/7561390625552092222/3.jpg", origin=f"录屏 #{rec}", meta={"sec": 3.0})
    _content(refs, wid, "recording", 2, "", verdict="rejected", reason="网页带锁(「扫码进入 App 端」)", purged="2026-11-07T10:00:00+08:00")
    text = "你怎么还在这里" * 12
    (root / "content/hongguo/7561390625552092222/ep1.transcript.json").write_text(json.dumps({"text": text, "chars": []}), encoding="utf-8")
    tr = _content(refs, wid, "transcript", 1, "content/hongguo/7561390625552092222/ep1.transcript.json", origin=f"whisper 转写录屏 #{rec}")
    return wid, rec, kf, tr, text


def test_record_page_lists_each_episode_with_checks_keyframe_and_transcript(rc):
    c, refs, root = rc
    wid, rec, kf, _, text = _recorded(refs, root)
    jobs.enqueue(refs, "hongguo", "record", day="2026-11-07", work_id=wid)
    t = c.get("/refs/record").text
    assert t.count("data-ref-record") == 2 and "掌上珠" in t
    assert "60.5" in t and "61.0" in t and "-18.3" in t and "97.4" in t
    assert f'src="/refs/file/{kf}"' in t and text[:60] in t and text[:61] not in t
    assert "带锁" in t and "打开浏览器扫码" in t


def test_record_page_says_not_configured_without_refs(client):
    r = client.get("/refs/record")
    assert r.status_code == 200 and "未配置参考库" in r.text


def test_record_button_only_for_hongguo(rc):
    c, refs, _ = rc
    hg = works.upsert(refs, "hongguo", "1", title="红果的剧")
    jj = works.upsert(refs, "jjwxc", "2", title="晋江的书")
    assert "录这部" in c.get(f"/refs/work?id={hg}").text
    assert "录这部" not in c.get(f"/refs/work?id={jj}").text
    r = c.post(f"/refs/work/{jj}/record")
    assert r.status_code == 400 and "只有红果" in r.text
    assert c.post(f"/refs/work/{hg}/record", follow_redirects=False).status_code == 303
    assert [(j["kind"], j["work_id"], jobs.params(j)) for j in jobs.recent(refs)] == [("record", hg, {})]


def test_work_page_shows_suggested_recording(rc):
    c, refs, _ = rc
    from tools.refs import clock
    hg = works.upsert(refs, "hongguo", "1", title="红果的剧")
    works.set_candidate(refs, hg, "建议录屏:今天第 1 名(前 2 名)", clock.today())
    assert "建议录屏:今天第 1 名" in c.get(f"/refs/work?id={hg}").text


def test_redo_one_episode_queues_it_again_after_failure(rc):
    c, refs, _ = rc
    hg = works.upsert(refs, "hongguo", "1", title="红果的剧")
    c.post(f"/refs/work/{hg}/record", data={"episode": "3"})
    j = jobs.recent(refs)[0]
    assert jobs.params(j) == {"episodes": [3]}
    jobs.start(refs, j["id"]); jobs.finish(refs, j["id"], error="录坏了")
    c.post(f"/refs/work/{hg}/record", data={"episode": "3"})
    assert [x["state"] for x in jobs.recent(refs)] == ["waiting", "failed"]
    r = c.post(f"/refs/work/{hg}/record", data={"episode": "6"})
    assert r.status_code == 400 and "前 5 集" in r.text


def test_refs_file_serves_only_live_keyframes_and_recordings_inside_root(rc, tmp_path):
    c, refs, root = rc
    wid, rec, kf, tr, _ = _recorded(refs, root)
    r = c.get(f"/refs/file/{kf}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content == b"\xff\xd8jpeg"
    assert c.get(f"/refs/file/{rec}").headers["content-type"] == "video/webm"
    (tmp_path / "outside.jpg").write_bytes(b"secret")
    escape = _content(refs, wid, "keyframe", 1, "../outside.jpg")
    gone = _content(refs, wid, "keyframe", 1, "content/hongguo/7561390625552092222/3.jpg", purged="2026-11-08T00:00:00+08:00")
    for cid in (tr, escape, gone, 9999):
        assert c.get(f"/refs/file/{cid}").status_code == 404, cid


def test_nav_badge_counts_refs_todo_and_hides_zero(rc, client):
    c, refs, root = rc
    assert "data-nav-todo" not in c.get("/refs/ranks").text
    assert "data-nav-todo" not in client.get("/refs/ranks").text       # 没配参考库不算
    from tools.refs import clock
    settings.trip(refs, root, "k17", "被挡住", clock.today())
    loginstate.record(refs, root, False, "录屏前检查")
    t = c.get("/refs/ranks").text
    assert 'data-nav-todo>2<' in t
    assert "红果没登录" in c.get("/refs/sources").text


def test_login_todo_follows_the_latest_login_in_meta(rc):
    c, refs, root = rc
    loginstate.record(refs, root, False, "录屏前检查")
    assert "红果没登录" in c.get("/refs/sources").text
    loginstate.record(refs, root, True, "扫码登录")
    assert "红果没登录" not in c.get("/refs/sources").text
    assert "已登录" in c.get("/refs/record").text
    assert [e["ok"] for e in rjournal.read(root, event="login")] == [True, False]      # 照旧留痕
    rjournal.append(root, "login", source="hongguo", ok=False, via="journal 里的旧事件")   # 面板只读 meta
    assert "红果没登录" not in c.get("/refs/sources").text


def test_corrupt_journal_lines_do_not_break_pages(rc):
    c, refs, root = rc
    wid = works.upsert(refs, "hongguo", "1", title="红果的剧")
    rjournal.append(root, "fetch", source="hongguo", work_id=wid, url="u")
    d = root / "journal" / "refs"
    (d / "2000-01-01.jsonl").write_bytes(b"{broken\n")
    for url in ("/refs/sources", f"/refs/work?id={wid}", "/refs/record", "/refs/ranks"):
        r = c.get(url)
        assert r.status_code == 200, (url, r.text[:300])
    assert "2000-01-01.jsonl 第 1 行" in c.get("/refs/sources").text
    assert "2000-01-01.jsonl 第 1 行" in c.get(f"/refs/work?id={wid}").text


def test_todo_that_cannot_be_read_shows_bang_not_400(rc, monkeypatch):
    c, refs, root = rc
    from tools.serve.panels import refs as refs_panel
    def boom(*a, **k):
        raise RuntimeError("库被锁住")
    monkeypatch.setattr(refs_panel, "todo", boom)
    r = c.get("/refs/ranks")
    assert r.status_code == 200 and "data-nav-todo>!<" in r.text
    t = c.get("/refs/sources")
    assert t.status_code == 200 and "参考库待办读不出:RuntimeError: 库被锁住" in t.text


def test_login_button_needs_a_hongguo_work(rc):
    c, _, _ = rc
    r = c.post("/refs/login")
    assert r.status_code == 400 and "还没有红果作品" in r.text


def test_rerecord_after_done_the_same_day_queues_a_new_job(rc):
    c, refs, _ = rc
    hg = works.upsert(refs, "hongguo", "1", title="红果的剧")
    c.post(f"/refs/work/{hg}/record", data={"episode": "2"})
    c.post(f"/refs/work/{hg}/record", data={"episode": "2"})          # 还在等:重复点不重复排
    assert len(jobs.recent(refs)) == 1
    j = jobs.recent(refs)[0]
    jobs.start(refs, j["id"]); jobs.finish(refs, j["id"])
    c.post(f"/refs/work/{hg}/record", data={"episode": "2"})          # 录完了同一天再点「重录」:新排一条
    assert [x["state"] for x in jobs.recent(refs)] == ["waiting", "done"]
    c.post(f"/refs/work/{hg}/record")                                  # 「录这部」同理
    c.post(f"/refs/work/{hg}/record")
    assert len(jobs.recent(refs)) == 3


def test_retranscribe_button_queues_a_new_transcription_with_checks(rc):
    c, refs, root = rc
    wid, rec, kf, tr, _ = _recorded(refs, root)
    t = c.get("/refs/record").text
    assert f'action="/refs/content/{rec}/transcribe"' in t and "重新转写" in t
    assert c.post(f"/refs/content/{rec}/transcribe", follow_redirects=False).status_code == 303
    c.post(f"/refs/content/{rec}/transcribe")                     # 还在等:不重复排
    tj = [j for j in jobs.recent(refs) if j["kind"] == "transcribe"]
    assert len(tj) == 1 and jobs.params(tj[0]) == {"recording_id": rec} and tj[0]["work_id"] == wid
    jobs.start(refs, tj[0]["id"]); jobs.finish(refs, tj[0]["id"], error="连续 3 次 ssh 连不上")
    c.post(f"/refs/content/{rec}/transcribe")                     # 失败了再点:新排
    assert len([j for j in jobs.recent(refs) if j["kind"] == "transcribe"]) == 2
    locked = refs.conn.execute("SELECT id FROM ref_content WHERE kind = 'recording' AND unit = 2").fetchone()[0]
    for cid, why in ((kf, "不是录屏"), (locked, "不收"), (tr, "不是录屏")):
        r = c.post(f"/refs/content/{cid}/transcribe")
        assert r.status_code == 400 and why in r.text, (cid, r.text)
    with refs.write() as x:
        x.execute("UPDATE ref_content SET purged_at = '2026-12-08T00:00:00+08:00' WHERE id = ?", (rec,))
    r = c.post(f"/refs/content/{rec}/transcribe")
    assert r.status_code == 400 and "清理" in r.text
    assert c.post("/refs/content/9999/transcribe").status_code == 404


def test_record_page_shows_keyframe_error(rc):
    import json
    c, refs, root = rc
    wid, rec, _, _, _ = _recorded(refs, root)
    with refs.write() as x:
        x.execute("UPDATE ref_content SET meta_json = json_set(meta_json, '$.keyframe_error', ?) WHERE id = ?",
                  ("第 3.00 秒抽不出关键帧", rec))
    t = c.get("/refs/record").text
    assert "data-ref-keyframe-error" in t and "第 3.00 秒抽不出关键帧" in t


def test_suggested_recordings_are_not_counted_as_candidates_and_expire(rc):
    c, refs, _ = rc
    from tools.refs import clock
    from tools.serve.panels import refs as refs_panel
    hg = works.upsert(refs, "hongguo", "1", title="红果的剧")
    works.set_candidate(refs, hg, "建议录屏:今天第 1 名(前 2 名)", clock.today())
    assert not any("候选" in t for t in refs_panel.todo(refs, clock.today()))
    jj = works.upsert(refs, "jjwxc", "2", title="晋江的书")
    works.set_candidate(refs, jj, "今天第 2 名(前 2 名)", clock.today())
    assert "今天有 1 部候选等你挑" in refs_panel.todo(refs, clock.today())
    works.set_candidate(refs, hg, "建议录屏:今天第 1 名(前 2 名)", "2000-01-01")
    assert "data-ref-suggest" not in c.get(f"/refs/work?id={hg}").text      # 过了那天不再提示


def test_source_form_can_pick_forever_and_work_page_shows_it(rc):
    c, refs, root = rc
    c.post("/refs/source/manual", data={"retention_kind": "forever", "retention_days": ""})
    assert settings.get(refs, "manual")["retention_forever"] == 1
    t = c.get("/refs/sources").text
    assert 'value="forever" selected' in t
    wid = works.upsert(refs, "manual", "manual-1", title="x")
    c.post(f"/refs/work/{wid}/manual", data={"title": "梗概", "text": BODY, "origin": "o"})
    w = c.get(f"/refs/work?id={wid}").text
    assert "永久(手动删)" in w and "9999" not in w
    log = c.get("/refs/sources").text
    assert "永久" in log


def _manual_work(c, refs, root):
    settings.update(refs, root, "manual", {"retention_days": "10"})
    wid = works.upsert(refs, "manual", "manual-1", title="x")
    c.post(f"/refs/work/{wid}/manual", data={"title": "梗概", "text": BODY, "origin": "o"})
    return wid


def test_purge_work_asks_first_then_deletes_and_keeps_fingerprint(rc):
    c, refs, root = rc
    wid = _manual_work(c, refs, root)
    assert f"/refs/work/{wid}/purge" in c.get(f"/refs/work?id={wid}").text
    r = c.post(f"/refs/work/{wid}/purge")
    assert r.status_code == 200 and "不可撤销" in r.text and "指纹" in r.text and "1 个文件" in r.text and "manual 1 个" in r.text
    assert refs.conn.execute("SELECT purged_at FROM ref_content").fetchone()[0] == ""
    r = c.post(f"/refs/work/{wid}/purge", data={"confirm": "1"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/refs/work?id={wid}"
    assert refs.conn.execute("SELECT purged_at FROM ref_content").fetchone()[0]
    assert refs.conn.execute("SELECT COUNT(*) FROM ref_fingerprint").fetchone()[0] > 0
    assert not works.get(refs, wid)["blocked"]
    assert f"/refs/work/{wid}/purge" not in c.get(f"/refs/work?id={wid}").text      # 没有可删的了,按钮不显示


def test_purge_work_unknown_is_404_and_failure_goes_to_errors(rc):
    c, refs, root = rc
    assert c.post("/refs/work/999/purge", data={"confirm": "1"}).status_code == 404
    wid = _manual_work(c, refs, root)
    (root / refs.conn.execute("SELECT path FROM ref_content").fetchone()[0]).unlink()
    c.post(f"/refs/work/{wid}/purge", data={"confirm": "1"})
    assert "找不到" in c.get("/refs/sources").text
    assert refs.conn.execute("SELECT purged_at FROM ref_content").fetchone()[0] == ""
