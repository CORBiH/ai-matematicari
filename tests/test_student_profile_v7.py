"""Account type, multi-grade profiles and atomic admin bulk updates (schema v7).

All identities and names in this module are synthetic.
"""
import re

import pytest
from werkzeug.datastructures import MultiDict

from matbot import (admin_reports, admin_sessions, parent_report, report_facts,
                    report_input, reporting_db, reporting_schema,
                    student_grade_audit, student_grades,
                    thinkific_grade_forensics)
from matbot.student_identity import PROVIDER_THINKIFIC_EMAIL
from tests.test_thinkific_progress_import import build_v1, migrate


libsql = pytest.importorskip("libsql")
PASSWORD = "synthetic-admin-password-123"


@pytest.fixture(autouse=True)
def fresh_login_limiter(flask_app):
    from matbot.admin_reports import LOGIN_LIMITER_KEY

    flask_app.config.pop(LOGIN_LIMITER_KEY, None)
    yield
    flask_app.config.pop(LOGIN_LIMITER_KEY, None)


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
        connect_factory=lambda: libsql.connect(
            path, timeout=10.0, _check_same_thread=False))
    reporting_db.set_database(database)
    yield database
    reporting_db.wait_for_pending_writes()
    reporting_db.set_database(None)


def _csrf(response):
    match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
    assert match
    return match.group(1).decode()


@pytest.fixture
def admin(client, db, monkeypatch):
    monkeypatch.setenv("MATBOT_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.setenv("MATBOT_ADMIN_COOKIE_SECURE", "disabled")
    token = _csrf(client.get("/admin/reports/login"))
    response = client.post("/admin/reports/login", data={
        "csrf_token": token, "password": PASSWORD})
    assert response.status_code == 302
    return client


def _identity(db, suffix):
    return db.get_or_create_student(
        PROVIDER_THINKIFIC_EMAIL, "%s@example.test" % suffix,
        "Sintetički %s" % suffix)


def _bulk_form(token, rows):
    values = [("csrf_token", token)]
    for student_id, account_type, grades in rows:
        values.append(("student_ids", str(student_id)))
        values.append(("account_type_%d" % student_id, account_type))
        values.extend(("grades_%d" % student_id, str(grade))
                      for grade in grades)
    return MultiDict(values)


def _migrate_through_v6(path):
    build_v1(path)
    conn = libsql.connect(path)
    reporting_schema.migrate_to_v2(conn)
    reporting_schema.migrate_to_v3(conn)
    reporting_schema.migrate_to_v4(conn)
    reporting_schema.migrate_to_v5(conn)
    reporting_schema.migrate_to_v6(conn)
    return conn


def _database_for(path):
    return reporting_db.ReportingDatabase(
        connect_factory=lambda: libsql.connect(
            path, timeout=10.0, _check_same_thread=False))


def _add_expected_v7_account_type(conn):
    conn.execute("ALTER TABLE students ADD COLUMN %s %s"
                 % reporting_schema.V7_ACCOUNT_TYPE_COLUMN)
    conn.commit()


def test_existing_single_grade_student_still_works(db):
    student_id = db.create_student("Jedan Razred", 6)

    profile = db.fetch_student_profile(student_id)
    payload = report_input.build_report_input(student_id, "2026-09", database=db)
    facts = report_facts.build_ai_facts(payload)

    assert profile["account_type"] == reporting_db.ACCOUNT_TYPE_STUDENT
    assert profile["grades"] == [6]
    assert payload["profile"]["grade_confirmed"] is True
    assert facts["grade"] == 6
    assert facts["grades"] == [6]
    assert facts["shared_account"] is False


def test_bulk_confirm_saves_multiple_selected_students(admin, db):
    first, second = _identity(db, "bulk-a"), _identity(db, "bulk-b")
    token = _csrf(admin.get("/admin/students"))

    response = admin.post(
        "/admin/students/bulk-confirm",
        data=_bulk_form(token, [
            (first, "STUDENT", [7]), (second, "STUDENT", [7])]),
        follow_redirects=True)

    assert response.status_code == 200
    assert "Potvrđeno 2 učenika.".encode() in response.data
    assert db.fetch_student_profile(first)["grades"] == [7]
    assert db.fetch_student_profile(second)["grades"] == [7]


def test_bulk_confirm_uses_each_rows_own_grades(admin, db):
    first, second = _identity(db, "different-a"), _identity(db, "different-b")
    token = _csrf(admin.get("/admin/students"))

    admin.post("/admin/students/bulk-confirm", data=_bulk_form(token, [
        (first, "STUDENT", [6]), (second, "STUDENT", [8, 9])]))

    assert db.fetch_student_profile(first)["grades"] == [6]
    assert db.fetch_student_profile(second)["grades"] == [8, 9]


@pytest.mark.parametrize("account_type", ["SUPPORT", "TEST"])
def test_special_account_is_blocked_before_report_generation(
        admin, db, monkeypatch, account_type):
    student_id = db.create_student("Poseban Nalog", 7)
    db.set_student_account_types([student_id], account_type)
    calls = []

    class NoExternalLLM:
        pass

    from matbot import llm
    monkeypatch.setattr(llm, "OpenAIPracticeLLM", NoExternalLLM)
    monkeypatch.setattr(
        parent_report, "generate_narrative",
        lambda *args, **kwargs: calls.append(True))
    token = _csrf(admin.get("/admin/students"))

    response = admin.post(
        "/admin/reports/student/%d/generate?month=2026-09" % student_id,
        data={"csrf_token": token})

    assert response.status_code == 200
    assert calls == []
    assert admin_reports.ERROR_REPORT_DISABLED.encode() in response.data
    assert db.fetch_monthly_report(student_id, "2026-09") is None


def test_student_still_generates_and_saves_one_report(admin, db, monkeypatch):
    student_id = db.create_student("Obični Učenik", 8)
    calls = []

    class NoExternalLLM:
        pass

    from matbot import llm
    monkeypatch.setattr(llm, "OpenAIPracticeLLM", NoExternalLLM)

    def generated(facts, model):
        calls.append(facts)
        return {"summary": "Sažetak", "strengths": [], "focus_areas": [],
                "next_month_recommendations": []}

    monkeypatch.setattr(parent_report, "generate_narrative", generated)
    token = _csrf(admin.get("/admin/students"))
    response = admin.post(
        "/admin/reports/student/%d/generate?month=2026-09" % student_id,
        data={"csrf_token": token})

    assert response.status_code == 302
    assert len(calls) == 1
    assert db.fetch_monthly_report(student_id, "2026-09") is not None


def test_student_support_student_transition_changes_only_eligibility(db):
    student_id = db.create_student("Povratni Nalog", 7)
    assert parent_report.build_facts(student_id, "2026-09", database=db)

    db.set_student_account_types([student_id], "SUPPORT")
    with pytest.raises(parent_report.ReportEligibilityError):
        parent_report.build_facts(student_id, "2026-09", database=db)
    assert db.fetch_student_profile(student_id)["grades"] == [7]

    db.set_student_account_types([student_id], "STUDENT")
    payload, facts = parent_report.build_facts(
        student_id, "2026-09", database=db)
    assert payload["profile"]["reporting_enabled"] is True
    assert facts["grades"] == [7]


def test_one_student_can_have_two_current_grades(db):
    student_id = db.create_student("Zajednički Nalog", 7)
    assert db.set_student_grades(student_id, [9, 7]) is True

    profile = db.fetch_student_profile(student_id)
    listed = db.list_students(grade=9)

    assert profile["grades"] == [7, 9]
    assert profile["grade"] is None  # legacy čitači ne smiju birati proizvoljno
    assert [row["student_id"] for row in listed] == [student_id]


def test_more_than_two_grades_is_rejected_before_writing(db):
    student_id = db.create_student("Previše Razreda", 6)

    with pytest.raises(reporting_db.ReportingUnavailable) as error:
        db.set_student_grades(student_id, [6, 7, 8])

    assert error.value.code == "student_grade_count_invalid"
    assert db.fetch_student_profile(student_id)["grades"] == [6]


def test_duplicate_grade_is_rejected_before_writing(db):
    student_id = db.create_student("Dupli Razred", 7)

    with pytest.raises(reporting_db.ReportingUnavailable) as error:
        db.set_student_grades(student_id, [7, 7])

    assert error.value.code == "student_grade_duplicate"
    assert db.fetch_student_profile(student_id)["grades"] == [7]


def test_v7_migration_backfills_only_confirmed_legacy_grade_and_keeps_report(
        tmp_path):
    path = str(tmp_path / "legacy-v6.db")
    build_v1(path)
    conn = libsql.connect(path)
    reporting_schema.migrate_to_v2(conn)
    reporting_schema.migrate_to_v3(conn)
    reporting_schema.migrate_to_v4(conn)
    reporting_schema.migrate_to_v5(conn)
    reporting_schema.migrate_to_v6(conn)
    conn.execute("DROP TABLE IF EXISTS monthly_reports")
    conn.execute(reporting_schema.MONTHLY_REPORTS_DDL)
    confirmed = conn.execute(
        "INSERT INTO students (display_name, grade, grade_confirmed_at, "
        "grade_source, created_at, updated_at, last_seen_at) VALUES "
        "('Legacy Confirmed', 8, '2026-09-01 10:00:00', 'admin', "
        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)").lastrowid
    pending = conn.execute(
        "INSERT INTO students (display_name, grade, created_at, updated_at, "
        "last_seen_at) VALUES ('Legacy Pending', 6, CURRENT_TIMESTAMP, "
        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)").lastrowid
    conn.execute(
        "INSERT INTO monthly_reports (student_id, report_month, status, "
        "ai_summary, created_at, updated_at) VALUES (?, '2026-08', 'draft', "
        "'legacy-report', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)", (confirmed,))
    conn.commit()

    assert reporting_schema.migrate_to_v7(conn) is True
    grades = conn.execute(
        "SELECT student_id, grade FROM student_current_grades "
        "ORDER BY student_id").fetchall()
    types = conn.execute(
        "SELECT id, account_type FROM students ORDER BY id").fetchall()
    saved = conn.execute(
        "SELECT ai_summary FROM monthly_reports WHERE student_id = ?",
        (confirmed,)).fetchall()

    assert grades == [(confirmed, 8)]
    assert dict(types) == {confirmed: "STUDENT", pending: "STUDENT"}
    assert saved == [("legacy-report",)]
    assert reporting_schema.verify_v7_schema(conn) == []
    conn.close()


def test_v7_resumes_exact_production_partial_state_and_is_idempotent(tmp_path):
    """Produkcija 2026-09-21: v1..v6 + validan account_type, bez grade tabele."""
    path = str(tmp_path / "production-partial-v7.db")
    conn = _migrate_through_v6(path)
    student_id = conn.execute(
        "INSERT INTO students (display_name, grade, grade_confirmed_at, "
        "grade_source, created_at, updated_at, last_seen_at) VALUES "
        "('Synthetic Partial', 8, '2026-09-20 10:11:12', 'admin', "
        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)").lastrowid
    conn.commit()
    _add_expected_v7_account_type(conn)

    account_sql_before = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'students'"
    ).fetchall()[0][0]
    assert reporting_schema.applied_versions(conn) == set(range(1, 7))
    assert "student_current_grades" not in reporting_schema.table_names(conn)
    assert [row[1] for row in conn.execute(
        "PRAGMA table_info(students)").fetchall()].count("account_type") == 1

    assert reporting_schema.migrate_to_v7(conn) is True
    assert reporting_schema.verify_v7_schema(conn) == []
    assert conn.execute(
        "SELECT student_id, grade, source FROM student_current_grades"
    ).fetchall() == [(student_id, 8, "admin")]
    assert conn.execute(
        "SELECT account_type FROM students WHERE id = ?", (student_id,)
    ).fetchall() == [("STUDENT",)]
    assert conn.execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE version = 7"
    ).fetchall() == [(1,)]
    assert conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'students'"
    ).fetchall()[0][0] == account_sql_before

    assert reporting_schema.migrate_to_v7(conn) is False
    assert conn.execute(
        "SELECT student_id, grade, source FROM student_current_grades"
    ).fetchall() == [(student_id, 8, "admin")]
    assert conn.execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE version = 7"
    ).fetchall() == [(1,)]
    assert [row[1] for row in conn.execute(
        "PRAGMA table_info(students)").fetchall()].count("account_type") == 1
    conn.close()


def test_v7_grade_table_failure_reports_exact_stage_and_leaves_version_unrecorded(
        tmp_path):
    path = str(tmp_path / "v7-grade-table-failure.db")
    conn = _migrate_through_v6(path)
    _add_expected_v7_account_type(conn)

    class RejectGradeTable:
        def execute(self, statement, *args):
            if "CREATE TABLE IF NOT EXISTS student_current_grades" in statement:
                raise RuntimeError("synthetic remote DDL rejection")
            return conn.execute(statement, *args)

        def __getattr__(self, name):
            return getattr(conn, name)

    with pytest.raises(reporting_schema.MigrationError) as caught:
        reporting_schema.migrate_to_v7(RejectGradeTable())

    assert caught.value.code == "v7_ddl_failed"
    assert caught.value.stage == "create_student_current_grades"
    assert caught.value.exception_class == "RuntimeError"
    assert caught.value.safe_detail == \
        "database operation failed; raw detail withheld"
    assert reporting_schema.SCHEMA_VERSION_V7 not in \
        reporting_schema.applied_versions(conn)
    assert "student_current_grades" not in reporting_schema.table_names(conn)
    conn.close()


def test_v7_resumes_when_grade_table_exists_but_index_is_absent(tmp_path):
    path = str(tmp_path / "partial-v7-no-index.db")
    conn = _migrate_through_v6(path)
    _add_expected_v7_account_type(conn)
    conn.execute(reporting_schema.SCHEMA_V7_STATEMENTS[0])
    conn.commit()
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' "
        "AND name = 'idx_student_current_grades_grade'"
    ).fetchall() == []

    assert reporting_schema.migrate_to_v7(conn) is True
    assert reporting_schema.verify_v7_schema(conn) == []
    assert conn.execute(
        "PRAGMA index_info(idx_student_current_grades_grade)"
    ).fetchall() == [(0, 1, "grade"), (1, 0, "student_id")]
    assert reporting_schema.migrate_to_v7(conn) is False
    conn.close()


def test_v7_records_version_for_complete_unrecorded_schema_once(tmp_path):
    path = str(tmp_path / "partial-v7-complete-unrecorded.db")
    conn = _migrate_through_v6(path)
    _add_expected_v7_account_type(conn)
    for statement in reporting_schema.SCHEMA_V7_STATEMENTS:
        conn.execute(statement)
    conn.commit()
    assert reporting_schema.verify_v7_schema(conn) == []
    assert reporting_schema.SCHEMA_VERSION_V7 not in \
        reporting_schema.applied_versions(conn)

    assert reporting_schema.migrate_to_v7(conn) is True
    assert conn.execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE version = 7"
    ).fetchall() == [(1,)]
    assert reporting_schema.migrate_to_v7(conn) is False
    assert conn.execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE version = 7"
    ).fetchall() == [(1,)]
    conn.close()


def test_shared_account_report_is_one_combined_account_level_report(db):
    student_id = db.create_student("Porodični Nalog", 7)
    db.set_student_grades(student_id, [7, 9])
    conn = db._connection()
    conn.execute(
        "INSERT INTO learning_activity (student_id, source, event_type, "
        "event_key, grade, occurred_at) VALUES "
        "(?, 'matbot', 'practice_task_presented', 'shared-1', 7, "
        "'2026-09-10 10:00:00')", (student_id,))
    conn.commit()

    payload = report_input.build_report_input(
        student_id, "2026-09", database=db)
    facts = report_facts.build_ai_facts(payload)
    population = db.fetch_report_population(
        "2026-09-01 00:00:00", "2026-10-01 00:00:00", "2026-09")

    assert population == [student_id]
    assert facts["grades"] == [7, 9]
    assert facts["grade"] is None
    assert facts["shared_account"] is True
    assert facts["activity_attribution"] == "shared_account_combined"
    assert facts["matbot"]["practice"]["tasks_presented"] == 1


def test_invalid_bulk_request_writes_nothing(admin, db):
    first, second = _identity(db, "invalid-a"), _identity(db, "invalid-b")
    token = _csrf(admin.get("/admin/students"))

    response = admin.post(
        "/admin/students/bulk-confirm",
        data=_bulk_form(token, [
            (first, "STUDENT", [7]), (second, "STUDENT", [])]),
        follow_redirects=True)

    assert response.status_code == 200
    assert ("Neispravni ili nepotpuni podaci za učenike: #%d" % second
            ).encode() in response.data
    assert db.fetch_student_profile(first)["grades"] == []
    assert db.fetch_student_profile(second)["grades"] == []


def test_bulk_database_failure_rolls_back_earlier_rows(db):
    first = db.create_student("Atomski Prvi", 6)
    second = db.create_student("Atomski Drugi", 7)
    conn = db._connection()
    conn.execute(
        "CREATE TRIGGER reject_second_type BEFORE UPDATE OF account_type "
        "ON students WHEN NEW.id = %d BEGIN "
        "SELECT RAISE(ABORT, 'synthetic failure'); END" % second)
    conn.commit()

    with pytest.raises(reporting_db.ReportingUnavailable):
        db.set_student_account_types([first, second], "SUPPORT")

    assert db.fetch_student_profile(first)["account_type"] == "STUDENT"
    assert db.fetch_student_profile(second)["account_type"] == "STUDENT"


def test_bulk_account_type_does_not_change_existing_grades(admin, db):
    first = db.create_student("Tip Prvi", 6)
    second = db.create_student("Tip Drugi", 8)
    token = _csrf(admin.get("/admin/students"))
    response = admin.post("/admin/students/bulk-account-type", data=MultiDict([
        ("csrf_token", token), ("student_ids", str(first)),
        ("student_ids", str(second)), ("bulk_account_type", "TEST")]))

    assert response.status_code == 302
    assert db.fetch_student_profile(first)["account_type"] == "TEST"
    assert db.fetch_student_profile(first)["grades"] == [6]
    assert db.fetch_student_profile(second)["account_type"] == "TEST"
    assert db.fetch_student_profile(second)["grades"] == [8]


def test_single_row_save_still_works(admin, db):
    student_id = _identity(db, "single-row")
    token = _csrf(admin.get("/admin/students"))

    response = admin.post(
        "/admin/students/%d/grade" % student_id,
        data=MultiDict([
            ("csrf_token", token), ("next", "index"),
            ("account_type_%d" % student_id, "STUDENT"),
            ("grades_%d" % student_id, "8")]))

    assert response.status_code == 302
    assert db.fetch_student_profile(student_id)["grades"] == [8]


def test_special_accounts_are_filtered_from_report_population_and_counts(db):
    normal = db.create_student("Normalni", 6)
    support = db.create_student("Podrška", 6)
    test = db.create_student("Testni", 6)
    db.set_student_account_types([support], "SUPPORT")
    db.set_student_account_types([test], "TEST")
    conn = db._connection()
    for student_id in (normal, support, test):
        conn.execute(
            "INSERT INTO learning_activity (student_id, source, event_type, "
            "event_key, occurred_at) VALUES (?, 'matbot', "
            "'practice_task_presented', ?, '2026-09-12 10:00:00')",
            (student_id, "population-%d" % student_id))
    conn.commit()

    assert db.fetch_report_population(
        "2026-09-01 00:00:00", "2026-10-01 00:00:00", "2026-09") == [normal]
    assert [row["student_id"] for row in
            db.list_students(account_type="SUPPORT")] == [support]
    assert [row["student_id"] for row in
            db.list_students(account_type="TEST")] == [test]


def test_registry_filters_types_and_exposes_select_all(admin, db):
    normal = db.create_student("Filter Normalni", 6)
    support = db.create_student("Filter Podrška", 7)
    db.set_student_account_types([support], "SUPPORT")

    page = admin.get("/admin/students?account_type=SUPPORT")

    assert page.status_code == 200
    assert b'id="select-visible"' in page.data
    assert b'name="confirmed"' in page.data
    assert b'name="needs_action"' in page.data
    assert "Filter Podrška".encode() in page.data
    assert "Filter Normalni".encode() not in page.data
    assert ("/admin/students/%d" % support).encode() in page.data
    assert ("/admin/students/%d" % normal).encode() not in page.data
    assert "Izvještaj isključen".encode() in page.data


def test_shared_account_is_explicit_in_profile_and_report_ui(admin, db):
    student_id = db.create_student("UI Zajednički", 7)
    db.set_student_grades(student_id, [7, 9])

    profile = admin.get("/admin/students/%d" % student_id)
    report = admin.get(
        "/admin/reports/student/%d?month=2026-09" % student_id)

    assert "7. razred + 9. razred".encode() in profile.data
    assert "Upiši čas za 7. razred".encode() in profile.data
    assert "Upiši čas za 9. razred".encode() in profile.data
    assert "zajedničkom Thinkific nalogu".encode() in report.data
    assert "ne dupliraju niti razdvajaju".encode() in report.data


@pytest.mark.parametrize("account_type", ["SUPPORT", "TEST"])
def test_database_refuses_report_writes_for_special_accounts(db, account_type):
    student_id = db.create_student("Bez Izvještaja", 9)
    db.set_student_account_types([student_id], account_type)

    with pytest.raises(reporting_db.ReportingUnavailable) as error:
        db.save_monthly_report(
            student_id=student_id, report_month="2026-09",
            ai_summary='{"summary":"must not persist"}')

    assert error.value.code == "student_report_disabled"
    assert db.fetch_monthly_report(student_id, "2026-09") is None


@pytest.mark.parametrize(("confirmed_at", "source", "promoted"), [
    (None, "admin", False),
    ("", "admin", False),
    ("   ", "admin", False),
    ("2026-09-20 10:11:12", "admin", True),
    ("2026-09-20 10:11:12", "", False),
    ("2026-09-20 10:11:12", "   ", False),
], ids=["null-time", "empty-time", "space-time", "valid",
        "empty-source", "space-source"])
def test_v7_backfill_requires_meaningful_legacy_confirmation(
        tmp_path, confirmed_at, source, promoted):
    path = str(tmp_path / ("v7-backfill-%s.db" % source.strip() or "blank"))
    conn = _migrate_through_v6(path)
    student_id = conn.execute(
        "INSERT INTO students (display_name, grade, grade_confirmed_at, "
        "grade_source, created_at, updated_at, last_seen_at) VALUES "
        "('Legacy', 8, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
        "CURRENT_TIMESTAMP)", (confirmed_at, source)).lastrowid
    conn.commit()

    assert reporting_schema.migrate_to_v7(conn) is True
    rows = conn.execute(
        "SELECT grade FROM student_current_grades WHERE student_id = ?",
        (student_id,)).fetchall()

    assert rows == ([(8,)] if promoted else [])
    conn.close()


def test_v7_old_app_legacy_write_is_consistent_in_every_new_reader(tmp_path):
    path = str(tmp_path / "rolling-deploy.db")
    conn = _migrate_through_v6(path)
    student_id = conn.execute(
        "INSERT INTO students (display_name, grade, created_at, updated_at, "
        "last_seen_at) VALUES ('Rolling Deploy', 6, CURRENT_TIMESTAMP, "
        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)").lastrowid
    conn.commit()
    assert reporting_schema.migrate_to_v7(conn) is True

    # Stari proces, koji jos nema v7 kod, potvrdi samo legacy trojku.
    conn.execute(
        "UPDATE students SET grade = 9, "
        "grade_confirmed_at = '2026-09-20 10:11:12', "
        "grade_source = 'admin' WHERE id = ?", (student_id,))
    conn.commit()
    conn.close()
    database = _database_for(path)

    assert database.fetch_student_profile(student_id)["grades"] == [9]
    assert database.list_students()[0]["grades"] == [9]
    assert [row["student_id"] for row in
            database.list_students(confirmed=True)] == [student_id]
    assert database.list_students(confirmed=False) == []
    assert [row["student_id"] for row in
            database.list_students(grade=9)] == [student_id]
    roster, unconfirmed = admin_sessions.class_roster(database, 9)
    assert [row["student_id"] for row in roster] == [student_id]
    assert unconfirmed == 0
    assert student_grade_audit.collect(database)[0]["status"] == \
        student_grades.STATUS_CONFIRMED
    assert thinkific_grade_forensics.collect(database)["students"][0][
        "grade_status"] == student_grades.STATUS_CONFIRMED


def test_v7_invalid_legacy_scalar_stays_unconfirmed(db):
    student_id = db.create_student("Invalid Legacy", 8)
    conn = db._connection()
    conn.execute("DELETE FROM student_current_grades WHERE student_id = ?",
                 (student_id,))
    conn.execute(
        "UPDATE students SET grade = 9, grade_confirmed_at = '   ', "
        "grade_source = 'admin' WHERE id = ?", (student_id,))
    conn.commit()

    assert db.fetch_student_profile(student_id)["grades"] == []
    assert db.list_students()[0]["grades"] == []
    assert db.list_students(confirmed=True) == []
    assert [row["student_id"] for row in
            db.list_students(confirmed=False)] == [student_id]
    assert db.list_students(grade=9) == []
    roster, unconfirmed = admin_sessions.class_roster(db, 9)
    assert roster == []
    assert unconfirmed == 1


def test_v7_normalized_rows_win_and_two_grades_never_collapse(db):
    student_id = db.create_student("Normalized Wins", 7)
    db.set_student_grades(student_id, [7, 9])
    conn = db._connection()
    conn.execute(
        "UPDATE students SET grade = 6, "
        "grade_confirmed_at = '2026-09-20 10:11:12', "
        "grade_source = 'admin' WHERE id = ?", (student_id,))
    conn.commit()

    assert db.fetch_student_profile(student_id)["grades"] == [7, 9]
    assert db.list_students()[0]["grades"] == [7, 9]
    assert db.list_students(grade=6) == []
    assert [row["student_id"] for row in db.list_students(grade=7)] == [student_id]
    assert [row["student_id"] for row in db.list_students(grade=9)] == [student_id]


@pytest.mark.parametrize("grades", [[7], [9], [7, 9]])
def test_profile_diagnostics_classify_authoritative_grade_sets(db, grades):
    student_id = db.create_student("Diagnostic Profile", grades[0])
    db.set_student_grades(student_id, grades)

    audit_row = student_grade_audit.collect(db)[0]
    forensic = thinkific_grade_forensics.collect(db)
    forensic_row = forensic["students"][0]

    assert audit_row["current_grades"] == grades
    assert audit_row["status"] == student_grades.STATUS_CONFIRMED
    assert forensic_row["current_grades"] == grades
    assert forensic_row["grade_status"] == student_grades.STATUS_CONFIRMED
    label = "+".join(map(str, grades))
    assert label in student_grade_audit.format_report([audit_row])
    assert ("grades=%-5s status=CONFIRMED" % label
            ) in thinkific_grade_forensics.format_report(forensic)


def test_shared_profile_has_no_scalar_unconfirmed_warning(admin, db):
    student_id = db.create_student("Shared Classification", 7)
    db.set_student_grades(student_id, [7, 9])

    response = admin.get("/admin/students/%d" % student_id)

    assert response.status_code == 200
    assert "7. razred + 9. razred".encode() in response.data
    assert "Trenutni razred nije potvrđen".encode() not in response.data


def test_genuinely_unconfirmed_profile_keeps_warning(admin, db):
    student_id = _identity(db, "unconfirmed-profile")

    response = admin.get("/admin/students/%d" % student_id)

    assert response.status_code == 200
    assert "Trenutni razred nije potvrđen".encode() in response.data


def _install_malformed_v7(conn, defect):
    account_decl = reporting_schema.V7_ACCOUNT_TYPE_COLUMN[1]
    grade_decl = "INTEGER NOT NULL CHECK (grade BETWEEN 6 AND 9)"
    source_decl = (
        "TEXT NOT NULL CHECK (source IN ('admin', 'manual_creation'))")
    primary = "PRIMARY KEY (student_id, grade)"
    foreign = (
        "FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE")
    index_columns = "grade, student_id"
    if defect == "account_check_missing":
        account_decl = "TEXT NOT NULL DEFAULT 'STUDENT'"
    elif defect == "account_check_permissive":
        account_decl = (
            "TEXT NOT NULL DEFAULT 'STUDENT' CHECK (account_type IN "
            "('STUDENT', 'SUPPORT', 'TEST', 'ARBITRARY'))")
    elif defect == "grade_check_missing":
        grade_decl = "INTEGER NOT NULL"
    elif defect == "grade_check_permissive":
        grade_decl = "INTEGER NOT NULL CHECK (grade BETWEEN 6 AND 99)"
    elif defect == "source_check_missing":
        source_decl = "TEXT NOT NULL"
    elif defect == "source_check_permissive":
        source_decl = (
            "TEXT NOT NULL CHECK (source IN "
            "('admin', 'manual_creation', 'automatic'))")
    elif defect == "index_shape":
        index_columns = "student_id, grade"
    elif defect == "foreign_key":
        foreign = None
    elif defect == "unique_shape":
        primary = "PRIMARY KEY (student_id)"
    conn.execute("ALTER TABLE students ADD COLUMN account_type %s" % account_decl)
    constraints = [primary]
    if foreign:
        constraints.append(foreign)
    conn.execute(
        "CREATE TABLE student_current_grades ("
        "student_id INTEGER NOT NULL, grade %s, "
        "confirmed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, source %s, %s)"
        % (grade_decl, source_decl, ", ".join(constraints)))
    conn.execute(
        "CREATE INDEX idx_student_current_grades_grade "
        "ON student_current_grades (%s)" % index_columns)
    conn.commit()


@pytest.mark.parametrize(("defect", "problem"), [
    ("account_check_missing", "v7_account_type_check:students"),
    ("account_check_permissive", "v7_account_type_check:students"),
    ("grade_check_missing", "v7_grade_check:student_current_grades"),
    ("grade_check_permissive", "v7_grade_check:student_current_grades"),
    ("source_check_missing", "v7_source_check:student_current_grades"),
    ("source_check_permissive", "v7_source_check:student_current_grades"),
    ("index_shape", "v7_index_shape:idx_student_current_grades_grade"),
    ("foreign_key", "v7_foreign_key_missing:student_current_grades"),
    ("unique_shape", "v7_unique_missing:student_current_grades"),
])
def test_v7_verifier_rejects_malformed_existing_objects(
        tmp_path, defect, problem):
    path = str(tmp_path / (defect + ".db"))
    conn = _migrate_through_v6(path)
    _install_malformed_v7(conn, defect)

    assert problem in reporting_schema.verify_v7_schema(conn)
    if defect == "account_check_permissive":
        conn.execute(
            "INSERT INTO students (display_name, account_type, created_at, "
            "updated_at, last_seen_at) VALUES ('Arbitrary', 'ARBITRARY', "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
        conn.rollback()
    if defect == "grade_check_permissive":
        student_id = conn.execute(
            "INSERT INTO students (display_name, created_at, updated_at, "
            "last_seen_at) VALUES ('Grade 99', CURRENT_TIMESTAMP, "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)").lastrowid
        conn.execute(
            "INSERT INTO student_current_grades "
            "(student_id, grade, source) VALUES (?, 99, 'admin')", (student_id,))
        conn.rollback()
    with pytest.raises(reporting_schema.MigrationError) as error:
        reporting_schema.migrate_to_v7(conn)
    assert error.value.code == problem
    assert reporting_schema.SCHEMA_VERSION_V7 not in \
        reporting_schema.applied_versions(conn)
    conn.close()
