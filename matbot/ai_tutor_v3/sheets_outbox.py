# -*- coding: utf-8 -*-
"""Durable-then-async Google Sheets delivery for V3 Practice turns.

Why this exists: calling ``matbot.sheets_log.log_transcript_to_sheet``
directly from the request path inherited that module's ``SHEETS_ASYNC_LOG``
toggle — non-blocking by default, but a config drift to ``SHEETS_ASYNC_LOG=0``
would make a V3 turn wait on real network I/O, and either way nothing
survived a process crash between "logged" and "actually delivered"
(``sheets_log``'s own async queue is in-memory only — verified by reading it:
no disk/SQLite backing).

The fix, in two independent parts:

  1. ``enqueue()`` — called from the request path — does exactly ONE thing
     synchronously: a local SQLite insert into the durable
     ``v3_sheets_outbox`` table (``state_store.enqueue_sheets_event``). No
     network call, no dependency on any Sheets config, unconditionally fast.
     This alone is what guarantees the student response never waits on
     Google Sheets.
  2. Delivery happens entirely off the request path, through an ATOMIC CLAIM
     (``state_store.claim_sheets_events``) so it is safe under:
       * multiple threads in one Gunicorn worker process (the deployed
         default is ``--workers 1 --threads 8`` — see Dockerfile), and
       * multiple Gunicorn worker processes sharing the same SQLite file, if
         ``WEB_CONCURRENCY`` is ever raised above the documented default.
     Three independent drain entry points all go through the SAME claim
     primitive, so none of them can double-claim or double-deliver a row:
       * the lazy, reused background worker thread kicked by ``enqueue()``
         (fast-path delivery for the turn that was just committed),
       * ``drain_pending_sheets_events`` — a bounded, synchronous sweep meant
         to be called by an external trigger (the existing ``/sheets/flush``
         endpoint now calls it; see app.py),
       * ``kick_startup_drain`` — one bounded, one-shot background sweep
         fired once at process start, recovering anything a prior process
         crashed before delivering.

A claimed-but-never-finished row (the drainer that claimed it crashed or was
killed) becomes reclaimable again after ``DEFAULT_LEASE_SECONDS`` — it is
never stuck ``in_progress`` forever. A failed delivery goes back to
``pending`` with an incremented ``attempts`` and a bounded exponential
``next_attempt_at`` backoff (``state_store.backoff_seconds``) — never a tight
retry loop.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
from typing import Optional

from matbot import sheets_log
from matbot.ai_tutor_v3.state_store import V3StateStore, next_attempt_at, now_iso

log = logging.getLogger("matbot.ai_tutor_v3.sheets_outbox")

#: How long an ``in_progress`` claim is honoured before another drainer may
#: reclaim the row — bounded so a crashed/killed drainer never blocks
#: delivery forever.
DEFAULT_LEASE_SECONDS = 120.0

#: Bounded batch size for the opportunistic fast-path and the startup sweep
#: — never an unbounded backlog scan.
DEFAULT_DRAIN_LIMIT = 50

_queue: Optional["queue.Queue[str]"] = None
_worker: Optional[threading.Thread] = None
_lock = threading.Lock()


def _claim_identity() -> str:
    return f"pid{os.getpid()}:thread{threading.get_ident()}"


def _ensure_worker(store: V3StateStore) -> "queue.Queue[str]":
    global _queue, _worker
    with _lock:
        if _queue is None:
            _queue = queue.Queue()
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(
                target=_worker_loop, args=(store, _queue),
                name="matbot-v3-sheets-outbox", daemon=True)
            _worker.start()
        return _queue


def _worker_loop(store: V3StateStore, q: "queue.Queue[str]") -> None:
    while True:
        q.get()   # the specific client_turn_id is informational only — the
                  # worker always attempts a bounded claim-and-drain pass,
                  # which naturally also picks up anything else pending.
        try:
            drain_pending_sheets_events(store, limit=DEFAULT_DRAIN_LIMIT)
        except Exception:
            log.exception("v3 sheets outbox worker: unexpected failure")
        finally:
            try:
                q.task_done()
            except Exception:
                pass


def _append(row: dict) -> bool:
    try:
        payload = json.loads(row["payload_json"])
        response = json.loads(row["response_json"])
    except (ValueError, TypeError):
        return False
    sheet_row = sheets_log._build_transcript_row(payload, response)
    return sheets_log.sheets_append_row_safe(sheet_row)


def enqueue(store: V3StateStore, *, client_turn_id: str, session_id: str,
           payload: dict, response: dict) -> None:
    """Durable local write (fast, no network) + a best-effort prompt-delivery
    kick on the shared background worker. Never raises — a failure here must
    never affect the already-committed student response."""
    try:
        store.enqueue_sheets_event(
            client_turn_id=client_turn_id, session_id=session_id,
            payload_json=json.dumps(payload, ensure_ascii=False),
            response_json=json.dumps(response, ensure_ascii=False),
            created_at=now_iso())
    except Exception:
        log.exception("v3 sheets outbox: durable enqueue failed "
                      "client_turn_id=%s", client_turn_id)
        return
    try:
        _ensure_worker(store).put(client_turn_id)
    except Exception:
        log.exception("v3 sheets outbox: failed to kick background worker "
                      "client_turn_id=%s", client_turn_id)


def drain_pending_sheets_events(
    store: V3StateStore, limit: int = DEFAULT_DRAIN_LIMIT,
    lease_seconds: float = DEFAULT_LEASE_SECONDS,
) -> int:
    """Bounded, synchronous claim-and-deliver sweep. NEVER call from the
    request path — this does real (retry-capable but blocking) network I/O.

    Safe to call concurrently from multiple threads/processes: each row is
    claimed atomically (``state_store.claim_sheets_events``) before delivery
    is attempted, so two concurrent callers can never both deliver the same
    row. Returns the number of rows successfully delivered in this call.
    """
    claimed_by = _claim_identity()
    claimed = store.claim_sheets_events(
        limit=limit, claimed_by=claimed_by, now=now_iso(),
        lease_seconds=lease_seconds)
    delivered = 0
    for row in claimed:
        client_turn_id = row["client_turn_id"]
        if _append(row):
            store.mark_sheets_event_delivered(client_turn_id, now_iso())
            delivered += 1
        else:
            attempts = int(row["attempts"] or 0) + 1
            store.mark_sheets_event_failed(
                client_turn_id, "delivery_failed",
                next_attempt_at=next_attempt_at(now_iso(), attempts))
    return delivered


def kick_startup_drain(store: Optional[V3StateStore] = None,
                       limit: int = DEFAULT_DRAIN_LIMIT) -> None:
    """One bounded, one-shot background sweep — meant to be called once at
    process start to recover anything a PRIOR process crashed before
    delivering. Never blocks the caller (starts a daemon thread and returns
    immediately); safe to call from every Gunicorn worker process, since
    delivery itself goes through the same atomic claim as everything else."""
    target_store = store or V3StateStore()

    def _run() -> None:
        try:
            delivered = drain_pending_sheets_events(target_store, limit=limit)
            if delivered:
                log.info("v3 sheets outbox: startup recovery delivered %d row(s)",
                         delivered)
        except Exception:
            log.exception("v3 sheets outbox: startup recovery sweep failed")

    threading.Thread(target=_run, name="matbot-v3-sheets-startup-drain",
                     daemon=True).start()


def _reset_for_tests() -> None:
    """Test-only: force a fresh worker thread/queue for the next ``enqueue``."""
    global _queue, _worker
    with _lock:
        _queue = None
        _worker = None
