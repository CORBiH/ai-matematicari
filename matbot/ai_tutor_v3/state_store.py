# -*- coding: utf-8 -*-
"""Durable V3 SQLite state — sessions, turns, blueprints, activity outbox.

Deliberately NOT the Minimal Engine's process-local idempotency: this is a
crash-durable store on the persistent volume, keyed by ``(session_id,
client_turn_id)`` at the DATABASE level so a resent SSE/JSON delivery cannot
double-apply counters even across a restart or a second worker process.

Transaction shape (the important part):

  1. reserve the turn as ``in_progress``   — short write txn, commit, release lock
  2. call the model                          — NO db transaction open
  3. load session, compare optimistic version, apply reducer outcome once,
     store the completed turn                — short write txn, commit

The store never holds a write lock across a model call. WAL + a busy timeout let
concurrent reads proceed while a turn commits.

This module imports only the standard library and ``matbot.ai_tutor_v3.schemas``
— no OpenAI, no frozen tutoring code, and (isolation test #3) importing it opens
no database. A connection is made only when a method actually runs.
"""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_DEFAULT_DB = Path(__file__).resolve().parent.parent.parent / "storage" / "v3_tutor.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS v3_blueprints (
    blueprint_id        TEXT PRIMARY KEY,
    lesson_id           TEXT NOT NULL,
    blueprint_version   TEXT NOT NULL,
    source_hash         TEXT NOT NULL,
    blueprint_json      TEXT NOT NULL,
    validation_status   TEXT NOT NULL,
    model               TEXT NOT NULL DEFAULT '',
    prompt_versions_json TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL,
    approved_at         TEXT,
    UNIQUE(lesson_id, source_hash)
);

CREATE TABLE IF NOT EXISTS v3_sessions (
    session_id          TEXT PRIMARY KEY,
    student_id          TEXT NOT NULL,
    grade               INTEGER NOT NULL,
    mode                TEXT NOT NULL,
    lesson_id           TEXT NOT NULL,
    blueprint_id        TEXT NOT NULL DEFAULT '',
    blueprint_version   TEXT NOT NULL DEFAULT '',
    state_json          TEXT NOT NULL,
    optimistic_version  INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'active',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    expires_at          TEXT
);

CREATE TABLE IF NOT EXISTS v3_turns (
    turn_id             TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL,
    client_turn_id      TEXT NOT NULL,
    request_id          TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL,
    state_version_before INTEGER,
    state_version_after INTEGER,
    input_summary_json  TEXT,
    interpretation_json TEXT,
    model_assessment_json TEXT,
    outcome_json        TEXT,
    response_json       TEXT,
    usage_json          TEXT,
    audit_json          TEXT,
    error_code          TEXT,
    created_at          TEXT NOT NULL,
    completed_at        TEXT,
    FOREIGN KEY (session_id) REFERENCES v3_sessions(session_id),
    UNIQUE(session_id, client_turn_id)
);

CREATE TABLE IF NOT EXISTS v3_activity_outbox (
    event_id            TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL,
    student_id          TEXT NOT NULL,
    event_type          TEXT NOT NULL,
    grade               INTEGER,
    mode                TEXT,
    lesson_id           TEXT,
    created_at          TEXT NOT NULL,
    summary_json        TEXT NOT NULL,
    ai_note             TEXT,
    delivered           INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (session_id) REFERENCES v3_sessions(session_id)
);

CREATE TABLE IF NOT EXISTS v3_sheets_outbox (
    client_turn_id      TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL,
    payload_json        TEXT NOT NULL,
    response_json       TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    -- 'pending' | 'in_progress' | 'delivered'. A row only ever moves
    -- pending -> in_progress -> delivered, or back pending on failure/stale
    -- claim — never delivered -> anything (delivery is terminal).
    status              TEXT NOT NULL DEFAULT 'pending',
    claimed_at          TEXT,
    claimed_by          TEXT,
    -- Eligible for (re)claim once now >= next_attempt_at. Set to created_at
    -- at insert (immediately eligible); pushed forward on failure (backoff).
    next_attempt_at     TEXT NOT NULL,
    delivered_at        TEXT,
    attempts            INTEGER NOT NULL DEFAULT 0,
    last_error          TEXT
);

CREATE INDEX IF NOT EXISTS idx_v3_turns_session ON v3_turns(session_id);
CREATE INDEX IF NOT EXISTS idx_v3_outbox_session ON v3_activity_outbox(session_id);
CREATE INDEX IF NOT EXISTS idx_v3_sheets_outbox_claim
    ON v3_sheets_outbox(status, next_attempt_at);
"""

#: Only these event types may ever enter the outbox (privacy scope).
ALLOWED_OUTBOX_EVENTS = frozenset(
    {"image_upload", "sos", "test_completed", "session_end"}
)


def resolve_db_path(path: str | Path | None = None) -> Path:
    """Explicit path > ``MATBOT_V3_DB_PATH`` > default storage file."""
    if path is not None:
        return Path(path)
    env = (os.getenv("MATBOT_V3_DB_PATH") or "").strip()
    return Path(env) if env else _DEFAULT_DB


@dataclass(frozen=True)
class TurnReservation:
    """Outcome of trying to reserve ``(session_id, client_turn_id)``."""
    #: "reserved"     — this caller owns the turn, proceed to the model call
    #: "completed"    — an identical turn already finished; ``response_json`` is set
    #: "in_progress"  — another caller owns it right now; caller should retry/conflict
    #: "retryable"    — a previous attempt failed; this caller may retry
    status: str
    turn_id: str = ""
    response_json: Optional[str] = None
    error_code: Optional[str] = None


class VersionConflict(RuntimeError):
    """Raised when the session's optimistic version moved under a turn commit."""


class V3StateStore:
    """Thin, connection-per-call SQLite wrapper. Safe to construct freely; it
    opens no connection until a method runs, and ``init_db`` is idempotent."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = resolve_db_path(db_path)
        self._initialized = False

    # -- connection ------------------------------------------------------- #
    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
        except sqlite3.Error:
            pass
        return conn

    def init_db(self) -> None:
        """Create tables + indices if absent. Safe to call repeatedly."""
        conn = self._connect()
        try:
            with conn:
                conn.executescript(_SCHEMA)
        finally:
            conn.close()
        self._initialized = True

    def _ensure(self) -> None:
        if not self._initialized:
            self.init_db()

    # -- blueprints ------------------------------------------------------- #
    def find_blueprint(self, lesson_id: str, source_hash: str) -> Optional[str]:
        """Return the stored blueprint JSON for this exact (lesson, source hash),
        or None. The UNIQUE(lesson_id, source_hash) constraint is what makes a
        blueprint reused instead of regenerated."""
        self._ensure()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT blueprint_json FROM v3_blueprints "
                "WHERE lesson_id=? AND source_hash=?",
                (lesson_id, source_hash),
            ).fetchone()
            return row["blueprint_json"] if row else None
        finally:
            conn.close()

    def store_blueprint(
        self, *, blueprint_id: str, lesson_id: str, blueprint_version: str,
        source_hash: str, blueprint_json: str, validation_status: str,
        model: str, prompt_versions_json: str, created_at: str,
    ) -> None:
        """Insert a blueprint. A duplicate (lesson_id, source_hash) is ignored —
        first writer wins, so a concurrent second generation cannot create a
        divergent duplicate."""
        self._ensure()
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO v3_blueprints "
                    "(blueprint_id, lesson_id, blueprint_version, source_hash, "
                    " blueprint_json, validation_status, model, "
                    " prompt_versions_json, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (blueprint_id, lesson_id, blueprint_version, source_hash,
                     blueprint_json, validation_status, model,
                     prompt_versions_json, created_at),
                )
        finally:
            conn.close()

    # -- sessions --------------------------------------------------------- #
    def load_session(self, session_id: str) -> Optional[tuple[str, int]]:
        """Return ``(state_json, optimistic_version)`` for a session, or None."""
        self._ensure()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT state_json, optimistic_version FROM v3_sessions "
                "WHERE session_id=?",
                (session_id,),
            ).fetchone()
            return (row["state_json"], row["optimistic_version"]) if row else None
        finally:
            conn.close()

    def create_session(
        self, *, session_id: str, student_id: str, grade: int, mode: str,
        lesson_id: str, blueprint_id: str, blueprint_version: str,
        state_json: str, created_at: str, updated_at: str,
    ) -> None:
        self._ensure()
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO v3_sessions "
                    "(session_id, student_id, grade, mode, lesson_id, "
                    " blueprint_id, blueprint_version, state_json, "
                    " optimistic_version, status, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,0,'active',?,?)",
                    (session_id, student_id, grade, mode, lesson_id,
                     blueprint_id, blueprint_version, state_json,
                     created_at, updated_at),
                )
        finally:
            conn.close()

    # -- turn lifecycle (idempotency) ------------------------------------ #
    def reserve_turn(
        self, *, session_id: str, client_turn_id: str, turn_id: str,
        request_id: str, created_at: str, input_summary_json: str = "",
    ) -> TurnReservation:
        """Phase 1: atomically claim ``(session_id, client_turn_id)``.

        A completed duplicate returns its stored response. An in-progress
        duplicate returns ``in_progress`` (caller decides retry/conflict). A
        previously failed turn is reset to ``in_progress`` and handed to this
        caller as ``retryable``.
        """
        self._ensure()
        conn = self._connect()
        try:
            with conn:
                existing = conn.execute(
                    "SELECT turn_id, status, response_json, error_code "
                    "FROM v3_turns WHERE session_id=? AND client_turn_id=?",
                    (session_id, client_turn_id),
                ).fetchone()
                if existing is not None:
                    st = existing["status"]
                    if st == "completed":
                        return TurnReservation(
                            status="completed", turn_id=existing["turn_id"],
                            response_json=existing["response_json"])
                    if st == "in_progress":
                        return TurnReservation(
                            status="in_progress", turn_id=existing["turn_id"])
                    # failed → reset for a fresh attempt by THIS caller
                    conn.execute(
                        "UPDATE v3_turns SET status='in_progress', error_code=NULL, "
                        "response_json=NULL WHERE turn_id=?",
                        (existing["turn_id"],))
                    return TurnReservation(
                        status="retryable", turn_id=existing["turn_id"])
                conn.execute(
                    "INSERT INTO v3_turns "
                    "(turn_id, session_id, client_turn_id, request_id, status, "
                    " input_summary_json, created_at) "
                    "VALUES (?,?,?,?, 'in_progress', ?, ?)",
                    (turn_id, session_id, client_turn_id, request_id,
                     input_summary_json, created_at))
                return TurnReservation(status="reserved", turn_id=turn_id)
        finally:
            conn.close()

    def commit_turn(
        self, *, session_id: str, turn_id: str, expected_version: int,
        new_state_json: str, updated_at: str,
        interpretation_json: str = "", model_assessment_json: str = "",
        outcome_json: str = "", response_json: str = "", usage_json: str = "",
        audit_json: str = "", completed_at: str = "",
        state_version_before: int = 0,
    ) -> int:
        """Phase 3: apply the reducer outcome and store the completed turn in ONE
        transaction, guarded by the session's optimistic version.

        Returns the new optimistic version. Raises ``VersionConflict`` if the
        session moved since ``expected_version`` — the caller must NOT retry the
        model call, only re-resolve state.
        """
        self._ensure()
        conn = self._connect()
        try:
            with conn:
                cur = conn.execute(
                    "UPDATE v3_sessions SET state_json=?, "
                    "optimistic_version=optimistic_version+1, updated_at=? "
                    "WHERE session_id=? AND optimistic_version=?",
                    (new_state_json, updated_at, session_id, expected_version))
                if cur.rowcount != 1:
                    raise VersionConflict(
                        f"session {session_id} version moved from {expected_version}")
                new_version = expected_version + 1
                conn.execute(
                    "UPDATE v3_turns SET status='completed', "
                    "state_version_before=?, state_version_after=?, "
                    "interpretation_json=?, model_assessment_json=?, outcome_json=?, "
                    "response_json=?, usage_json=?, audit_json=?, completed_at=? "
                    "WHERE turn_id=?",
                    (state_version_before, new_version, interpretation_json,
                     model_assessment_json, outcome_json, response_json,
                     usage_json, audit_json, completed_at or updated_at, turn_id))
                return new_version
        finally:
            conn.close()

    def fail_turn(self, *, turn_id: str, error_code: str,
                  completed_at: str = "") -> None:
        """Mark a turn failed WITHOUT touching session state — the active task
        and counters are preserved, and the turn can be retried later."""
        self._ensure()
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "UPDATE v3_turns SET status='failed', error_code=?, "
                    "completed_at=? WHERE turn_id=?",
                    (error_code, completed_at, turn_id))
        finally:
            conn.close()

    def get_turn(self, turn_id: str) -> Optional[dict]:
        self._ensure()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM v3_turns WHERE turn_id=?", (turn_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # -- activity outbox -------------------------------------------------- #
    def record_activity(
        self, *, event_id: str, session_id: str, student_id: str,
        event_type: str, grade: int, mode: str, lesson_id: str,
        created_at: str, summary_json: str, ai_note: str = "",
    ) -> None:
        """Append a privacy-safe activity event. Rejects any event type outside
        the approved set, and stores only the structured summary — never a raw
        transcript."""
        if event_type not in ALLOWED_OUTBOX_EVENTS:
            raise ValueError(
                f"event_type {event_type!r} not allowed in the activity outbox "
                f"(allowed: {sorted(ALLOWED_OUTBOX_EVENTS)})")
        self._ensure()
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO v3_activity_outbox "
                    "(event_id, session_id, student_id, event_type, grade, mode, "
                    " lesson_id, created_at, summary_json, ai_note) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (event_id, session_id, student_id, event_type, grade, mode,
                     lesson_id, created_at, summary_json, ai_note))
        finally:
            conn.close()

    def list_activity(self, session_id: str) -> list[dict]:
        self._ensure()
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM v3_activity_outbox WHERE session_id=? "
                "ORDER BY created_at", (session_id,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # -- Sheets delivery outbox (durable, crash-safe) --------------------- #
    #
    # Distinct from ``v3_activity_outbox`` above: that one is a privacy-scoped
    # student-activity feed (image_upload/sos/test_completed/session_end only).
    # This one is Sheets-transcript delivery telemetry for EVERY visible V3
    # turn — same privacy shape ``matbot.sheets_log`` already trusts (payload +
    # adapter response dicts; never a raw system/user model prompt, which
    # neither dict ever contains).
    def enqueue_sheets_event(
        self, *, client_turn_id: str, session_id: str, payload_json: str,
        response_json: str, created_at: str,
    ) -> None:
        """Durable, LOCAL-ONLY write (no network) — this is what lets the
        student response return without ever waiting on Google Sheets.
        ``client_turn_id`` is the primary key: a duplicate enqueue (e.g. a
        defensive double-call, or a retried request already handled by turn-
        level idempotency upstream) is a no-op, never a second row.
        Immediately eligible for claim: ``next_attempt_at = created_at``."""
        self._ensure()
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO v3_sheets_outbox "
                    "(client_turn_id, session_id, payload_json, response_json, "
                    " created_at, status, next_attempt_at) "
                    "VALUES (?,?,?,?,?,'pending',?)",
                    (client_turn_id, session_id, payload_json, response_json,
                     created_at, created_at))
        finally:
            conn.close()

    def claim_sheets_events(
        self, *, limit: int, claimed_by: str, now: str, lease_seconds: float,
    ) -> list[dict]:
        """Atomically claim up to ``limit`` deliverable rows for THIS caller.

        A row is claimable when it is ``pending`` (or an ``in_progress`` claim
        has exceeded ``lease_seconds`` — a crashed/killed drainer's claim
        eventually becomes retryable) AND ``next_attempt_at <= now`` (the
        backoff window has elapsed).

        Safe under concurrent callers (multiple threads in one process, or
        multiple Gunicorn worker processes sharing the same SQLite file):
        candidates are read, then EACH is claimed with its own single-row
        ``UPDATE ... WHERE client_turn_id=? AND <still claimable>`` and the
        claim only counts if ``cur.rowcount == 1`` — SQLite serializes writes,
        so at most one caller ever wins a given row, exactly like the
        existing optimistic-version check in ``commit_turn``.
        """
        self._ensure()
        conn = self._connect()
        claimed: list[dict] = []
        try:
            candidates = conn.execute(
                "SELECT client_turn_id, claimed_at FROM v3_sheets_outbox "
                "WHERE next_attempt_at <= ? "
                "AND (status='pending' OR (status='in_progress' AND claimed_at <= ?)) "
                "ORDER BY created_at LIMIT ?",
                (now, _lease_cutoff(now, lease_seconds), max(0, int(limit)),
                 ),
            ).fetchall()
            for cand in candidates:
                with conn:
                    cur = conn.execute(
                        "UPDATE v3_sheets_outbox SET status='in_progress', "
                        "claimed_at=?, claimed_by=? "
                        "WHERE client_turn_id=? AND next_attempt_at <= ? "
                        "AND (status='pending' OR "
                        "     (status='in_progress' AND claimed_at <= ?))",
                        (now, claimed_by, cand["client_turn_id"], now,
                         _lease_cutoff(now, lease_seconds)))
                    if cur.rowcount == 1:
                        row = conn.execute(
                            "SELECT * FROM v3_sheets_outbox WHERE client_turn_id=?",
                            (cand["client_turn_id"],)).fetchone()
                        if row is not None:
                            claimed.append(dict(row))
            return claimed
        finally:
            conn.close()

    def mark_sheets_event_delivered(self, client_turn_id: str, delivered_at: str) -> None:
        """Terminal: a delivered row is never reclaimed or retried again."""
        self._ensure()
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "UPDATE v3_sheets_outbox SET status='delivered', "
                    "delivered_at=? WHERE client_turn_id=?",
                    (delivered_at, client_turn_id))
        finally:
            conn.close()

    def mark_sheets_event_failed(
        self, client_turn_id: str, error: str, *, next_attempt_at: str,
    ) -> None:
        """Back to ``pending`` (never stuck ``in_progress``) with
        ``attempts`` incremented and a bounded ``next_attempt_at`` backoff —
        durable and retryable, never a tight retry loop."""
        self._ensure()
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "UPDATE v3_sheets_outbox SET status='pending', "
                    "attempts=attempts+1, last_error=?, next_attempt_at=? "
                    "WHERE client_turn_id=?",
                    (error, next_attempt_at, client_turn_id))
        finally:
            conn.close()

    def get_sheets_event(self, client_turn_id: str) -> Optional[dict]:
        """Diagnostic/test lookup — the row regardless of status, or None."""
        self._ensure()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM v3_sheets_outbox WHERE client_turn_id=?",
                (client_turn_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def count_sheets_events(self, *, status: Optional[str] = None) -> int:
        """Diagnostic/test count, optionally filtered by ``status``."""
        self._ensure()
        conn = self._connect()
        try:
            if status is None:
                row = conn.execute("SELECT COUNT(*) FROM v3_sheets_outbox").fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM v3_sheets_outbox WHERE status=?",
                    (status,)).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()

    # -- introspection (tests / diagnostics) ------------------------------ #
    def pragma(self, name: str):
        conn = self._connect()
        try:
            row = conn.execute(f"PRAGMA {name}").fetchone()
            return row[0] if row else None
        finally:
            conn.close()


def now_iso() -> str:
    """UTC ISO-8601 timestamp for created/updated columns."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def monotonic_ms() -> float:
    return time.monotonic() * 1000.0


def _lease_cutoff(now: str, lease_seconds: float) -> str:
    """``now`` minus ``lease_seconds``, as an ISO-8601 string — a stale
    ``in_progress`` claim's ``claimed_at`` at or before this cutoff is treated
    as abandoned (the drainer that claimed it crashed or hung) and becomes
    reclaimable. ISO-8601 strings compare lexicographically the same as
    chronologically, so this stays a plain string comparison in SQL."""
    from datetime import datetime, timedelta

    dt = datetime.fromisoformat(now)
    return (dt - timedelta(seconds=max(0.0, float(lease_seconds)))).isoformat()


def backoff_seconds(attempts: int, *, base: float = 5.0, cap: float = 300.0) -> float:
    """Bounded exponential backoff: 5s, 10s, 20s, 40s, ... capped at 5 minutes.
    ``attempts`` is the count AFTER the failure being backed off from (i.e.
    the first failure uses ``attempts=1``)."""
    return min(cap, base * (2 ** max(0, attempts - 1)))


def next_attempt_at(now: str, attempts: int) -> str:
    """``now`` plus the bounded backoff for ``attempts`` failures, as ISO-8601."""
    from datetime import datetime, timedelta

    dt = datetime.fromisoformat(now)
    return (dt + timedelta(seconds=backoff_seconds(attempts))).isoformat()
