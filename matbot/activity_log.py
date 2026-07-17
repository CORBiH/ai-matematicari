"""Phase 5 — minimalni activity log za modularni AI tutor (SQLite).

Loguje SAMO metapodatke o tutor interakcijama (za kasniji parent report):
nikad puni tekst učenikove poruke, AI odgovora, sadržaj slike niti tajne.

DB putanja: env ``MATBOT_DB_PATH`` ako je postavljen, inače
``storage/matbot.sqlite3`` u repo root-u (folder se kreira automatski).

Pravilo otpornosti: logovanje NIKAD ne smije srušiti tutor odgovor —
``log_student_activity`` hvata sve izuzetke i vraća ``False``.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("matbot.activity")

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = _REPO_ROOT / "storage" / "matbot.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS student_activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NULL,
    session_id TEXT NULL,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    grade INTEGER NULL,
    entry_source TEXT NULL,
    course_name TEXT NULL,
    section_name TEXT NULL,
    lesson_title TEXT NULL,
    final_topic TEXT NULL,
    mode TEXT NULL,
    status TEXT NULL,
    parent_report_signal TEXT NULL,
    mistake_tag TEXT NULL,
    recommendation TEXT NULL,
    topic_conflict INTEGER DEFAULT 0,
    task_id TEXT NULL,
    task_status TEXT NULL,
    attempt_number INTEGER NULL,
    total_attempt_count INTEGER NULL,
    wrong_attempt_count INTEGER NULL,
    hint_count INTEGER NULL,
    parent_task_id TEXT NULL,
    followup_task_id TEXT NULL,
    task_origin TEXT NULL,
    hint_level INTEGER NULL,
    highest_hint_level INTEGER NULL,
    hint_reason TEXT NULL,
    solution_revealed INTEGER DEFAULT 0,
    solved_independently INTEGER DEFAULT 0,
    solved_with_hints INTEGER DEFAULT 0,
    completed_parent_task TEXT NULL
)
"""

_FEEDBACK_SCHEMA = """
CREATE TABLE IF NOT EXISTS tutor_feedback_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    message_index INTEGER NOT NULL,
    verdict TEXT NOT NULL,
    mode TEXT NULL,
    topic TEXT NULL
)
"""

_COLUMNS = (
    "student_id", "session_id", "timestamp", "event_type", "grade", "entry_source",
    "course_name", "section_name", "lesson_title", "final_topic", "mode",
    "status", "parent_report_signal", "mistake_tag", "recommendation",
    "topic_conflict", "task_id", "task_status", "attempt_number",
    "total_attempt_count", "wrong_attempt_count", "hint_count", "parent_task_id",
    "followup_task_id", "task_origin", "hint_level", "highest_hint_level",
    "hint_reason", "solution_revealed", "solved_independently",
    "solved_with_hints", "completed_parent_task",
)


def resolve_db_path(path: str | Path | None = None) -> Path:
    """Eksplicitna putanja > env MATBOT_DB_PATH > default storage/matbot.sqlite3."""
    if path is not None:
        return Path(path)
    env = (os.getenv("MATBOT_DB_PATH") or "").strip()
    return Path(env) if env else DEFAULT_DB_PATH


_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_activity_session_ts "
    "ON student_activity_log (session_id, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_activity_student_ts "
    "ON student_activity_log (student_id, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_feedback_session_ts "
    "ON tutor_feedback_log (session_id, timestamp)",
)

_MIGRATIONS = (
    ("grade", "ALTER TABLE student_activity_log ADD COLUMN grade INTEGER NULL"),
    ("task_id", "ALTER TABLE student_activity_log ADD COLUMN task_id TEXT NULL"),
    ("task_status", "ALTER TABLE student_activity_log ADD COLUMN task_status TEXT NULL"),
    ("attempt_number", "ALTER TABLE student_activity_log ADD COLUMN attempt_number INTEGER NULL"),
    ("total_attempt_count", "ALTER TABLE student_activity_log ADD COLUMN total_attempt_count INTEGER NULL"),
    ("wrong_attempt_count", "ALTER TABLE student_activity_log ADD COLUMN wrong_attempt_count INTEGER NULL"),
    ("hint_count", "ALTER TABLE student_activity_log ADD COLUMN hint_count INTEGER NULL"),
    ("parent_task_id", "ALTER TABLE student_activity_log ADD COLUMN parent_task_id TEXT NULL"),
    ("followup_task_id", "ALTER TABLE student_activity_log ADD COLUMN followup_task_id TEXT NULL"),
    ("task_origin", "ALTER TABLE student_activity_log ADD COLUMN task_origin TEXT NULL"),
    ("hint_level", "ALTER TABLE student_activity_log ADD COLUMN hint_level INTEGER NULL"),
    ("highest_hint_level", "ALTER TABLE student_activity_log ADD COLUMN highest_hint_level INTEGER NULL"),
    ("hint_reason", "ALTER TABLE student_activity_log ADD COLUMN hint_reason TEXT NULL"),
    ("solution_revealed", "ALTER TABLE student_activity_log ADD COLUMN solution_revealed INTEGER DEFAULT 0"),
    ("solved_independently", "ALTER TABLE student_activity_log ADD COLUMN solved_independently INTEGER DEFAULT 0"),
    ("solved_with_hints", "ALTER TABLE student_activity_log ADD COLUMN solved_with_hints INTEGER DEFAULT 0"),
    ("completed_parent_task", "ALTER TABLE student_activity_log ADD COLUMN completed_parent_task TEXT NULL"),
)


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5)
    conn.row_factory = sqlite3.Row
    # Phase 6.1: WAL + busy_timeout — sigurnije za istovremene upise (gunicorn threadovi)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
    except sqlite3.Error:
        pass
    return conn


def init_db(path: str | Path | None = None) -> Path:
    """Kreiraj DB fajl + tabelu + indekse ako ne postoje; vrati putanju."""
    p = resolve_db_path(path)
    conn = _connect(p)
    try:
        with conn:
            conn.execute(_SCHEMA)
            conn.execute(_FEEDBACK_SCHEMA)
            existing = {
                r[1] for r in conn.execute("PRAGMA table_info(student_activity_log)")
            }
            for col, sql in _MIGRATIONS:
                if col not in existing:
                    conn.execute(sql)
            for idx in _INDEXES:
                conn.execute(idx)
    finally:
        conn.close()
    return p


def _clean(val: Any) -> str | None:
    s = str(val).strip() if val is not None else ""
    return s or None


def _clean_grade(val: Any) -> int | None:
    s = _clean(val)
    if not s:
        return None
    match = re.search(r"\d+", s)
    return int(match.group(0)) if match else None


def _clean_int(val: Any) -> int | None:
    if val in (None, ""):
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _clean_bool_int(val: Any) -> int:
    return 1 if bool(val) else 0


def _json_meta(val: Any) -> str | None:
    if val in (None, "", [], {}):
        return None
    try:
        return json.dumps(val, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(val)


def classify_event_type(payload: dict, response: dict) -> str:
    """event_type po prioritetu (handoff §9):
    practice_answer > exam_mode_used > topic_selected > ai_message."""
    payload = payload or {}
    response = response or {}
    phase = str(payload.get("interaction_phase") or "").strip().lower()
    if phase == "answering_practice_task":
        return "practice_answer"
    mode = str(response.get("mode") or payload.get("mode") or "").strip().lower()
    if mode == "exam":
        return "exam_mode_used"
    entry = str(payload.get("entry_source") or "").strip().lower()
    if entry == "manual_topic_choice":
        return "topic_selected"
    return "ai_message"


def log_student_activity(
    payload: dict, response: dict, path: str | Path | None = None
) -> bool:
    """Upiši JEDAN red metapodataka. Nikad ne baca izuzetak (greška → False).

    Namjerno NE upisuje: student_message, AI answer, slike, tajne."""
    try:
        payload = payload or {}
        response = response or {}
        next_state = response.get("next_state") if isinstance(response.get("next_state"), dict) else {}

        def _telemetry(key: str) -> Any:
            value = response.get(key)
            if value is None and isinstance(next_state, dict):
                value = next_state.get(key)
            return value

        row = {
            "student_id": _clean(payload.get("student_id")),
            "session_id": _clean(payload.get("session_id")),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": classify_event_type(payload, response),
            "grade": _clean_grade(payload.get("grade")),
            "entry_source": _clean(
                response.get("entry_source_used") or payload.get("entry_source")
            ),
            "course_name": _clean(payload.get("course_name")),
            "section_name": _clean(payload.get("section_name")),
            "lesson_title": _clean(payload.get("lesson_title")),
            "final_topic": _clean(response.get("final_topic")),
            "mode": _clean(response.get("mode")),
            "status": _clean(response.get("status")),
            "parent_report_signal": _clean(response.get("parent_report_signal")),
            "mistake_tag": None,       # rezervisano za kasnije faze
            "recommendation": None,    # rezervisano za kasnije faze
            "topic_conflict": 1 if response.get("topic_conflict") else 0,
            "task_id": _clean(
                response.get("task_id")
                or next_state.get("task_id")
                or next_state.get("completed_task_id")
            ),
            "task_status": _clean(response.get("task_status") or next_state.get("task_status")),
            "attempt_number": _clean_int(
                _telemetry("attempt_number")
                if _telemetry("attempt_number") is not None
                else _telemetry("attempt_count")
            ),
            "total_attempt_count": _clean_int(
                _telemetry("total_attempt_count")
                if _telemetry("total_attempt_count") is not None
                else _telemetry("attempt_count")
            ),
            "wrong_attempt_count": _clean_int(_telemetry("wrong_attempt_count")),
            "hint_count": _clean_int(_telemetry("hint_count")),
            "parent_task_id": _clean(_telemetry("parent_task_id")),
            "followup_task_id": _clean(_telemetry("followup_task_id")),
            "task_origin": _clean(_telemetry("task_origin")),
            "hint_level": _clean_int(_telemetry("hint_level")),
            "highest_hint_level": _clean_int(_telemetry("highest_hint_level")),
            "hint_reason": _clean(_telemetry("hint_reason")),
            "solution_revealed": _clean_bool_int(_telemetry("solution_revealed")),
            "solved_independently": _clean_bool_int(_telemetry("solved_independently")),
            "solved_with_hints": _clean_bool_int(_telemetry("solved_with_hints")),
            "completed_parent_task": _json_meta(_telemetry("completed_parent_task")),
        }
        p = init_db(path)
        conn = _connect(p)
        try:
            with conn:
                conn.execute(
                    "INSERT INTO student_activity_log "
                    f"({', '.join(_COLUMNS)}) VALUES ({', '.join('?' * len(_COLUMNS))})",
                    tuple(row[c] for c in _COLUMNS),
                )
        finally:
            conn.close()
        return True
    except Exception:
        log.exception("activity log: upis nije uspio — tutor odgovor se ne prekida")
        return False


def log_tutor_feedback(
    payload: dict, path: str | Path | None = None
) -> bool:
    """Upiši thumbs-up/down metapodatke za bot poruku. Bez teksta poruke."""
    try:
        payload = payload or {}
        row = {
            "session_id": _clean(payload.get("session_id")) or "",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message_index": int(payload.get("message_index")),
            "verdict": _clean(payload.get("verdict")) or "",
            "mode": _clean(payload.get("mode")),
            "topic": _clean(payload.get("topic")),
        }
        if not row["session_id"] or row["verdict"] not in ("up", "down"):
            return False
        p = init_db(path)
        conn = _connect(p)
        try:
            with conn:
                conn.execute(
                    "INSERT INTO tutor_feedback_log "
                    "(session_id, timestamp, message_index, verdict, mode, topic) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        row["session_id"], row["timestamp"], row["message_index"],
                        row["verdict"], row["mode"], row["topic"],
                    ),
                )
        finally:
            conn.close()
        return True
    except Exception:
        log.exception("feedback log: upis nije uspio")
        return False


def get_recent_feedback(
    session_id: str | None = None,
    limit: int = 50,
    path: str | Path | None = None,
) -> list[dict]:
    """Zadnji feedback zapisi, najnoviji prvi. Greška → prazna lista."""
    try:
        p = init_db(path)
        query = "SELECT * FROM tutor_feedback_log"
        params: list[Any] = []
        if session_id:
            query += " WHERE session_id = ?"
            params.append(str(session_id))
        query += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        conn = _connect(p)
        try:
            return [dict(r) for r in conn.execute(query, params).fetchall()]
        finally:
            conn.close()
    except Exception:
        log.exception("feedback log: čitanje nije uspjelo")
        return []


def get_recent_activity(
    session_id: str | None = None,
    student_id: str | None = None,
    limit: int = 50,
    path: str | Path | None = None,
) -> list[dict]:
    """Zadnjih ``limit`` redova (najnoviji prvi), opciono filtrirano po
    session_id i/ili student_id. Greška → prazna lista (nikad ne baca)."""
    try:
        p = init_db(path)
        query = "SELECT * FROM student_activity_log"
        conds: list[str] = []
        params: list[Any] = []
        if session_id:
            conds.append("session_id = ?")
            params.append(str(session_id))
        if student_id:
            conds.append("student_id = ?")
            params.append(str(student_id))
        if conds:
            query += " WHERE " + " AND ".join(conds)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        conn = _connect(p)
        try:
            return [dict(r) for r in conn.execute(query, params).fetchall()]
        finally:
            conn.close()
    except Exception:
        log.exception("activity log: čitanje nije uspjelo")
        return []
