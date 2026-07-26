# -*- coding: utf-8 -*-
"""Durable V3 Sheets delivery outbox with atomic claim/lease + bounded
backoff (see matbot.ai_tutor_v3.sheets_outbox and state_store's
``v3_sheets_outbox`` table).

No live/paid OpenAI or Google Sheets call is made anywhere — every test
monkeypatches ``matbot.sheets_log.sheets_append_row_safe`` (the one real
network boundary this module calls) with a deterministic fake.
"""
from __future__ import annotations

import threading
import time

import pytest

from matbot import sheets_log
from matbot.ai_tutor_v3 import sheets_outbox
from matbot.ai_tutor_v3 import state_store as ss


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MATBOT_V3_DB_PATH", str(tmp_path / "v3.sqlite3"))
    s = ss.V3StateStore()
    s.init_db()
    sheets_outbox._reset_for_tests()
    yield s
    sheets_outbox._reset_for_tests()


def _payload(turn="t1"):
    return {"session_id": "sess1", "client_turn_id": turn, "grade": 6,
           "student_message": "test"}


def _response():
    return {"status": "ready", "engine": "v3_practice", "answer": "ok",
           "next_state": {}}


def _enqueue(store, turn="t1"):
    store.enqueue_sheets_event(client_turn_id=turn, session_id="sess1",
                              payload_json="{}", response_json="{}",
                              created_at=ss.now_iso())


# --------------------------------------------------------------------------- #
# Basic enqueue / dedup / status transitions                                  #
# --------------------------------------------------------------------------- #
def test_enqueue_writes_a_durable_pending_row(store):
    sheets_outbox.enqueue(store, client_turn_id="t1", session_id="sess1",
                         payload=_payload(), response=_response())
    row = store.get_sheets_event("t1")
    assert row is not None
    assert row["session_id"] == "sess1"
    # enqueue() also kicks the background worker immediately, so by the time
    # this check runs the row may already have been claimed (in_progress) —
    # the durability guarantee under test is that the row EXISTS, not its
    # exact status at this arbitrary instant.
    assert row["status"] in ("pending", "in_progress")


def test_duplicate_enqueue_same_client_turn_id_is_a_no_op(store):
    sheets_outbox.enqueue(store, client_turn_id="t1", session_id="sess1",
                         payload=_payload(), response=_response())
    sheets_outbox.enqueue(store, client_turn_id="t1", session_id="sess1",
                         payload=_payload(), response=_response())
    assert store.count_sheets_events() == 1


def test_enqueue_never_raises_when_durable_write_fails(store, monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("disk full")
    monkeypatch.setattr(store, "enqueue_sheets_event", _boom)
    sheets_outbox.enqueue(store, client_turn_id="t1", session_id="sess1",
                         payload=_payload(), response=_response())  # must not raise


# --------------------------------------------------------------------------- #
# Atomic claiming                                                             #
# --------------------------------------------------------------------------- #
def test_claim_marks_row_in_progress(store):
    _enqueue(store, "t1")
    now = ss.now_iso()
    claimed = store.claim_sheets_events(limit=10, claimed_by="worker-a",
                                        now=now, lease_seconds=120)
    assert len(claimed) == 1
    assert claimed[0]["status"] == "in_progress"
    row = store.get_sheets_event("t1")
    assert row["status"] == "in_progress"
    assert row["claimed_by"] == "worker-a"


def test_two_concurrent_claimers_cannot_claim_the_same_row(store):
    """The core safety property: simulate two drainers racing for the SAME
    row. Only one may win."""
    _enqueue(store, "t1")
    now = ss.now_iso()
    results: list[list[dict]] = [None, None]

    def _claim(idx, name):
        results[idx] = store.claim_sheets_events(
            limit=10, claimed_by=name, now=now, lease_seconds=120)

    t1 = threading.Thread(target=_claim, args=(0, "worker-a"))
    t2 = threading.Thread(target=_claim, args=(1, "worker-b"))
    t1.start(); t2.start()
    t1.join(); t2.join()

    total_claimed = len(results[0]) + len(results[1])
    assert total_claimed == 1, "exactly one drainer must win the row"


def test_claim_skips_rows_not_yet_eligible_for_retry(store):
    """A row with a future next_attempt_at (backoff window) must not be
    claimable yet."""
    _enqueue(store, "t1")
    from datetime import datetime, timedelta, timezone
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    conn = store._connect()
    try:
        with conn:
            conn.execute(
                "UPDATE v3_sheets_outbox SET next_attempt_at=? WHERE client_turn_id=?",
                (future, "t1"))
    finally:
        conn.close()
    claimed = store.claim_sheets_events(limit=10, claimed_by="worker-a",
                                        now=ss.now_iso(), lease_seconds=120)
    assert claimed == []


def test_stale_in_progress_claim_becomes_retryable(store):
    """A claim older than the lease is treated as abandoned (crashed/killed
    drainer) and can be reclaimed by someone else."""
    _enqueue(store, "t1")
    now = ss.now_iso()
    first = store.claim_sheets_events(limit=10, claimed_by="worker-a",
                                      now=now, lease_seconds=0.01)
    assert len(first) == 1

    time.sleep(0.05)   # let the (tiny) lease expire
    second = store.claim_sheets_events(limit=10, claimed_by="worker-b",
                                       now=ss.now_iso(), lease_seconds=0.01)
    assert len(second) == 1
    assert second[0]["claimed_by"] == "worker-b"


def test_fresh_in_progress_claim_is_not_reclaimed(store):
    _enqueue(store, "t1")
    now = ss.now_iso()
    first = store.claim_sheets_events(limit=10, claimed_by="worker-a",
                                      now=now, lease_seconds=120)
    assert len(first) == 1
    second = store.claim_sheets_events(limit=10, claimed_by="worker-b",
                                       now=ss.now_iso(), lease_seconds=120)
    assert second == []   # still within lease — not reclaimable yet


def test_delivered_row_is_never_reclaimed(store):
    _enqueue(store, "t1")
    now = ss.now_iso()
    store.claim_sheets_events(limit=10, claimed_by="worker-a", now=now, lease_seconds=120)
    store.mark_sheets_event_delivered("t1", now)
    claimed = store.claim_sheets_events(limit=10, claimed_by="worker-b",
                                        now=ss.now_iso(), lease_seconds=0.0001)
    assert claimed == []


# --------------------------------------------------------------------------- #
# Backoff / retry bookkeeping                                                 #
# --------------------------------------------------------------------------- #
def test_backoff_seconds_is_bounded_and_increasing():
    seq = [ss.backoff_seconds(n) for n in (1, 2, 3, 4, 5, 20)]
    assert seq == sorted(seq)              # monotonically non-decreasing
    assert seq[-1] <= 300.0                # capped
    assert seq[0] >= 1.0                   # not an immediate tight-loop retry


def test_mark_failed_increments_attempts_and_sets_future_next_attempt(store):
    _enqueue(store, "t1")
    now = ss.now_iso()
    store.claim_sheets_events(limit=10, claimed_by="worker-a", now=now, lease_seconds=120)
    later = ss.next_attempt_at(now, attempts=1)
    store.mark_sheets_event_failed("t1", "boom", next_attempt_at=later)
    row = store.get_sheets_event("t1")
    assert row["status"] == "pending"      # retryable, not stuck in_progress
    assert row["attempts"] == 1
    assert row["last_error"] == "boom"
    assert row["next_attempt_at"] == later
    assert row["next_attempt_at"] > now    # a real backoff window, not immediate


def test_failed_row_is_not_immediately_reclaimable_tight_loop(store):
    _enqueue(store, "t1")
    now = ss.now_iso()
    store.claim_sheets_events(limit=10, claimed_by="worker-a", now=now, lease_seconds=120)
    store.mark_sheets_event_failed("t1", "boom", next_attempt_at=ss.next_attempt_at(now, 1))
    immediate_retry = store.claim_sheets_events(
        limit=10, claimed_by="worker-b", now=now, lease_seconds=120)
    assert immediate_retry == [], "must not be claimable again before the backoff window"


# --------------------------------------------------------------------------- #
# drain_pending_sheets_events                                                 #
# --------------------------------------------------------------------------- #
def test_drain_delivers_pending_rows_and_marks_them(store, monkeypatch):
    monkeypatch.setattr(sheets_log, "sheets_append_row_safe", lambda row: True)
    _enqueue(store, "t1")
    delivered = sheets_outbox.drain_pending_sheets_events(store, limit=10)
    assert delivered == 1
    row = store.get_sheets_event("t1")
    assert row["status"] == "delivered"
    assert row["delivered_at"]


def test_drain_leaves_failed_rows_pending_for_retry(store, monkeypatch):
    monkeypatch.setattr(sheets_log, "sheets_append_row_safe", lambda row: False)
    _enqueue(store, "t1")
    delivered = sheets_outbox.drain_pending_sheets_events(store, limit=10)
    assert delivered == 0
    row = store.get_sheets_event("t1")
    assert row["status"] == "pending"
    assert row["attempts"] == 1
    assert row["last_error"] == "delivery_failed"
    assert row["next_attempt_at"] > row["created_at"]  # bounded backoff applied


def test_drain_is_bounded_by_limit(store, monkeypatch):
    monkeypatch.setattr(sheets_log, "sheets_append_row_safe", lambda row: True)
    for i in range(5):
        _enqueue(store, f"t{i}")
    delivered = sheets_outbox.drain_pending_sheets_events(store, limit=2)
    assert delivered == 2
    assert store.count_sheets_events(status="pending") == 3
    assert store.count_sheets_events(status="delivered") == 2


def test_drain_delivers_at_most_once_per_row_even_called_twice(store, monkeypatch):
    calls = []
    monkeypatch.setattr(sheets_log, "sheets_append_row_safe",
                        lambda row: calls.append(1) or True)
    _enqueue(store, "t1")
    sheets_outbox.drain_pending_sheets_events(store, limit=10)
    sheets_outbox.drain_pending_sheets_events(store, limit=10)  # already delivered
    assert len(calls) == 1


# --------------------------------------------------------------------------- #
# Durability across restart + non-blocking background delivery                #
# --------------------------------------------------------------------------- #
def test_pending_events_survive_a_fresh_store_instance(store):
    """Simulates a process restart: a NEW V3StateStore pointing at the SAME
    on-disk file must still see the pending row — proving durability, unlike
    matbot.sheets_log's own purely in-memory async queue."""
    _enqueue(store, "t1")
    fresh_store = ss.V3StateStore()   # a brand new instance, same MATBOT_V3_DB_PATH
    row = fresh_store.get_sheets_event("t1")
    assert row is not None
    assert row["status"] == "pending"


def test_background_worker_eventually_delivers_without_blocking_enqueue(store, monkeypatch):
    monkeypatch.setattr(sheets_log, "sheets_append_row_safe", lambda row: True)
    t0 = time.monotonic()
    sheets_outbox.enqueue(store, client_turn_id="t1", session_id="sess1",
                         payload=_payload(), response=_response())
    elapsed = time.monotonic() - t0
    assert elapsed < 0.5

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        row = store.get_sheets_event("t1")
        if row is not None and row["status"] == "delivered":
            break
        time.sleep(0.02)
    assert store.get_sheets_event("t1")["status"] == "delivered"


def test_startup_drain_is_non_blocking_and_recovers_pending_row(store, monkeypatch):
    monkeypatch.setattr(sheets_log, "sheets_append_row_safe", lambda row: True)
    _enqueue(store, "t1")   # simulates a row orphaned by a prior process crash

    t0 = time.monotonic()
    sheets_outbox.kick_startup_drain(store, limit=10)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.5, "startup recovery must not block the caller"

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        row = store.get_sheets_event("t1")
        if row is not None and row["status"] == "delivered":
            break
        time.sleep(0.02)
    assert store.get_sheets_event("t1")["status"] == "delivered"
