"""Testovi za matbot.activity_log (Phase 5) + session_id u templateu.

Sve na tmp_path SQLite fajlovima — bez mreže, bez diranja repo storage/ foldera.
"""
import sqlite3

import pytest

from matbot import activity_log as al


def _cols(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return [r[1] for r in conn.execute("PRAGMA table_info(student_activity_log)")]
    finally:
        conn.close()


# --- init_db ---------------------------------------------------------------------

def test_init_db_creates_folder_and_table(tmp_path):
    db = tmp_path / "novi_folder" / "log.sqlite3"   # folder ne postoji → kreira se
    p = al.init_db(db)
    assert p.exists()
    cols = _cols(p)
    for col in ("id", "student_id", "session_id", "timestamp", "event_type",
                "grade", "entry_source", "course_name", "section_name", "lesson_title",
                "final_topic", "mode", "status", "parent_report_signal",
                "mistake_tag", "recommendation", "topic_conflict", "task_id",
                "task_status", "attempt_number", "total_attempt_count",
                "wrong_attempt_count", "hint_count", "parent_task_id",
                "followup_task_id", "task_origin", "hint_level",
                "highest_hint_level", "hint_reason", "solution_revealed",
                "solved_independently", "solved_with_hints",
                "completed_parent_task"):
        assert col in cols
    # NE postoje kolone za pune poruke/odgovore
    assert "student_message" not in cols
    assert "answer" not in cols


def test_resolve_db_path_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "env.sqlite3"))
    assert al.resolve_db_path() == tmp_path / "env.sqlite3"
    monkeypatch.delenv("MATBOT_DB_PATH")
    assert al.resolve_db_path() == al.DEFAULT_DB_PATH
    # eksplicitna putanja ima prednost nad env
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "env.sqlite3"))
    assert al.resolve_db_path(tmp_path / "x.sqlite3") == tmp_path / "x.sqlite3"


# --- log_student_activity ---------------------------------------------------------

def test_log_inserts_one_row(tmp_path):
    db = tmp_path / "log.sqlite3"
    ok = al.log_student_activity(
        {"session_id": "s-1", "student_id": "u-9", "entry_source": "manual_topic_choice",
         "grade": 7,
         "course_name": "Matematika 6", "section_name": "Skupovi",
         "lesson_title": "Lekcija", "student_message": "TAJNA PORUKA"},
        {"final_topic": "skupovi_uvod", "mode": "explain", "status": "ready",
         "parent_report_signal": "neutral", "topic_conflict": False,
         "entry_source_used": "manual_topic_choice", "answer": "TAJNI ODGOVOR"},
        path=db,
    )
    assert ok is True
    rows = al.get_recent_activity(session_id="s-1", path=db)
    assert len(rows) == 1
    r = rows[0]
    assert r["student_id"] == "u-9"
    assert r["event_type"] == "topic_selected"
    assert r["grade"] == 7
    assert r["final_topic"] == "skupovi_uvod"
    assert r["status"] == "ready"
    assert r["timestamp"]
    assert r["topic_conflict"] == 0
    # metapodaci samo: poruka i odgovor NIKAD u bazi
    raw = db.read_bytes()
    assert b"TAJNA PORUKA" not in raw
    assert b"TAJNI ODGOVOR" not in raw


def test_init_db_migrates_existing_table_adds_grade(tmp_path):
    db = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "CREATE TABLE student_activity_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "student_id TEXT NULL, session_id TEXT NULL, timestamp TEXT NOT NULL, "
            "event_type TEXT NOT NULL, entry_source TEXT NULL, course_name TEXT NULL, "
            "section_name TEXT NULL, lesson_title TEXT NULL, final_topic TEXT NULL, "
            "mode TEXT NULL, status TEXT NULL, parent_report_signal TEXT NULL, "
            "mistake_tag TEXT NULL, recommendation TEXT NULL, topic_conflict INTEGER DEFAULT 0)"
        )
        conn.commit()
    finally:
        conn.close()

    al.init_db(db)
    assert "grade" in _cols(db)


def test_event_type_priorities():
    # practice_answer > exam > topic_selected > ai_message
    assert al.classify_event_type(
        {"interaction_phase": "answering_practice_task", "entry_source": "manual_topic_choice"},
        {"mode": "exam"},
    ) == "practice_answer"
    assert al.classify_event_type(
        {"entry_source": "manual_topic_choice"}, {"mode": "exam"}
    ) == "exam_mode_used"
    assert al.classify_event_type(
        {"entry_source": "manual_topic_choice"}, {"mode": "practice"}
    ) == "topic_selected"
    assert al.classify_event_type({}, {"mode": "explain"}) == "ai_message"


def test_log_includes_task_lifecycle_metadata_without_full_text(tmp_path):
    db = tmp_path / "lifecycle.sqlite3"
    ok = al.log_student_activity(
        {"session_id": "s-life", "student_message": "SECRET STUDENT TEXT"},
        {
            "status": "ready",
            "mode": "practice",
            "task_id": "task-child",
            "task_status": "active",
            "attempt_number": 0,
            "total_attempt_count": 0,
            "wrong_attempt_count": 0,
            "hint_count": 0,
            "parent_task_id": "task-parent",
            "followup_task_id": "task-child",
            "task_origin": "independent_followup",
            "hint_level": 0,
            "highest_hint_level": 0,
            "solution_revealed": False,
            "solved_independently": False,
            "solved_with_hints": False,
            "completed_parent_task": {
                "task_id": "task-parent",
                "task_status": "completed",
                "attempt_number": 2,
                "wrong_attempt_count": 1,
                "hint_count": 5,
                "solution_revealed": True,
                "solved_with_hints": True,
                "followup_task_id": "task-child",
            },
            "answer": "SECRET TUTOR TEXT",
        },
        path=db,
    )
    assert ok is True
    row = al.get_recent_activity(session_id="s-life", path=db)[0]
    assert row["task_id"] == "task-child"
    assert row["task_status"] == "active"
    assert row["attempt_number"] == 0
    assert row["wrong_attempt_count"] == 0
    assert row["hint_count"] == 0
    assert row["parent_task_id"] == "task-parent"
    assert row["followup_task_id"] == "task-child"
    assert row["task_origin"] == "independent_followup"
    assert '"task_id": "task-parent"' in row["completed_parent_task"]
    raw = db.read_bytes()
    assert b"SECRET STUDENT TEXT" not in raw
    assert b"SECRET TUTOR TEXT" not in raw


def test_get_recent_filters_by_session(tmp_path):
    db = tmp_path / "log.sqlite3"
    for sid, n in (("s-a", 2), ("s-b", 3)):
        for _ in range(n):
            al.log_student_activity({"session_id": sid}, {"mode": "explain", "status": "ready"}, path=db)
    assert len(al.get_recent_activity(session_id="s-a", path=db)) == 2
    assert len(al.get_recent_activity(session_id="s-b", path=db)) == 3
    assert len(al.get_recent_activity(path=db)) == 5
    # limit radi
    assert len(al.get_recent_activity(path=db, limit=1)) == 1


def test_log_tutor_feedback(tmp_path):
    db = tmp_path / "feedback.sqlite3"
    ok = al.log_tutor_feedback(
        {"session_id": "s-fb", "message_index": 1, "verdict": "down",
         "mode": "practice", "topic": "6-01-001"},
        path=db,
    )
    assert ok is True
    rows = al.get_recent_feedback(session_id="s-fb", path=db)
    assert len(rows) == 1
    assert rows[0]["message_index"] == 1
    assert rows[0]["verdict"] == "down"
    assert rows[0]["topic"] == "6-01-001"


def test_log_tutor_feedback_rejects_invalid(tmp_path):
    db = tmp_path / "feedback_bad.sqlite3"
    assert al.log_tutor_feedback(
        {"session_id": "s-fb", "message_index": 1, "verdict": "maybe"},
        path=db,
    ) is False


def test_log_failure_returns_false_never_raises(tmp_path):
    # roditeljska "putanja" je fajl → mkdir puca → False, bez izuzetka
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    bad = blocker / "sub" / "log.sqlite3"
    assert al.log_student_activity({"session_id": "s"}, {"status": "ready"}, path=bad) is False
    assert al.get_recent_activity(session_id="s", path=bad) == []


# --- Phase 6.1: WAL, busy_timeout, indeksi ----------------------------------------

def test_wal_mode_and_busy_timeout(tmp_path):
    db = tmp_path / "wal.sqlite3"
    al.init_db(db)
    conn = al._connect(db)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        conn.close()


def test_indexes_created(tmp_path):
    db = tmp_path / "idx.sqlite3"
    al.init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
    finally:
        conn.close()
    assert "idx_activity_session_ts" in names
    assert "idx_activity_student_ts" in names


def test_concurrent_writes_smoke(tmp_path):
    """WAL + busy_timeout: par threadova upisuje istovremeno bez greške."""
    import threading
    db = tmp_path / "conc.sqlite3"
    results = []

    def work(i):
        ok = al.log_student_activity({"session_id": f"s{i % 2}"}, {"status": "ready"}, path=db)
        results.append(ok)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert all(results)
    assert len(al.get_recent_activity(path=db, limit=20)) == 8


# --- template šalje session_id (Phase 5, frontend) --------------------------------

def test_template_sends_session_id(client):
    html = client.get("/").get_data(as_text=True)
    assert "matbot_session_id" in html              # localStorage ključ
    assert "session_id: sessionId" in html          # ide u tutor payload
    assert "crypto.randomUUID" in html              # UUID sa fallbackom
