# -*- coding: utf-8 -*-
import json
import sqlite3

import pytest

from tools.drama import repo
from tools.drama.tasks import create_task, set_task_state, tasks_for


def _task(store, seg, seed=7):
    return create_task(store, kind="video", target_type="segment", target_id=seg,
                       model="minimax_h3_ref2va_pruned_fp8_scaled", params={"steps": 20, "frames": 192},
                       seed=seed, prompt="<Picture 1> 说话", refs=["frames/s1.png"])


@pytest.mark.parametrize("bad_seed", [None, True, 1.5, "7"])
def test_seed_must_be_explicit_int(store, sample, bad_seed):
    with pytest.raises(TypeError, match="seed"):
        _task(store, sample["seg"], seed=bad_seed)


def test_params_are_stored_canonically(store, sample):
    t = _task(store, sample["seg"])
    row = repo.get(store, "task", t)
    assert json.loads(row["params_json"]) == {"frames": 192, "steps": 20}
    assert row["state"] == "pending"


def test_happy_path_sets_timestamps(store, sample):
    t = _task(store, sample["seg"])
    set_task_state(store, t, "queued", external_id="prompt-abc")
    set_task_state(store, t, "running")
    set_task_state(store, t, "done", result_path="videos/s1.mp4", result_sha256="deadbeef")
    row = repo.get(store, "task", t)
    assert row["state"] == "done" and row["external_id"] == "prompt-abc"
    assert row["started_at"] and row["finished_at"]


def test_illegal_transition_rejected(store, sample):
    t = _task(store, sample["seg"])
    with pytest.raises(ValueError, match="非法状态迁移:pending → running"):
        set_task_state(store, t, "running")


def test_terminal_states_are_final(store, sample):
    t = _task(store, sample["seg"])
    set_task_state(store, t, "failed", error="OOM")
    with pytest.raises(ValueError, match="非法状态迁移"):
        set_task_state(store, t, "queued")


def test_failure_needs_reason_and_done_needs_result(store, sample):
    t = _task(store, sample["seg"])
    set_task_state(store, t, "queued")
    with pytest.raises(ValueError, match="原因"):
        set_task_state(store, t, "failed", error="  ")
    with pytest.raises(ValueError, match="产物路径"):
        set_task_state(store, t, "done")
    assert repo.get(store, "task", t)["state"] == "queued"


def test_waiting_host_is_recoverable(store, sample):
    t = _task(store, sample["seg"])
    set_task_state(store, t, "queued")
    set_task_state(store, t, "waiting_host")
    set_task_state(store, t, "running")
    assert repo.get(store, "task", t)["state"] == "running"


def test_rerun_is_a_new_row_and_history_is_kept(store, sample):
    a = _task(store, sample["seg"], seed=1)
    b = _task(store, sample["seg"], seed=2)
    assert [r["id"] for r in tasks_for(store, "segment", sample["seg"])] == [a, b]
    with pytest.raises(sqlite3.IntegrityError, match="不可变"):
        with store.write() as c:
            c.execute("UPDATE task SET prompt = 'changed' WHERE id = ?", (a,))
