"""Faza 3D — registar učenika, evidencija časova, migracija v3, izvještaj.

TVRDNJA KOJU OVAJ FAJL DOKAZUJE: čas je ravnopravan izvor izvještaja, a njegova
semantika se ne smije pomiješati ni s ocjenom iz matematike ni sa sposobnošću.
Sve tri granice iz `student_sessions` imaju ovdje svoj test:

  1. angažman (1–5) NIJE ocjena,
  2. odsutan učenik NEMA angažman,
  3. „nije zadana" NIJE neurađena zadaća.

PII: svi učenici i svi komentari su sintetički.
"""
import json

import pytest

from matbot import (parent_report, report_facts, report_input, report_pdf,
                    report_prompt, reporting_db, reporting_schema,
                    student_sessions)
from matbot.student_identity import PROVIDER_THINKIFIC_EMAIL

from tests.test_thinkific_progress_import import build_v1, migrate, migrate_v2_only

libsql = pytest.importorskip("libsql")
pypdf = pytest.importorskip("pypdf")


# ---------------------------------------------------------------------------
# Pomoćnici
# ---------------------------------------------------------------------------
# Kanonske vrijednosti iz STVARNOG kurikuluma 6. razreda — od Faze 3D
# otvrdnjavanja gradivo se provjerava prema `data/topics.json`.
CANON_AREA = "Djeljivost brojeva"
CANON_LESSON = "Djeljivost zbira, razlike i proizvoda"


def session(date="2026-08-05", attendance="present", activity=4,
            homework="done", area=CANON_AREA, lesson=CANON_LESSON,
            comment=None, grade=6):
    return student_sessions.validate_session(
        session_date=date, attendance=attendance, activity_rating=activity,
        homework_status=homework, area_name=area, lesson_name=lesson,
        comment=comment, grade=grade)


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "reporting.db")
    build_v1(path)
    migrate(path)
    conn = libsql.connect(path)
    conn.execute("DROP TABLE IF EXISTS monthly_reports")
    conn.execute(reporting_schema.MONTHLY_REPORTS_DDL)
    conn.execute(reporting_schema.MONTHLY_REPORTS_INDEX_DDL)
    conn.commit()
    conn.close()
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://test.invalid")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token-not-real")
    database = reporting_db.ReportingDatabase(
        connect_factory=lambda: libsql.connect(path, timeout=10.0,
                                               _check_same_thread=False))
    reporting_db.set_database(database)
    yield database
    reporting_db.wait_for_pending_writes()
    reporting_db.set_database(None)


@pytest.fixture
def student(db):
    return db.create_student("Sintetički Učenik", 6)


# ===========================================================================
# 1) MIGRACIJA V3
# ===========================================================================
def _fresh_v2(tmp_path, name="v2.db"):
    path = str(tmp_path / name)
    build_v1(path)
    migrate_v2_only(path)
    return path


def test_production_shaped_v2_migrates_to_v3(tmp_path):
    """Stvarni oblik v2 (uključujući produkcijsku `monthly_reports`) → v3."""
    path = _fresh_v2(tmp_path)
    conn = libsql.connect(path)
    conn.execute("DROP TABLE IF EXISTS monthly_reports")
    conn.execute(reporting_schema.MONTHLY_REPORTS_DDL)
    conn.commit()
    assert reporting_schema.current_version(conn) == 2

    assert reporting_schema.migrate_to_v3(conn) is True
    assert reporting_schema.current_version(conn) == 3
    assert reporting_schema.verify_v3_schema(conn) == []
    # Ponovni poziv ne radi ništa — migracija je idempotentna.
    assert reporting_schema.migrate_to_v3(conn) is False
    # Faza 3C ostaje netaknuta.
    assert reporting_schema.verify_monthly_reports_schema(conn) == []
    conn.close()


def test_v3_migration_records_a_description(tmp_path):
    """Živi incident v1→v2: `description` je NOT NULL bez default-a."""
    path = _fresh_v2(tmp_path)
    conn = libsql.connect(path)
    reporting_schema.migrate_to_v3(conn)
    rows = conn.execute(
        "SELECT description FROM schema_migrations WHERE version = 3").fetchall()
    assert rows and rows[0][0] == reporting_schema.MIGRATION_DESCRIPTIONS[3]
    assert rows[0][0].strip()
    conn.close()


def test_v3_table_has_the_required_structure(tmp_path):
    path = _fresh_v2(tmp_path)
    conn = libsql.connect(path)
    reporting_schema.migrate_to_v3(conn)
    columns = {row[1]: row for row
               in conn.execute("PRAGMA table_info(student_sessions)").fetchall()}
    for name in reporting_schema.EXPECTED_V3_SCHEMA["student_sessions"]["required_columns"]:
        assert name in columns, name
    for name in ("student_id", "session_date", "attendance", "homework_status"):
        assert columns[name][3], "%s mora biti NOT NULL" % name
    # Angažman SMIJE biti NULL — odsutan učenik ga nema.
    assert not columns["activity_rating"][3]
    keys = conn.execute("PRAGMA foreign_key_list(student_sessions)").fetchall()
    assert keys and keys[0][2] == "students" and keys[0][3] == "student_id"
    assert (keys[0][6] or "").upper() == "CASCADE"
    conn.close()


def test_v3_index_exists(tmp_path):
    path = _fresh_v2(tmp_path)
    conn = libsql.connect(path)
    reporting_schema.migrate_to_v3(conn)
    names = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()}
    assert "idx_student_sessions_student_date" in names
    columns = tuple(r[2] for r in conn.execute(
        "PRAGMA index_info(idx_student_sessions_student_date)").fetchall())
    assert columns == ("student_id", "session_date")
    conn.close()


def test_partial_table_without_version_row_resumes(tmp_path):
    """Prekinuta migracija: tabela postoji, zapis verzije ne. Mora se dovršiti."""
    path = _fresh_v2(tmp_path)
    conn = libsql.connect(path)
    for statement in reporting_schema.SCHEMA_V3_STATEMENTS:
        conn.execute(statement)
    conn.commit()
    assert reporting_schema.current_version(conn) == 2

    assert reporting_schema.migrate_to_v3(conn) is True
    assert reporting_schema.current_version(conn) == 3
    conn.close()


def test_malformed_existing_table_fails_closed(tmp_path):
    """Tuđa tabela istog imena se NE smije blagosloviti.

    `CREATE TABLE IF NOT EXISTS` nad njom tiho ne uradi ništa — bez ove provjere
    bi migracija „uspjela" nad neupotrebljivom strukturom."""
    path = _fresh_v2(tmp_path)
    conn = libsql.connect(path)
    conn.execute("CREATE TABLE student_sessions (id INTEGER PRIMARY KEY)")
    conn.commit()
    with pytest.raises(reporting_schema.MigrationError) as caught:
        reporting_schema.migrate_to_v3(conn)
    assert caught.value.code.startswith("v3_")
    assert reporting_schema.current_version(conn) == 2, "verzija NIJE smjela biti upisana"
    conn.close()


def test_recorded_v3_without_a_usable_table_fails_closed(tmp_path):
    path = _fresh_v2(tmp_path)
    conn = libsql.connect(path)
    conn.execute("INSERT INTO schema_migrations (version, description) "
                 "VALUES (3, 'lažni zapis')")
    conn.commit()
    with pytest.raises(reporting_schema.MigrationError):
        reporting_schema.migrate_to_v3(conn)
    conn.close()


def test_v3_migration_requires_v2_first(tmp_path):
    path = str(tmp_path / "v1.db")
    build_v1(path)
    conn = libsql.connect(path)
    with pytest.raises(reporting_schema.MigrationError) as caught:
        reporting_schema.migrate_to_v3(conn)
    assert caught.value.code == "v2_migration_record_missing"
    conn.close()


def test_v3_migration_touches_no_phase3c_data(tmp_path):
    """Postojeći red u `monthly_reports` mora preživjeti migraciju."""
    path = _fresh_v2(tmp_path)
    conn = libsql.connect(path)
    conn.execute("DROP TABLE IF EXISTS monthly_reports")
    conn.execute(reporting_schema.MONTHLY_REPORTS_DDL)
    conn.execute("INSERT INTO students (display_name, grade) VALUES ('X', 6)")
    conn.execute("INSERT INTO monthly_reports (student_id, report_month, status, "
                 " ai_summary) VALUES (1, '2026-07', 'draft', '{\"summary\": \"staro\"}')")
    conn.commit()

    reporting_schema.migrate_to_v3(conn)
    rows = conn.execute("SELECT ai_summary FROM monthly_reports").fetchall()
    assert rows and "staro" in rows[0][0]
    conn.close()


def test_checker_exposes_v3_state(db):
    report = db.check()
    assert report["schema_version"] == 3
    assert report["v3_schema_verified"] is True
    rendered = reporting_db._format_report(report)
    assert "v3_schema: verified" in rendered
    assert "columns[student_sessions]" in rendered


# ===========================================================================
# 2) REGISTAR UČENIKA — jedna kanonska tabela
# ===========================================================================
def test_manual_student_can_be_created(db):
    student_id = db.create_student("Ručno Upisan", 7)
    rows = db.list_students()
    assert any(r["student_id"] == student_id and r["grade"] == 7 for r in rows)


def test_thinkific_students_appear_in_the_same_registry(db):
    thinkific_id = db.get_or_create_student(
        PROVIDER_THINKIFIC_EMAIL, "ucenik@example.com", grade=6)
    manual_id = db.create_student("Ručno Upisan", 6)
    listed = {r["student_id"]: r for r in db.list_students()}
    assert thinkific_id in listed and manual_id in listed
    assert listed[thinkific_id]["thinkific_linked"] is True
    assert listed[manual_id]["thinkific_linked"] is False


def test_registry_never_returns_an_email(db):
    db.get_or_create_student(PROVIDER_THINKIFIC_EMAIL, "tajna@example.com", grade=6)
    blob = json.dumps(db.list_students(), ensure_ascii=False)
    assert "@" not in blob and "tajna" not in blob


@pytest.mark.parametrize("grade", [0, 5, 10, None, "šest"])
def test_invalid_grade_is_rejected(db, grade):
    with pytest.raises(reporting_db.ReportingUnavailable):
        db.create_student("Neko", grade)


@pytest.mark.parametrize("name", ["", "   ", None])
def test_empty_name_is_rejected(db, name):
    with pytest.raises(reporting_db.ReportingUnavailable):
        db.create_student(name, 6)


def test_thinkific_account_can_be_linked_to_a_manual_student(db):
    student_id = db.create_student("Ručno Upisan", 6)
    assert db.student_has_thinkific(student_id) is False
    assert db.link_thinkific_account(student_id, "novi@example.com") is True
    assert db.student_has_thinkific(student_id) is True


def test_linking_an_account_owned_by_another_student_fails_closed(db):
    owner = db.get_or_create_student(PROVIDER_THINKIFIC_EMAIL, "zauzet@example.com",
                                     grade=6)
    other = db.create_student("Drugi Učenik", 6)
    with pytest.raises(reporting_db.ReportingUnavailable) as caught:
        db.link_thinkific_account(other, "zauzet@example.com")
    # Kod nosi ID POSTOJEĆEG učenika da administrator zna koji zapis da pogleda.
    assert caught.value.code == "student_account_taken:%d" % owner
    # Nalog NIJE preuzet.
    assert db.student_has_thinkific(other) is False


def test_relinking_the_same_account_to_the_same_student_is_a_noop(db):
    student_id = db.create_student("Neko", 6)
    assert db.link_thinkific_account(student_id, "isti@example.com") is True
    assert db.link_thinkific_account(student_id, "isti@example.com") is False


def test_manual_student_has_no_account_when_no_email_supplied(db):
    student_id = db.create_student("Bez Naloga", 8)
    assert db.student_has_thinkific(student_id) is False


# ===========================================================================
# 3) SEMANTIKA ČASA
# ===========================================================================
@pytest.mark.parametrize("rating", [1, 2, 3, 4, 5])
def test_present_with_any_valid_activity(rating):
    record = session(activity=rating)
    assert record["activity_rating"] == rating


@pytest.mark.parametrize("rating", [0, 6, -1, 99])
def test_activity_outside_one_to_five_is_rejected(rating):
    with pytest.raises(student_sessions.SessionValidationError) as caught:
        session(activity=rating)
    assert caught.value.code == "session_activity_range"


def test_absent_student_must_have_no_activity():
    """GRANICA 2: lažna jedinica za odsutnog bi mjesecima obarala prosjek."""
    with pytest.raises(student_sessions.SessionValidationError) as caught:
        session(attendance="absent", activity=1)
    assert caught.value.code == "session_absent_with_activity"
    assert session(attendance="absent", activity=None)["activity_rating"] is None


@pytest.mark.parametrize("value", ["", "prisutan", "PRESENT", "maybe"])
def test_invalid_attendance_is_rejected(value):
    with pytest.raises(student_sessions.SessionValidationError):
        session(attendance=value)


@pytest.mark.parametrize("status", ["done", "not_done", "not_assigned"])
def test_all_three_homework_states_are_valid(status):
    assert session(homework=status)["homework_status"] == status


@pytest.mark.parametrize("status", ["", "maybe", "true", "1"])
def test_invalid_homework_status_is_rejected(status):
    with pytest.raises(student_sessions.SessionValidationError):
        session(homework=status)


def test_date_is_stored_canonically():
    assert session(date="2026-08-05")["session_date"] == "2026-08-05"


@pytest.mark.parametrize("value", ["05.08.2026", "2026-13-01", "2026-02-30",
                                   "2026-8-5", "", None, "danas"])
def test_invalid_date_is_rejected(value):
    with pytest.raises(student_sessions.SessionValidationError):
        session(date=value)


def test_comment_length_is_bounded():
    assert session(comment="x" * student_sessions.MAX_COMMENT_CHARS)
    with pytest.raises(student_sessions.SessionValidationError) as caught:
        session(comment="x" * (student_sessions.MAX_COMMENT_CHARS + 1))
    assert caught.value.code == "session_comment_too_long"


def test_labels_reject_markup():
    with pytest.raises(student_sessions.SessionValidationError):
        session(area="<b>Razlomci</b>", grade=None)
    # A s razredom pada i kao nekanonska vrijednost.
    with pytest.raises(student_sessions.SessionValidationError):
        session(area="<b>Razlomci</b>")


def test_activity_labels_never_call_it_a_grade():
    """Angažman NIJE ocjena iz matematike — ni u jednoj oznaci."""
    blob = " ".join(student_sessions.ACTIVITY_LABELS.values()).lower()
    assert "ocjen" not in blob
    assert set(student_sessions.ACTIVITY_LABELS) == {1, 2, 3, 4, 5}


# ===========================================================================
# 4) CRUD
# ===========================================================================
def test_session_crud_round_trip(db, student):
    session_id = db.insert_session(student, session(comment="Prvi čas."))
    stored = db.fetch_session(session_id, student)
    assert stored["session_date"] == "2026-08-05"
    assert stored["comment"] == "Prvi čas."

    assert db.update_session(session_id, student,
                             session(activity=2, comment="Ispravka.")) is True
    assert db.fetch_session(session_id, student)["activity_rating"] == 2

    assert db.delete_session(session_id, student) is True
    assert db.fetch_session(session_id, student) is None


def test_another_students_session_cannot_be_touched(db, student):
    other = db.create_student("Drugi", 6)
    session_id = db.insert_session(student, session())
    # Vlasništvo je USLOV u upitu — pogođen ID ne pomaže.
    assert db.update_session(session_id, other, session(activity=1)) is False
    assert db.delete_session(session_id, other) is False
    assert db.fetch_session(session_id, other) is None
    assert db.fetch_session(session_id, student) is not None


def test_delete_removes_only_the_intended_row(db, student):
    first = db.insert_session(student, session(date="2026-08-01"))
    second = db.insert_session(student, session(date="2026-08-02"))
    db.delete_session(first, student)
    remaining = [r["id"] for r in db.fetch_sessions(student)]
    assert remaining == [second]


def test_history_is_deterministically_ordered(db, student):
    db.insert_session(student, session(date="2026-08-09"))
    db.insert_session(student, session(date="2026-08-02"))
    db.insert_session(student, session(date="2026-08-02"))
    rows = db.fetch_sessions(student)
    assert [r["session_date"] for r in rows] == ["2026-08-02", "2026-08-02",
                                                 "2026-08-09"]
    assert rows[0]["id"] < rows[1]["id"]


def test_deleting_a_student_cascades_their_sessions(db, student):
    db.insert_session(student, session())
    conn = db._connection()
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("DELETE FROM students WHERE id = ?", (student,))
    conn.commit()
    assert db.fetch_sessions(student) == []


# ===========================================================================
# 5) MJESEČNI SAŽETAK
# ===========================================================================
def _summary(rows):
    return student_sessions.build_monthly_summary(rows)


def _row(date, attendance="present", activity=4, homework="done",
         area="Razlomci", lesson=None, comment=None, row_id=1):
    return {"id": row_id, "session_date": date, "attendance": attendance,
            "activity_rating": activity, "homework_status": homework,
            "area_name": area, "lesson_name": lesson, "comment": comment}


def test_attendance_counts(db, student):
    for date in ("2026-08-01", "2026-08-08", "2026-08-15"):
        db.insert_session(student, session(date=date))
    db.insert_session(student, session(date="2026-08-22", attendance="absent",
                                       activity=None, homework="not_assigned"))
    summary = report_input.build_instruction_section(student, "2026-08", database=db)
    assert summary["sessions_total"] == 4
    assert summary["present_count"] == 3
    assert summary["absent_count"] == 1


def test_month_boundaries_are_half_open(db, student):
    db.insert_session(student, session(date="2026-07-31"))
    db.insert_session(student, session(date="2026-08-01"))
    db.insert_session(student, session(date="2026-08-31"))
    db.insert_session(student, session(date="2026-09-01"))
    summary = report_input.build_instruction_section(student, "2026-08", database=db)
    assert summary["sessions_total"] == 2


def test_activity_average_uses_only_rated_present_sessions():
    summary = _summary([
        _row("2026-08-01", activity=5),
        _row("2026-08-02", activity=3),
        _row("2026-08-03", attendance="absent", activity=None),
        _row("2026-08-04", activity=None),          # prisutan, neocijenjen
    ])
    assert summary["activity"]["rated_sessions"] == 2
    assert summary["activity"]["average"] == 4.0


def test_no_ratings_means_no_average_not_zero():
    summary = _summary([_row("2026-08-01", activity=None)])
    assert summary["activity"]["average"] is None
    assert summary["activity"]["rated_sessions"] == 0


def test_homework_denominator_excludes_not_assigned():
    """GRANICA 3: „nije zadana" nije neurađena zadaća."""
    summary = _summary([
        _row("2026-08-01", homework="done"),
        _row("2026-08-02", homework="done"),
        _row("2026-08-03", homework="not_done"),
        _row("2026-08-04", homework="not_assigned"),
        _row("2026-08-05", homework="not_assigned"),
    ])
    assert summary["homework"]["assigned_count"] == 3
    assert summary["homework"]["done_count"] == 2
    assert summary["homework"]["not_done_count"] == 1
    assert summary["homework"]["not_assigned_count"] == 2


def test_zero_assigned_homework_is_not_zero_percent():
    summary = _summary([_row("2026-08-01", homework="not_assigned")])
    assert summary["homework"]["assigned_count"] == 0
    assert summary["homework"]["done_count"] == 0


def test_worked_areas_are_distinct_and_ordered():
    # `_row` gradi red kakav baza vraća — ovdje se mjeri SAŽETAK, ne validacija.
    summary = _summary([
        _row("2026-08-01", area="Razlomci", lesson="Sabiranje"),
        _row("2026-08-02", area="Razlomci", lesson="Oduzimanje"),
        _row("2026-08-03", area="Jednačine", lesson="Sabiranje"),
    ])
    assert summary["areas_worked"] == ["Razlomci", "Jednačine"]
    assert summary["lessons_worked"] == ["Sabiranje", "Oduzimanje"]


def test_parent_comments_are_capped_and_newest_first():
    summary = _summary([
        _row("2026-08-01", comment="prvi", row_id=1),
        _row("2026-08-08", comment="drugi", row_id=2),
        _row("2026-08-15", comment="treći", row_id=3),
        _row("2026-08-22", comment="četvrti", row_id=4),
        _row("2026-08-23", comment="   ", row_id=5),      # prazan se preskače
    ])
    comments = summary["parent_comments"]
    assert len(comments) == student_sessions.MAX_PARENT_COMMENTS == 3
    assert [c["comment"] for c in comments] == ["četvrti", "treći", "drugi"]


def test_same_day_comment_order_is_deterministic():
    summary = _summary([_row("2026-08-05", comment="a", row_id=1),
                        _row("2026-08-05", comment="b", row_id=2)])
    assert [c["comment"] for c in summary["parent_comments"]] == ["b", "a"]


# ===========================================================================
# 6) SIGNALI RADNIH NAVIKA
# ===========================================================================
def test_no_signal_below_the_minimum_sample():
    """Dva časa nisu obrazac. Ispod praga se NE tvrdi ništa."""
    summary = _summary([_row("2026-08-01"), _row("2026-08-02")])
    assert summary["signals"] == []


def test_consistent_attendance_signal():
    summary = _summary([_row("2026-08-0%d" % i) for i in range(1, 5)])
    assert student_sessions.SIGNAL_CONSISTENT_ATTENDANCE in summary["signals"]


def test_attendance_needing_attention_signal():
    rows = [_row("2026-08-01"), _row("2026-08-02", attendance="absent", activity=None),
            _row("2026-08-03", attendance="absent", activity=None),
            _row("2026-08-04", attendance="absent", activity=None)]
    assert student_sessions.SIGNAL_ATTENDANCE_NEEDS_ATTENTION in _summary(rows)["signals"]


def test_engagement_signals():
    strong = _summary([_row("2026-08-0%d" % i, activity=5) for i in range(1, 4)])
    assert student_sessions.SIGNAL_STRONG_ENGAGEMENT in strong["signals"]
    weak = _summary([_row("2026-08-0%d" % i, activity=2) for i in range(1, 4)])
    assert student_sessions.SIGNAL_ENGAGEMENT_NEEDS_SUPPORT in weak["signals"]


def test_homework_signals():
    good = _summary([_row("2026-08-0%d" % i, homework="done") for i in range(1, 5)])
    assert student_sessions.SIGNAL_CONSISTENT_HOMEWORK in good["signals"]
    poor = _summary([_row("2026-08-0%d" % i, homework="not_done") for i in range(1, 5)])
    assert student_sessions.SIGNAL_HOMEWORK_NEEDS_ATTENTION in poor["signals"]


def test_the_band_between_thresholds_claims_nothing():
    """Između 60 % i 80 % se ne tvrdi ni jedno ni drugo — jedan čas ne smije
    prevrnuti poruku roditelju."""
    rows = [_row("2026-08-01"), _row("2026-08-02"), _row("2026-08-03"),
            _row("2026-08-04", attendance="absent", activity=None)]
    signals = _summary(rows)["signals"]
    assert student_sessions.SIGNAL_ATTENDANCE_NEEDS_ATTENTION not in signals


# ===========================================================================
# 7) POPULACIJA I UGOVOR PREMA MODELU
# ===========================================================================
def test_class_only_student_enters_the_report_population(db, student):
    """OBAVEZNO: učenik bez naloga i bez MAT-BOT-a, ali s časom, ide u izvještaj."""
    assert report_input.report_population("2026-08", database=db) == []
    db.insert_session(student, session(date="2026-08-11"))
    assert student in report_input.report_population("2026-08", database=db)
    # Drugi mjesec ga ne uključuje.
    assert student not in report_input.report_population("2026-09", database=db)


def test_registry_membership_alone_does_not_earn_a_report(db):
    db.create_student("Samo U Registru", 6)
    assert report_input.report_population("2026-08", database=db) == []


def test_report_input_carries_the_instruction_section(db, student):
    db.insert_session(student, session(date="2026-08-04", comment="Bilješka."))
    payload = report_input.build_report_input(student, "2026-08", database=db)
    assert payload["instruction"]["sessions_total"] == 1
    assert payload["instruction"]["parent_comments"][0]["comment"] == "Bilješka."


def test_ai_facts_never_receive_raw_class_comments(db, student):
    """Dio 21: slobodan tekst instruktora modelu ne ide NIKAD."""
    db.insert_session(student, session(
        date="2026-08-04", comment="Tajna bilješka o porodici."))
    payload = report_input.build_report_input(student, "2026-08", database=db)
    facts = report_facts.build_ai_facts(payload)
    blob = json.dumps(facts, ensure_ascii=False)
    assert "Tajna bilješka" not in blob
    assert "parent_comments" not in blob
    # A brojevi i signali JESU tu.
    assert facts["instruction"]["sessions_total"] == 1


def test_ai_facts_carry_no_identity(db, student):
    db.insert_session(student, session(date="2026-08-04"))
    payload = report_input.build_report_input(student, "2026-08", database=db)
    facts = report_facts.build_ai_facts(payload)
    blob = json.dumps(facts, ensure_ascii=False)
    assert "Sintetički Učenik" not in blob
    assert "student_id" not in blob and "@" not in blob


def test_instruction_numbers_are_decided_before_the_model(db, student):
    for date in ("2026-08-01", "2026-08-08", "2026-08-15"):
        db.insert_session(student, session(date=date, activity=4))
    payload = report_input.build_report_input(student, "2026-08", database=db)
    facts = report_facts.build_ai_facts(payload)
    assert facts["instruction"]["activity_average"] == 4.0
    assert facts["instruction"]["present_count"] == 3
    assert student_sessions.SIGNAL_CONSISTENT_ATTENDANCE in facts["instruction"]["signals"]
    # Sve te brojke su i DOPUŠTENE modelu da ih navede.
    allowed = report_facts.allowed_numbers(facts)
    assert 3.0 in allowed and 4.0 in allowed


def test_class_curriculum_labels_are_trusted_spans(db, student):
    """Naziv lekcije S CIFROM mora biti pouzdan raspon i kad dolazi s časa."""
    digit_lesson = "Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25"
    db.insert_session(student, session(date="2026-08-04",
                                       area=CANON_AREA, lesson=digit_lesson))
    payload = report_input.build_report_input(student, "2026-08", database=db)
    facts = report_facts.build_ai_facts(payload)
    assert digit_lesson in report_facts.trusted_labels(facts)


# ===========================================================================
# 8) PARENT PDF
# ===========================================================================
def _facts(instruction=None, matbot=None, thinkific=None):
    base = {
        "report_month": "2026-08", "grade": 6,
        "instruction": {"available": False, "sessions_total": 0,
                        "present_count": 0, "absent_count": 0,
                        "activity_average": None, "activity_rated_sessions": 0,
                        "homework_assigned": 0, "homework_done": 0,
                        "homework_not_done": 0, "areas_worked": [],
                        "lessons_worked": [], "signals": []},
        "thinkific": {"available": False, "previous_available": False,
                      "parent_sections": []},
        "matbot": {"any_activity": False, "active_days": 0,
                   "practice": {"answers_total": 0, "accuracy_percent": None},
                   "kontrolni": {"attempts": 0}},
    }
    if instruction:
        base["instruction"].update(instruction)
    if matbot:
        base["matbot"].update(matbot)
    if thinkific:
        base["thinkific"].update(thinkific)
    return base


def _narrative():
    return {"summary": "Sažetak mjeseca.", "strengths": ["Redovno prisustvo."],
            "focus_areas": ["Zadaća."], "next_month_recommendations": ["Vježbati."]}


def _text(data):
    reader = pypdf.PdfReader(__import__("io").BytesIO(data))
    return len(reader.pages), "\n".join(p.extract_text() for p in reader.pages)


FULL_INSTRUCTION = {"available": True, "sessions_total": 8, "present_count": 7,
                    "absent_count": 1, "activity_average": 4.1,
                    "activity_rated_sessions": 7, "homework_assigned": 7,
                    "homework_done": 6, "homework_not_done": 1,
                    "areas_worked": ["Razlomci", "Linearne jednačine"],
                    "lessons_worked": [], "signals": []}

FULL_MATBOT = {"any_activity": True, "active_days": 5,
               "practice": {"answers_total": 42, "accuracy_percent": 71.0,
                            "tasks_presented": 90, "hints_used": 11,
                            "full_solutions_shown": 9},
               "kontrolni": {"attempts": 2, "average_score_percent": 65.0},
               "explain_count": 19, "quick_count": 4}


def test_class_section_is_the_primary_metrics_block():
    _, text = _text(report_pdf.render_report_pdf(
        _facts(FULL_INSTRUCTION, FULL_MATBOT), _narrative(), "", "Neko Neko"))
    assert "RAD NA ČASOVIMA" in text
    # PRVI mjerni odjeljak: dolazi prije MAT-BOT-a i prije platforme.
    assert text.index("RAD NA ČASOVIMA") < text.index("SAMOSTALNI RAD U MAT-BOT-U")
    assert text.index("SAMOSTALNI RAD U MAT-BOT-U") < text.index("RAD NA PLATFORMI")


def test_attendance_renders_as_a_fraction():
    _, text = _text(report_pdf.render_report_pdf(
        _facts(FULL_INSTRUCTION), _narrative(), "", "Neko"))
    assert "7 od 8" in text


def test_activity_renders_out_of_five():
    _, text = _text(report_pdf.render_report_pdf(
        _facts(FULL_INSTRUCTION), _narrative(), "", "Neko"))
    assert "4,1 / 5" in text


def test_homework_renders_done_over_assigned():
    _, text = _text(report_pdf.render_report_pdf(
        _facts(FULL_INSTRUCTION), _narrative(), "", "Neko"))
    assert "6 od 7 urađenih" in text


def test_no_assigned_homework_says_so_instead_of_zero_percent():
    instruction = dict(FULL_INSTRUCTION, homework_assigned=0, homework_done=0,
                       homework_not_done=0)
    _, text = _text(report_pdf.render_report_pdf(
        _facts(instruction), _narrative(), "", "Neko"))
    assert "Zadaća nije evidentirana kao zadana u ovom mjesecu." in text
    assert "0%" not in text


def test_no_sessions_says_so():
    _, text = _text(report_pdf.render_report_pdf(
        _facts(), _narrative(), "", "Neko"))
    assert "Nema evidentiranih časova u ovom mjesecu." in text


def test_matbot_parent_section_is_reduced():
    _, text = _text(report_pdf.render_report_pdf(
        _facts(FULL_INSTRUCTION, FULL_MATBOT), _narrative(), "", "Neko"))
    assert "Aktivnih dana" in text and "Odgovorenih zadataka" in text
    assert "42" in text and "71%" in text
    # Ono što je uklonjeno iz izvještaja roditelju (Dio 25).
    for forbidden in ("Zadataka prikazano", "Nagovještaji", "gotova rješenja",
                      "Objašnjenja", "Rezultat", "90"):
        assert forbidden not in text


def test_kontrolni_is_folded_into_the_matbot_section():
    _, text = _text(report_pdf.render_report_pdf(
        _facts(FULL_INSTRUCTION, FULL_MATBOT), _narrative(), "", "Neko"))
    assert "Kontrolni" in text and "prosjek 65%" in text
    assert "KONTROLNI\n" not in text        # više nije zaseban odjeljak


def test_absent_kontrolni_is_stated():
    matbot = dict(FULL_MATBOT, kontrolni={"attempts": 0})
    _, text = _text(report_pdf.render_report_pdf(
        _facts(FULL_INSTRUCTION, matbot), _narrative(), "", "Neko"))
    assert "Nema evidentiranih kontrolnih." in text


def test_total_course_percentages_are_absent_from_the_parent_pdf():
    """Godišnji procenti kursa su zavodljivi kao naslovna mjera (Dio 26)."""
    thinkific = {"available": True, "previous_available": False,
                 "percent_viewed": 12, "percent_completed": 1,
                 "parent_sections": [{"name": "Razlomci", "current_percent": 40,
                                      "delta_percent": None}]}
    _, text = _text(report_pdf.render_report_pdf(
        _facts(FULL_INSTRUCTION, None, thinkific), _narrative(), "", "Neko"))
    assert "Pregledano sadržaja" not in text
    assert "Završeno kursa" not in text


def test_thinkific_sections_are_capped_at_three():
    rows = [{"name": "S%d" % i, "current_percent": 10 * i, "delta_percent": None}
            for i in range(1, 6)]
    facts = _facts(FULL_INSTRUCTION, None,
                   {"available": True, "previous_available": False,
                    "parent_sections": rows[:3]})
    _, text = _text(report_pdf.render_report_pdf(facts, _narrative(), "", "Neko"))
    assert "S4" not in text and "S5" not in text


def test_no_baseline_thinkific_wording_claims_no_monthly_progress():
    facts = _facts(FULL_INSTRUCTION, None,
                   {"available": True, "previous_available": False,
                    "parent_sections": [{"name": "Razlomci", "current_percent": 40,
                                         "delta_percent": None}]})
    _, text = _text(report_pdf.render_report_pdf(facts, _narrative(), "", "Neko"))
    assert "Evidentirani sadržaji na platformi:" in text
    assert "p.p." not in text


def test_missing_thinkific_snapshot_is_stated():
    _, text = _text(report_pdf.render_report_pdf(
        _facts(FULL_INSTRUCTION), _narrative(), "", "Neko"))
    assert "Thinkific podaci nisu dostupni za ovaj mjesec." in text


def test_class_comments_render_at_most_three_and_are_escaped():
    comments = [{"date": "2026-08-%02d" % day, "comment": "Zapažanje <b>%d</b>" % day}
                for day in (22, 15, 8, 1)]
    _, text = _text(report_pdf.render_report_pdf(
        _facts(FULL_INSTRUCTION), _narrative(), "", "Neko", comments))
    assert "ZAPAŽANJA SA ČASOVA" in text
    assert "22.08." in text and "15.08." in text and "08.08." in text
    assert "01.08." not in text
    # Oznake se ESCAPUJU, pa se u dokumentu vide kao OBIČAN TEKST umjesto da ih
    # reportlab protumači kao podebljanje. Zato `<b>` MORA biti u izvučenom
    # tekstu — da je progutan kao markup, ovdje ga ne bi bilo.
    assert "<b>22</b>" in text


def test_instructor_monthly_comment_still_renders():
    _, text = _text(report_pdf.render_report_pdf(
        _facts(FULL_INSTRUCTION), _narrative(), "Mjesečni komentar.", "Neko"))
    assert "KOMENTAR INSTRUKTORA" in text and "Mjesečni komentar." in text


def test_section_order_matches_the_product_specification():
    comments = [{"date": "2026-08-22", "comment": "Zapažanje."}]
    _, text = _text(report_pdf.render_report_pdf(
        _facts(FULL_INSTRUCTION, FULL_MATBOT), _narrative(), "Komentar.",
        "Neko", comments))
    order = ["SAŽETAK MJESECA", "RAD NA ČASOVIMA", "SAMOSTALNI RAD U MAT-BOT-U",
             "RAD NA PLATFORMI", "POZITIVNE NAVIKE U RADU", "NA ČEMU TREBA RADITI",
             "PREPORUKA ZA NAREDNI MJESEC", "ZAPAŽANJA SA ČASOVA",
             "KOMENTAR INSTRUKTORA"]
    positions = [text.index(title) for title in order]
    assert positions == sorted(positions)


def test_normal_report_is_one_page():
    comments = [{"date": "2026-08-22", "comment": "Samostalno riješio većinu primjera."}]
    pages, _ = _text(report_pdf.render_report_pdf(
        _facts(FULL_INSTRUCTION, FULL_MATBOT), _narrative(), "Komentar.",
        "Đžemal Šćepanović", comments))
    assert pages == 1


def test_class_only_and_no_class_reports_both_render():
    pages, text = _text(report_pdf.render_report_pdf(
        _facts(FULL_INSTRUCTION), _narrative(), "", "Neko"))
    assert pages == 1 and "Nema zabilježene MAT-BOT aktivnosti" in text
    pages, text = _text(report_pdf.render_report_pdf(
        _facts(None, FULL_MATBOT), _narrative(), "", "Neko"))
    assert pages == 1 and "Nema evidentiranih časova" in text


def test_bosnian_month_and_glyphs_are_preserved():
    _, text = _text(report_pdf.render_report_pdf(
        _facts(FULL_INSTRUCTION), _narrative(), "Đačko rođenje: čćžšđ ĆŠČŽ.",
        "Đžemal Šćepanović"))
    assert "august 2026." in text
    for char in "čćžšđĐŠČĆŽ":
        assert char in text, char
    for name in (report_pdf._FONT_REGULAR, report_pdf._FONT_BOLD):
        assert report_pdf.missing_glyphs(name) == set()


# ===========================================================================
# 9) SNIMAK I SAGLASNOST SA STARIM NACRTIMA
# ===========================================================================
LEGACY_3C_FACTS = {
    "report_month": "2026-07", "grade": 6,
    "thinkific": {"available": True, "percent_viewed": 12,
                  "percent_completed": 1, "previous_available": False,
                  "sections": [{"name": "SKUPOVI", "current_percent": 14}]},
    "matbot": {"any_activity": True, "active_days": 2,
               "practice": {"tasks_presented": 37, "answers_total": 24,
                            "correct": 14, "incorrect": 10,
                            "accuracy_percent": 58.3, "hints_used": 6,
                            "full_solutions_shown": 7},
               "explain_count": 19, "quick_count": 1,
               "kontrolni": {"attempts": 4, "average_score_percent": 25.0,
                             "correct_total": 5, "question_total": 20}},
}


def test_old_phase3c_snapshot_still_renders():
    """Snimak bez `instruction` ide STARIM putem — stari nacrt se ne mijenja."""
    pages, text = _text(report_pdf.render_report_pdf(
        LEGACY_3C_FACTS, _narrative(), "Stari komentar.", "Neko Neko"))
    assert pages == 1
    assert "NAPREDAK NA PLATFORMI" in text          # naslijeđeni raspored
    assert "Zadataka prikazano" in text
    assert "RAD NA ČASOVIMA" not in text


def test_new_snapshot_records_the_format_version_and_comments(db, student):
    db.insert_session(student, session(date="2026-08-04", comment="Zapažanje."))
    payload = report_input.build_report_input(student, "2026-08", database=db)
    facts = report_facts.build_ai_facts(payload)
    snapshot = parent_report.metrics_snapshot(
        facts, model="m", prompt_version=report_prompt.REPORT_PROMPT_VERSION,
        parent_comments=payload["instruction"]["parent_comments"])
    assert snapshot["report_format_version"] == parent_report.REPORT_FORMAT_VERSION
    assert snapshot["parent_comments"][0]["comment"] == "Zapažanje."
    assert snapshot["facts"]["instruction"]["sessions_total"] == 1
    # Zapažanja stoje IZVAN činjenica.
    assert "parent_comments" not in json.dumps(snapshot["facts"], ensure_ascii=False)


def test_editing_a_class_record_does_not_mutate_a_saved_report(db, student):
    db.insert_session(student, session(date="2026-08-04", activity=5))
    payload = report_input.build_report_input(student, "2026-08", database=db)
    facts = report_facts.build_ai_facts(payload)
    snapshot = parent_report.metrics_snapshot(facts, model="m", prompt_version="p")
    parent_report.save_narrative(student, "2026-08", _narrative(), snapshot,
                                 database=db)

    # Instruktor kasnije mijenja čas...
    session_id = db.fetch_sessions(student)[0]["id"]
    db.update_session(session_id, student, session(date="2026-08-04", activity=1))

    saved = parent_report.load_saved(student, "2026-08", database=db)
    assert saved["snapshot"]["facts"]["instruction"]["activity_average"] == 5.0


def test_old_snapshot_without_the_new_fields_opens_safely(db, student):
    """Nacrt Faze 3C u bazi mora se otvoriti bez pada."""
    legacy = {"facts": LEGACY_3C_FACTS,
              "generated_by": {"model": "m", "prompt_version": "3c-1"}}
    parent_report.save_narrative(student, "2026-07", _narrative(), legacy,
                                 database=db)
    saved = parent_report.load_saved(student, "2026-07", database=db)
    assert saved["parent_comments"] == []
    assert saved["report_format_version"] is None
    pages, _ = _text(report_pdf.render_report_pdf(
        saved["snapshot"]["facts"], saved["narrative"],
        saved["instructor_comment"], "Neko", saved["parent_comments"]))
    assert pages == 1


# ===========================================================================
# 10) PROMPT
# ===========================================================================
def test_prompt_version_is_3d():
    assert report_prompt.REPORT_PROMPT_VERSION == "3d-2"


def test_prompt_states_the_source_priority():
    prompt = report_prompt.SYSTEM_PROMPT
    assert "PRIORITET IZVORA" in prompt
    assert prompt.index("RAD NA ČASOVIMA") < prompt.index("SAMOSTALAN RAD U MAT-BOT-U")


def test_prompt_forbids_treating_class_data_as_knowledge():
    prompt = report_prompt.SYSTEM_PROMPT
    # 3d-2: metrika se opisuje onim STO JEST, pa se rijec „ocjena" vise
    # ne pojavljuje ni u opisu ni u porici — samo u samoj zabrani.
    assert "ANGAŽMAN NA ČASU" in prompt
    assert "Zabrana važi I U PORICANJU" in prompt
    assert "RADNA NAVIKA" in prompt
    assert "činjenica, nikad moralni sud" in prompt
    assert "NISKA ZAVRŠENOST KURSA SAMA PO SEBI NIJE" in prompt
