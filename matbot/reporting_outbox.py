"""Durable local queue for MAT-BOT reporting facts.

The request path writes a small, structured JSON envelope to SQLite/WAL before
returning.  A daemon worker then delivers it to Turso.  The local row is the
durability boundary: process/container restarts may delay delivery, but cannot
turn an accepted event into an in-memory-only event.

This database never stores prompts, student messages, tutor answers, images or
raw external identities.  Unattributed rows use a keyed subject identifier and
remain outside the official delivery queue until an approved student mapping
exists.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time


logger = logging.getLogger("matbot.reporting_outbox")

SCHEMA_VERSION = 1
PAYLOAD_VERSION = 1
STATE_PENDING = "pending"
STATE_IN_FLIGHT = "in_flight"
STATE_DELIVERED = "delivered"
STATE_FAILED = "failed"
_SAFE_CODE = re.compile(r"[^A-Za-z0-9_.:-]")
_TIMESTAMP = re.compile(r"\A\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\Z")
_EVENT_METADATA_FIELDS = frozenset((
    "difficulty", "difficulty_level", "hint_level", "question_count",
))
_ATTEMPT_FIELDS = frozenset((
    "assessment_type", "external_attempt_id", "grade", "area_name",
    "total_count", "score_percent", "correct_count", "started_at",
    "completed_at",
))
_ITEM_FIELDS = frozenset((
    "item_key", "ordinal", "lesson_id", "lesson_name", "difficulty",
    "area_name", "is_correct",
))


class OutboxError(RuntimeError):
    """A local durability operation failed; the code never contains row data."""

    def __init__(self, code, cause=None):
        super().__init__(code)
        self.code = code
        self.cause = cause


@dataclass(frozen=True)
class DeliveryResult:
    ok: bool
    code: str = ""
    retryable: bool = True


@dataclass(frozen=True)
class EnqueueResult:
    accepted: bool
    duplicate: bool
    delivery_key: str


def _safe_code(value, fallback="unknown"):
    cleaned = _SAFE_CODE.sub("_", str(value or ""))[:120]
    return cleaned or fallback


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _event_dict(event):
    raw_metadata = getattr(event, "metadata", None)
    metadata = ({key: raw_metadata[key] for key in _EVENT_METADATA_FIELDS
                 if key in raw_metadata}
                if isinstance(raw_metadata, dict) else {})
    return {
        "event_type": str(getattr(event, "event_type", "")),
        "event_key": str(getattr(event, "event_key", "")),
        "mode": str(getattr(event, "mode", "") or ""),
        "grade": getattr(event, "grade", None),
        "area_name": str(getattr(event, "area_name", "") or ""),
        "lesson_id": str(getattr(event, "lesson_id", "") or ""),
        "lesson_name": str(getattr(event, "lesson_name", "") or ""),
        "metadata": metadata if isinstance(metadata, dict) else {},
        "occurred_at": getattr(event, "occurred_at", None),
    }


def _assessment_dict(assessment):
    if not isinstance(assessment, dict):
        return None
    attempt = assessment.get("attempt")
    if not isinstance(attempt, dict):
        return None
    result = {
        "kind": assessment.get("kind"),
        "attempt": {key: attempt[key] for key in _ATTEMPT_FIELDS
                    if key in attempt},
    }
    items = assessment.get("items")
    if isinstance(items, (list, tuple)):
        result["items"] = [
            {key: item[key] for key in _ITEM_FIELDS if key in item}
            for item in items if isinstance(item, dict)
        ]
    return result


def turn_payload(events=None, assessment=None):
    """Serialize the already validated reporting facts, never tutor content."""
    return {
        "v": PAYLOAD_VERSION,
        "events": [_event_dict(event) for event in list(events or ())],
        "assessment": _assessment_dict(assessment),
    }


def _timestamps(payload):
    found = []
    for event in payload.get("events") or ():
        value = event.get("occurred_at") if isinstance(event, dict) else None
        if isinstance(value, str) and _TIMESTAMP.match(value):
            found.append(value)
    assessment = payload.get("assessment")
    attempt = assessment.get("attempt") if isinstance(assessment, dict) else None
    if isinstance(attempt, dict):
        for name in ("started_at", "completed_at"):
            value = attempt.get(name)
            if isinstance(value, str) and _TIMESTAMP.match(value):
                found.append(value)
    return (min(found), max(found)) if found else (None, None)


def _delivery_key(owner, payload):
    material = (str(owner) + "\x00" + _canonical_json(payload)).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


class ReportingOutbox:
    """One SQLite/WAL outbox plus a single bounded-retry delivery worker."""

    def __init__(self, path, deliver=None, *, poll_seconds=1.0,
                 retry_base_seconds=1.0, retry_max_seconds=300.0,
                 clock=time.time, recover_in_flight=True):
        self.path = os.path.abspath(str(path))
        self._deliver = deliver
        self._poll_seconds = max(0.01, float(poll_seconds))
        self._retry_base = max(0.01, float(retry_base_seconds))
        self._retry_max = max(self._retry_base, float(retry_max_seconds))
        self._clock = clock
        self._recover_in_flight = bool(recover_in_flight)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._initialize()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=2.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=2000")
        return conn

    def _initialize(self):
        try:
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with self._connect() as conn:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS outbox_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS reporting_outbox (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        delivery_key TEXT NOT NULL UNIQUE,
                        student_id INTEGER NOT NULL,
                        payload_json TEXT,
                        state TEXT NOT NULL
                            CHECK (state IN ('pending','in_flight','delivered','failed')),
                        attempts INTEGER NOT NULL DEFAULT 0,
                        next_attempt_at REAL NOT NULL,
                        occurred_min TEXT,
                        occurred_max TEXT,
                        last_error_code TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        delivered_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_reporting_outbox_due
                        ON reporting_outbox (state, next_attempt_at, id);
                    CREATE INDEX IF NOT EXISTS idx_reporting_outbox_report
                        ON reporting_outbox (student_id, state, occurred_min, occurred_max);
                    CREATE TABLE IF NOT EXISTS unattributed_activity (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        subject_key TEXT NOT NULL,
                        delivery_key TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        occurred_min TEXT,
                        occurred_max TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (subject_key, delivery_key)
                    );
                    CREATE INDEX IF NOT EXISTS idx_unattributed_subject
                        ON unattributed_activity (subject_key, id);
                    CREATE INDEX IF NOT EXISTS idx_unattributed_time
                        ON unattributed_activity (occurred_min, occurred_max);
                    CREATE TABLE IF NOT EXISTS approved_subjects (
                        subject_key TEXT PRIMARY KEY,
                        student_id INTEGER NOT NULL,
                        approved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                row = conn.execute(
                    "SELECT value FROM outbox_meta WHERE key = 'schema_version'"
                ).fetchone()
                if row is None:
                    conn.execute(
                        "INSERT INTO outbox_meta (key, value) VALUES ('schema_version', ?)",
                        (str(SCHEMA_VERSION),))
                elif row[0] != str(SCHEMA_VERSION):
                    raise OutboxError("outbox_schema_version_mismatch")
                integrity = conn.execute("PRAGMA quick_check").fetchall()
                if integrity != [("ok",)]:
                    raise OutboxError("outbox_integrity_check_failed")
                # A crash can happen after claiming but before acknowledging.
                recovered = 0
                if self._recover_in_flight:
                    recovered = conn.execute(
                        "UPDATE reporting_outbox SET state = 'pending', "
                        "next_attempt_at = ?, updated_at = CURRENT_TIMESTAMP "
                        "WHERE state = 'in_flight'", (self._clock(),)).rowcount
            if recovered:
                logger.info("reporting_outbox_recovered count=%s", recovered)
        except OutboxError:
            raise
        except Exception as exc:
            raise OutboxError(
                "outbox_initialize_failed:" + type(exc).__name__, exc) from None

    def start(self):
        if self._deliver is None:
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="matbot-reporting-outbox", daemon=True)
            self._thread.start()

    def close(self, timeout=3.0):
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, float(timeout)))

    def enqueue(self, student_id, events=None, assessment=None):
        payload = turn_payload(events, assessment)
        if assessment is not None and payload["assessment"] is None:
            raise OutboxError("outbox_assessment_invalid")
        if not payload["events"] and not payload["assessment"]:
            raise OutboxError("outbox_empty_turn")
        try:
            student_id = int(student_id)
            if student_id <= 0:
                raise ValueError
        except (TypeError, ValueError):
            raise OutboxError("outbox_student_invalid") from None
        encoded = _canonical_json(payload)
        key = _delivery_key("student:%d" % student_id, payload)
        occurred_min, occurred_max = _timestamps(payload)
        try:
            with self._lock, self._connect() as conn:
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO reporting_outbox "
                    "(delivery_key, student_id, payload_json, state, "
                    " next_attempt_at, occurred_min, occurred_max) "
                    "VALUES (?, ?, ?, 'pending', ?, ?, ?)",
                    (key, student_id, encoded, self._clock(),
                     occurred_min, occurred_max))
            duplicate = cursor.rowcount == 0
        except Exception as exc:
            raise OutboxError(
                "outbox_enqueue_failed:" + type(exc).__name__, exc) from None
        self.start()
        self._wake.set()
        return EnqueueResult(True, duplicate, key)

    def enqueue_unattributed(self, subject_key, events=None, assessment=None):
        subject = str(subject_key or "")
        if (not subject.startswith("v1:") or len(subject) != 67
                or not all(char in "0123456789abcdef" for char in subject[3:])):
            raise OutboxError("outbox_subject_invalid")
        payload = turn_payload(events, assessment)
        if assessment is not None and payload["assessment"] is None:
            raise OutboxError("outbox_assessment_invalid")
        if not payload["events"] and not payload["assessment"]:
            raise OutboxError("outbox_empty_turn")
        encoded = _canonical_json(payload)
        key = _delivery_key("subject:" + subject, payload)
        occurred_min, occurred_max = _timestamps(payload)
        try:
            with self._lock, self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                approved = conn.execute(
                    "SELECT student_id FROM approved_subjects "
                    "WHERE subject_key = ?", (subject,)).fetchone()
                if approved:
                    student_id = int(approved[0])
                    key = _delivery_key("student:%d" % student_id, payload)
                    cursor = conn.execute(
                        "INSERT OR IGNORE INTO reporting_outbox "
                        "(delivery_key, student_id, payload_json, state, "
                        " next_attempt_at, occurred_min, occurred_max) "
                        "VALUES (?, ?, ?, 'pending', ?, ?, ?)",
                        (key, student_id, encoded, self._clock(),
                         occurred_min, occurred_max))
                else:
                    cursor = conn.execute(
                        "INSERT OR IGNORE INTO unattributed_activity "
                        "(subject_key, delivery_key, payload_json, "
                        " occurred_min, occurred_max) VALUES (?, ?, ?, ?, ?)",
                        (subject, key, encoded, occurred_min, occurred_max))
            duplicate = cursor.rowcount == 0
        except Exception as exc:
            raise OutboxError(
                "unattributed_enqueue_failed:" + type(exc).__name__, exc) from None
        if approved:
            self.start()
            self._wake.set()
        return EnqueueResult(True, duplicate, key)

    def claim_unattributed(self, subject_key, student_id, limit=250):
        """Atomically move explicitly linked subject events into the outbox."""
        subject = str(subject_key or "")
        if (not subject.startswith("v1:") or len(subject) != 67
                or not all(char in "0123456789abcdef" for char in subject[3:])):
            raise OutboxError("outbox_subject_invalid")
        try:
            student_id = int(student_id)
            if student_id <= 0:
                raise ValueError
        except (TypeError, ValueError):
            raise OutboxError("outbox_student_invalid") from None
        moved = 0
        try:
            with self._lock, self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                approved = conn.execute(
                    "SELECT student_id FROM approved_subjects "
                    "WHERE subject_key = ?", (subject,)).fetchone()
                if approved and int(approved[0]) != student_id:
                    raise OutboxError("outbox_subject_owner_conflict")
                conn.execute(
                    "INSERT OR IGNORE INTO approved_subjects "
                    "(subject_key, student_id) VALUES (?, ?)",
                    (subject, student_id))
                rows = conn.execute(
                    "SELECT id, payload_json, occurred_min, occurred_max "
                    "FROM unattributed_activity WHERE subject_key = ? "
                    "ORDER BY id LIMIT ?", (subject, max(1, int(limit)))).fetchall()
                for row_id, encoded, occurred_min, occurred_max in rows:
                    payload = json.loads(encoded)
                    key = _delivery_key("student:%d" % student_id, payload)
                    conn.execute(
                        "INSERT OR IGNORE INTO reporting_outbox "
                        "(delivery_key, student_id, payload_json, state, "
                        " next_attempt_at, occurred_min, occurred_max) "
                        "VALUES (?, ?, ?, 'pending', ?, ?, ?)",
                        (key, student_id, encoded, self._clock(),
                         occurred_min, occurred_max))
                    conn.execute(
                        "DELETE FROM unattributed_activity WHERE id = ?",
                        (row_id,))
                    moved += 1
        except OutboxError:
            raise
        except Exception as exc:
            raise OutboxError(
                "unattributed_claim_failed:" + type(exc).__name__, exc) from None
        if moved:
            self.start()
            self._wake.set()
            logger.info("unattributed_activity_claimed count=%s", moved)
        return moved

    def _claim_due(self):
        now = self._clock()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # There is exactly one delivery worker per configured process. If
            # it is asking for another row, any surviving in_flight row came
            # from a failed local acknowledgement and is safe to replay: the
            # remote event/assessment keys are idempotent.
            conn.execute(
                "UPDATE reporting_outbox SET state = 'pending', "
                "next_attempt_at = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE state = 'in_flight'", (now,))
            row = conn.execute(
                "SELECT id, student_id, payload_json FROM reporting_outbox "
                "WHERE state = 'pending' AND next_attempt_at <= ? "
                "ORDER BY next_attempt_at, id LIMIT 1", (now,)).fetchone()
            if row is None:
                return None
            changed = conn.execute(
                "UPDATE reporting_outbox SET state = 'in_flight', "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND state = 'pending'", (row[0],)).rowcount
            return row if changed == 1 else None

    def _complete(self, row_id):
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE reporting_outbox SET state = 'delivered', "
                "payload_json = NULL, last_error_code = NULL, "
                "delivered_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND state = 'in_flight'", (row_id,))

    def _fail(self, row_id, result):
        code = _safe_code(result.code, "delivery_failed")
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT attempts FROM reporting_outbox WHERE id = ?",
                (row_id,)).fetchone()
            attempts = int(row[0] if row else 0) + 1
            if result.retryable:
                exponent = min(attempts - 1, 30)
                delay = min(self._retry_max, self._retry_base * (2 ** exponent))
                conn.execute(
                    "UPDATE reporting_outbox SET state = 'pending', attempts = ?, "
                    "next_attempt_at = ?, last_error_code = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (attempts, self._clock() + delay, code, row_id))
                logger.info(
                    "reporting_persistence status=retrying code=%s attempts=%s "
                    "retry_in_s=%s", code, attempts, round(delay, 3))
            else:
                conn.execute(
                    "UPDATE reporting_outbox SET state = 'failed', attempts = ?, "
                    "last_error_code = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ?", (attempts, code, row_id))
                logger.error(
                    "reporting_persistence status=failed code=%s attempts=%s",
                    code, attempts)

    def process_one(self):
        row = self._claim_due()
        if row is None:
            return False
        row_id, student_id, encoded = row
        try:
            payload = json.loads(encoded)
            if not isinstance(payload, dict):
                raise ValueError("payload_not_object")
            payload["student_id"] = int(student_id)
            result = self._deliver(payload)
            if not isinstance(result, DeliveryResult):
                result = DeliveryResult(False, "delivery_contract_invalid", False)
        except (TypeError, ValueError):
            result = DeliveryResult(False, "outbox_payload_invalid", False)
        except Exception as exc:
            result = DeliveryResult(
                False, "delivery_unexpected:" + type(exc).__name__, True)
        if result.ok:
            self._complete(row_id)
            logger.info("reporting_persistence status=success")
        else:
            self._fail(row_id, result)
        return True

    def _run(self):
        while not self._stop.is_set():
            try:
                if self.process_one():
                    continue
            except Exception as exc:
                logger.error("reporting_outbox_worker_failed code=unexpected:%s",
                             type(exc).__name__)
            self._wake.wait(self._poll_seconds)
            self._wake.clear()

    def wait_until_idle(self, timeout=5.0):
        deadline = time.monotonic() + max(0.0, float(timeout))
        while time.monotonic() < deadline:
            state = self.health()
            if state["pending"] == 0 and state["in_flight"] == 0:
                return True
            self._wake.set()
            time.sleep(0.01)
        return False

    def report_state(self, student_id, month_start, next_month_start):
        """Pending/failed attributed rows that can make one report incomplete."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT "
                "SUM(CASE WHEN state IN ('pending','in_flight') THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN state = 'failed' THEN 1 ELSE 0 END), "
                "MAX(id) "
                "FROM reporting_outbox WHERE student_id = ? "
                "AND (occurred_min IS NULL OR occurred_min < ?) "
                "AND (occurred_max IS NULL OR occurred_max >= ?)",
                (int(student_id), next_month_start, month_start)).fetchone()
        return {"pending": int((row or (0, 0, 0))[0] or 0),
                "failed": int((row or (0, 0, 0))[1] or 0),
                "version": int((row or (0, 0, 0))[2] or 0)}

    def verify_writable(self):
        """Prove a main-database write can commit, retaining no probe data.

        Opening SQLite and reading the schema is not enough: an existing WAL
        database can still be readable when its directory no longer permits the
        writes needed by a tutor request. The deployment preflight uses this
        rolled-back transaction before it replaces the live container.
        """
        try:
            with self._lock:
                conn = self._connect()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute(
                        "INSERT OR REPLACE INTO outbox_meta (key, value) "
                        "VALUES ('__write_probe__', '1')")
                    conn.rollback()
                finally:
                    conn.close()
        except Exception as exc:
            raise OutboxError(
                "outbox_write_check_failed:" + type(exc).__name__, exc) from None
        return True

    def health(self):
        try:
            with self._lock, self._connect() as conn:
                rows = dict(conn.execute(
                    "SELECT state, COUNT(*) FROM reporting_outbox GROUP BY state"
                ).fetchall())
                retrying = conn.execute(
                    "SELECT COUNT(*) FROM reporting_outbox "
                    "WHERE state = 'pending' AND attempts > 0").fetchone()[0]
                unattributed = conn.execute(
                    "SELECT COUNT(*) FROM unattributed_activity").fetchone()[0]
                oldest = conn.execute(
                    "SELECT MIN(created_at) FROM reporting_outbox "
                    "WHERE state IN ('pending','in_flight','failed')").fetchone()[0]
                oldest_unattributed = conn.execute(
                    "SELECT MIN(created_at) FROM unattributed_activity").fetchone()[0]
                error_codes = dict(conn.execute(
                    "SELECT last_error_code, COUNT(*) FROM reporting_outbox "
                    "WHERE last_error_code IS NOT NULL GROUP BY last_error_code"
                ).fetchall())
            return {
                "configured": True,
                "writable": True,
                "schema_version": SCHEMA_VERSION,
                "pending": int(rows.get(STATE_PENDING, 0)),
                "in_flight": int(rows.get(STATE_IN_FLIGHT, 0)),
                "delivered": int(rows.get(STATE_DELIVERED, 0)),
                "failed": int(rows.get(STATE_FAILED, 0)),
                "retrying": int(retrying or 0),
                "unattributed": int(unattributed or 0),
                "oldest_unfinished_at": oldest,
                "oldest_unattributed_at": oldest_unattributed,
                "error_codes": error_codes,
                "file_bytes": sum(
                    os.path.getsize(candidate) for candidate in (
                        self.path, self.path + "-wal", self.path + "-shm")
                    if os.path.exists(candidate)),
            }
        except Exception as exc:
            return {"configured": True, "writable": False,
                    "error": "outbox_health_failed:" + type(exc).__name__}


def main(argv=None):
    from matbot import config

    parser = argparse.ArgumentParser(
        description="Privacy-safe MAT-BOT reporting outbox diagnostics.")
    parser.add_argument("--check", action="store_true",
                        help="return non-zero when the outbox is unavailable or failed")
    args = parser.parse_args(argv)
    path = config.reporting_outbox_path()
    if not path:
        print(json.dumps({"configured": False}, sort_keys=True))
        return 1 if args.check else 0
    try:
        outbox = ReportingOutbox(path, recover_in_flight=False)
        if args.check:
            outbox.verify_writable()
        report = outbox.health()
    except OutboxError as exc:
        report = {"configured": True, "writable": False, "error": exc.code}
    print(json.dumps(report, sort_keys=True))
    if args.check and (not report.get("writable") or report.get("failed")):
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
