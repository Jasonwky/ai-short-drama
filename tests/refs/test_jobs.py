# -*- coding: utf-8 -*-
import pytest

from tools.refs import jobs, settings, works


@pytest.fixture
def r(refs):
    settings.ensure(refs)
    return refs


def test_same_job_same_day_is_not_queued_twice(r):
    a = jobs.enqueue(r, "jjwxc", "ranks", day="2026-11-07", params={"board": "topten7"})
    assert jobs.enqueue(r, "jjwxc", "ranks", day="2026-11-07", params={"board": "topten7"}) == a
    assert jobs.enqueue(r, "jjwxc", "ranks", day="2026-11-08", params={"board": "topten7"}) != a


def test_failed_job_can_be_queued_again_by_hand(r):
    a = jobs.enqueue(r, "jjwxc", "ranks", day="d")
    jobs.finish(r, a, error="x")
    assert jobs.enqueue(r, "jjwxc", "ranks", day="d") != a


def test_auto_content_count_ignores_manual(r):
    w1, w2 = works.upsert(r, "jjwxc", "1"), works.upsert(r, "jjwxc", "2")
    jobs.enqueue(r, "jjwxc", "content", work_id=w1, day="d", auto=True)
    jobs.enqueue(r, "jjwxc", "content", work_id=w2, day="d", auto=False)
    assert jobs.auto_content_today(r, "jjwxc", "d") == 1


def test_next_skips_blocked_sources(r, tmp_path):
    jobs.enqueue(r, "zongheng", "ranks", day="2026-11-07")
    b = jobs.enqueue(r, "k17", "ranks", day="2026-11-07")
    settings.trip(r, tmp_path, "zongheng", "412", "2026-11-07")
    assert jobs.next_waiting(r, "2026-11-07")["id"] == b


def test_retry_puts_it_back_and_counts_attempts(r):
    a = jobs.enqueue(r, "k17", "ranks", day="d")
    jobs.start(r, a)
    jobs.retry(r, a, "超时")
    j = jobs.get(r, a)
    assert j["state"] == "waiting" and j["attempts"] == 1 and j["error"] == "超时"


def test_auto_enqueue_does_not_requeue_after_failure(r):
    a = jobs.enqueue(r, "jjwxc", "ranks", day="d", params={"board": "x"}, again_after_failure=False)
    jobs.finish(r, a, error="HTTP 500")
    assert jobs.enqueue(r, "jjwxc", "ranks", day="d", params={"board": "x"}, again_after_failure=False) == a
    assert jobs.enqueue(r, "jjwxc", "ranks", day="d", params={"board": "x"}) != a        # 人点照样能重试


DAY = "2026-11-07"


@pytest.fixture
def a_mode(r, tmp_path):
    settings.update(r, tmp_path, "jjwxc", {"mode": "A", "retention_days": "30", "chapters_n": "5", "cap_fetch": "2",
                                           "cap_breakdown": "0", "top_k": "5"})
    ws = [works.upsert(r, "jjwxc", str(i)) for i in range(3)]
    auto = [jobs.enqueue(r, "jjwxc", "content", work_id=w, day=DAY, auto=True, again_after_failure=False) for w in ws[:2]]
    by_hand = jobs.enqueue(r, "jjwxc", "content", work_id=ws[2], day=DAY)
    return r, tmp_path, auto, by_hand


def test_lowering_the_cap_stops_waiting_auto_jobs(a_mode):
    r, root, (j1, j2), by_hand = a_mode
    assert jobs.next_waiting(r, DAY)["id"] == j1
    jobs.start(r, j1)
    jobs.finish(r, j1)
    settings.update(r, root, "jjwxc", {"cap_fetch": "1"})
    assert jobs.next_waiting(r, DAY)["id"] == by_hand                     # 标失败后接着找下一条;人点的不受上限
    assert jobs.get(r, j2)["state"] == "failed" and jobs.get(r, j2)["error"] == "今日自动抓取已满 1/1"


def test_cap_zero_stops_auto_jobs_at_once(a_mode):
    r, root, (j1, j2), by_hand = a_mode
    settings.update(r, root, "jjwxc", {"cap_fetch": "0"})
    assert jobs.next_waiting(r, DAY)["id"] == by_hand
    assert [jobs.get(r, j)["error"] for j in (j1, j2)] == ["今日自动抓取已满 0/0"] * 2


def test_switching_to_c_stops_auto_jobs(a_mode):
    r, root, (j1, j2), by_hand = a_mode
    ranks = jobs.enqueue(r, "jjwxc", "ranks", day=DAY, params={"board": "x"}, auto=True, again_after_failure=False)
    settings.update(r, root, "jjwxc", {"mode": "C"})
    assert jobs.next_waiting(r, DAY)["id"] == by_hand
    assert {jobs.get(r, j)["error"] for j in (j1, j2)} == {"来源已不是 A 模式"}
    jobs.finish(r, by_hand)
    assert jobs.next_waiting(r, DAY) is None
    assert jobs.get(r, ranks)["error"] == "来源已切到 C 全手动"


def test_auto_jobs_are_not_run_on_a_later_day(a_mode):
    r, root, (j1, j2), by_hand = a_mode
    assert jobs.next_waiting(r, "2026-11-08")["id"] == by_hand            # 人点的照跑
    assert {jobs.get(r, j)["error"] for j in (j1, j2)} == {"自动任务不补跑(排于 2026-11-07 日)"}


def test_auto_count_ignores_auto_jobs_failed_before_they_started(a_mode):
    r, root, (j1, j2), _ = a_mode
    settings.update(r, root, "jjwxc", {"cap_fetch": "0"})
    jobs.next_waiting(r, DAY)
    assert jobs.auto_content_today(r, "jjwxc", DAY) == 0                  # 没打过网站的不算用量,调高上限能接着跑
