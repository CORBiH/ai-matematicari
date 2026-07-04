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
import sqlite3
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
    topic_conflict INTEGER DEFAULT 0
)
"""

_COLUMNS = (
    "student_id", "session_id", "timestamp", "event_type", "entry_source",
    "course_name", "section_name", "lesson_title", "final_topic", "mode",
    "status", "parent_report_signal", "mistake_tag", "recommendation",
    "topic_conflict",
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
            for idx in _INDEXES:
                conn.execute(idx)
    finally:
        conn.close()
    return p


def _clean(val: Any) -> str | None:
    s = str(val).strip() if val is not None else ""
    return s or None


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
        row = {
            "student_id": _clean(payload.get("student_id")),
            "session_id": _clean(payload.get("session_id")),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": classify_event_type(payload, response),
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
