"""Name-only Thinkific backfill: preview, potvrda i stroge DB invarijante."""
import csv
import io
import logging

import pytest

from matbot import reporting_db, student_name_backfill as backfill
from matbot.student_identity import PROVIDER_THINKIFIC_EMAIL
from tests.test_thinkific_progress_import import build_v1, migrate


libsql = pytest.importorskip("libsql")


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = str(tmp_path / "reporting.db")
    build_v1(path)
    migrate(path)
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://name-backfill-test.invalid")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token-not-real")
    database = reporting_db.ReportingDatabase(
        connect_factory=lambda: libsql.connect(
            path, timeout=10.0, _check_same_thread=False))
    yield path, database
    database.close()


def rows(path, sql, params=()):
    conn = libsql.connect(path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def execute(path, sql, params=()):
    conn = libsql.connect(path)
    try:
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def add_student(path, email, *, name=None, provider=PROVIDER_THINKIFIC_EMAIL,
                grade=7):
    conn = libsql.connect(path)
    try:
        cursor = conn.execute(
            "INSERT INTO students (display_name, grade, status, created_at, "
            "updated_at, last_seen_at) VALUES (?, ?, 'active', ?, ?, ?)",
            (name, grade, "2026-01-01 00:00:00", "2026-02-02 00:00:00",
             "2026-03-03 00:00:00"))
        student_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO student_accounts (student_id, provider, "
            "external_user_id, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
            (student_id, provider, email, "2026-01-01 00:00:00",
             "2026-03-03 00:00:00"))
        conn.commit()
        return student_id
    finally:
        conn.close()


def make_csv(entries, *, headers=None):
    headers = headers or ["First Name", "Last Name", "Email"]
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(headers)
    for entry in entries:
        if isinstance(entry, dict):
            writer.writerow([entry.get(header, "") for header in headers])
        else:
            writer.writerow(entry)
    return stream.getvalue().encode("utf-8")


def person(email="student@example.com", first="Ana", last="Anić", **extra):
    value = {"First Name": first, "Last Name": last, "Email": email}
    value.update(extra)
    return value


def plan_for(database, payload):
    source, errors = backfill.parse_csv(payload)
    return backfill.build_plan(source, errors, database)


def run_stdin(database, payload, *args):
    output = io.StringIO()
    code = backfill.run(["--csv", "-", *args], database=database,
                        stdin=io.BytesIO(payload), stdout=output)
    return code, output.getvalue()


@pytest.mark.parametrize("old_name", [None, "", "  \t  "])
def test_null_empty_and_whitespace_names_update_without_timestamps(store, old_name):
    path, database = store
    student_id = add_student(path, "student@example.com", name=old_name)
    before = rows(path, "SELECT grade, created_at, updated_at, last_seen_at "
                        "FROM students WHERE id = ?", (student_id,))[0]
    account_before = rows(path, "SELECT created_at, last_seen_at FROM "
                                "student_accounts WHERE student_id = ?",
                          (student_id,))[0]
    payload = make_csv([person()])
    preview = plan_for(database, payload)

    assert preview.rows[0].classification == backfill.WOULD_UPDATE
    code, output = run_stdin(
        database, payload, "--apply", "--confirm-plan", preview.plan_id)

    assert code == 0 and "updated: 1" in output
    assert rows(path, "SELECT display_name FROM students WHERE id = ?",
                (student_id,)) == [("Ana Anić",)]
    assert rows(path, "SELECT grade, created_at, updated_at, last_seen_at "
                      "FROM students WHERE id = ?", (student_id,))[0] == before
    assert rows(path, "SELECT created_at, last_seen_at FROM student_accounts "
                      "WHERE student_id = ?", (student_id,))[0] == account_before


def test_existing_name_is_never_overwritten_and_second_run_is_noop(store):
    path, database = store
    student_id = add_student(path, "student@example.com", name="Existing Name")
    payload = make_csv([person(first="New", last="Name")])
    preview = plan_for(database, payload)

    assert preview.rows[0].classification == backfill.SKIP_EXISTING_NAME
    assert preview.totals["would_update"] == 0
    code, output = run_stdin(
        database, payload, "--apply", "--confirm-plan", preview.plan_id)

    assert code == 0 and "updated: 0" in output
    assert rows(path, "SELECT display_name FROM students WHERE id = ?",
                (student_id,)) == [("Existing Name",)]


def test_email_is_normalized_and_provider_must_be_thinkific_email(store):
    path, database = store
    matched = add_student(path, "student@example.com", name=None)
    other = add_student(path, "other@example.com", name=None, provider="moodle")
    payload = make_csv([
        person(email="  Student@Example.COM  "),
        person(email="other@example.com", first="Other"),
    ])
    plan = plan_for(database, payload)

    assert plan.rows[0].student_id == matched
    assert plan.rows[0].classification == backfill.WOULD_UPDATE
    assert plan.rows[1].student_id is None
    assert plan.rows[1].classification == backfill.UNMATCHED
    assert rows(path, "SELECT display_name FROM students WHERE id = ?", (other,)) \
        == [(None,)]


def test_unmatched_is_reviewed_noop_and_creates_nothing(store):
    path, database = store
    payload = make_csv([person(email="new@example.com")])
    before = (rows(path, "SELECT COUNT(*) FROM students")[0][0],
              rows(path, "SELECT COUNT(*) FROM student_accounts")[0][0])
    preview = plan_for(database, payload)

    assert preview.rows[0].classification == backfill.UNMATCHED
    code, _ = run_stdin(
        database, payload, "--apply", "--confirm-plan", preview.plan_id)

    assert code == 0
    assert (rows(path, "SELECT COUNT(*) FROM students")[0][0],
            rows(path, "SELECT COUNT(*) FROM student_accounts")[0][0]) == before


@pytest.mark.parametrize(
    "entry,reason",
    [
        (person(email="not-an-email"), "email_invalid"),
        (person(first="", last=""), "name_blank"),
        (person(first="A" * 121, last=""), "name_too_long"),
        (person(first="Ana\x1b", last="Anić"), "name_control_character"),
    ],
)
def test_invalid_rows_block_apply(store, entry, reason):
    _path, database = store
    plan = plan_for(database, make_csv([entry]))

    assert plan.rows[0].classification == backfill.ERROR
    assert plan.rows[0].reason == reason
    assert plan.apply_allowed is False
    assert plan.plan_id is None


@pytest.mark.parametrize(
    "payload,error_code",
    [
        (make_csv([], headers=["First Name", "Email"]),
         "missing_required_header:Last Name"),
        (make_csv([], headers=["First Name", "Last Name", "Email", "Email"]),
         "duplicate_required_header:Email"),
        (b'First Name,Last Name,Email\n"unterminated', "csv_malformed"),
    ],
)
def test_bad_csv_structure_blocks_apply(store, payload, error_code):
    _path, database = store
    plan = plan_for(database, payload)

    assert error_code in plan.file_errors
    assert plan.apply_allowed is False


def test_unrelated_extra_columns_are_ignored(store):
    path, database = store
    student_id = add_student(path, "student@example.com")
    headers = ["First Name", "Progress", "Email", "Last Name", "% Viewed"]
    payload = make_csv([{
        "First Name": "Ana", "Last Name": "Anić",
        "Email": "student@example.com", "Progress": "99", "% Viewed": "88",
    }], headers=headers)
    plan = plan_for(database, payload)

    assert plan.rows[0].classification == backfill.WOULD_UPDATE
    assert plan.rows[0].student_id == student_id


def test_duplicate_normalized_email_blocks_apply(store):
    path, database = store
    add_student(path, "student@example.com")
    payload = make_csv([
        person(email="Student@Example.com"),
        person(email=" student@example.com ", first="Druga"),
    ])
    plan = plan_for(database, payload)

    assert [row.classification for row in plan.rows] == [
        backfill.DUPLICATE, backfill.DUPLICATE]
    assert plan.totals["duplicates"] == 2
    assert plan.apply_allowed is False


def test_distinct_emails_resolving_to_same_student_block_apply(store):
    path, database = store
    student_id = add_student(path, "first@example.com")
    execute(path, "INSERT INTO student_accounts (student_id, provider, "
                  "external_user_id) VALUES (?, ?, ?)",
            (student_id, PROVIDER_THINKIFIC_EMAIL, "second@example.com"))
    payload = make_csv([
        person(email="first@example.com", first="Ana"),
        person(email="second@example.com", first="Different"),
    ])
    plan = plan_for(database, payload)

    assert all(row.classification == backfill.DUPLICATE for row in plan.rows)
    assert all(row.reason == "conflicting_target_student" for row in plan.rows)
    assert plan.apply_allowed is False


def test_default_cli_is_read_only_and_hides_raw_email_from_output_and_logs(
        store, caplog):
    path, database = store
    add_student(path, "student@example.com")
    payload = make_csv([person()])
    before = rows(path, "SELECT * FROM students")
    caplog.set_level(logging.INFO, logger="matbot.student_name_backfill")

    code, output = run_stdin(database, payload)

    assert code == 0
    assert "WOULD_UPDATE" in output and "s***@example.com" in output
    assert "student@example.com" not in output
    assert "student@example.com" not in caplog.text
    assert "Ana Anić" not in caplog.text
    assert rows(path, "SELECT * FROM students") == before


@pytest.mark.parametrize("arguments", [
    ("--apply",),
    ("--apply", "--confirm-plan", "wrong-plan"),
])
def test_missing_or_wrong_confirmation_performs_zero_writes(store, arguments):
    path, database = store
    add_student(path, "student@example.com")
    payload = make_csv([person()])
    before = rows(path, "SELECT * FROM students")

    code, output = run_stdin(database, payload, *arguments)

    assert code == 2 and "apply refused" in output
    assert rows(path, "SELECT * FROM students") == before


def test_stale_preview_is_refused_before_any_backfill_write(store):
    path, database = store
    student_id = add_student(path, "student@example.com")
    payload = make_csv([person()])
    old_plan = plan_for(database, payload)
    execute(path, "UPDATE students SET display_name = 'Concurrent Name' WHERE id = ?",
            (student_id,))

    code, output = run_stdin(
        database, payload, "--apply", "--confirm-plan", old_plan.plan_id)

    assert code == 2 and "plan_mismatch" in output
    assert rows(path, "SELECT display_name FROM students WHERE id = ?",
                (student_id,)) == [("Concurrent Name",)]


def test_concurrent_name_change_rolls_back_the_entire_batch(store, monkeypatch):
    path, database = store
    first = add_student(path, "first@example.com")
    second = add_student(path, "second@example.com")
    payload = make_csv([
        person(email="first@example.com", first="First"),
        person(email="second@example.com", first="Second"),
    ])
    preview = plan_for(database, payload)
    original = database.apply_student_display_names

    def race(changes):
        execute(path, "UPDATE students SET display_name = 'Concurrent' WHERE id = ?",
                (second,))
        return original(changes)

    monkeypatch.setattr(database, "apply_student_display_names", race)
    code, output = run_stdin(
        database, payload, "--apply", "--confirm-plan", preview.plan_id)

    assert code == 1 and "name_backfill_guard_failed" in output
    assert rows(path, "SELECT id, display_name FROM students ORDER BY id") == [
        (first, None), (second, "Concurrent")]


def test_database_failure_midway_rolls_back_all_names(store):
    path, database = store
    first = add_student(path, "first@example.com")
    second = add_student(path, "second@example.com")
    execute(path, "CREATE TRIGGER fail_second_name BEFORE UPDATE OF display_name "
                  "ON students WHEN NEW.id = %d BEGIN "
                  "SELECT RAISE(ABORT, 'synthetic failure'); END" % second)
    changes = [
        {"student_id": first, "external_user_id": "first@example.com",
         "expected_display_name": None, "display_name": "First Name"},
        {"student_id": second, "external_user_id": "second@example.com",
         "expected_display_name": None, "display_name": "Second Name"},
    ]

    with pytest.raises(reporting_db.ReportingUnavailable) as caught:
        database.apply_student_display_names(changes)

    assert caught.value.code.startswith("name_backfill_apply_failed:")
    assert rows(path, "SELECT id, display_name FROM students ORDER BY id") == [
        (first, None), (second, None)]


def _table_dump(path, table):
    return rows(path, "SELECT * FROM " + table + " ORDER BY rowid")


def test_apply_changes_only_students_display_name(store):
    path, database = store
    student_id = add_student(path, "student@example.com")
    conn = libsql.connect(path)
    try:
        conn.execute(
            "INSERT INTO learning_activity (student_id, source, event_type, "
            "event_key) VALUES (?, 'matbot', 'practice_task_presented', 'event-1')",
            (student_id,))
        attempt = conn.execute(
            "INSERT INTO assessment_attempts (student_id, source, "
            "assessment_type, external_attempt_id) VALUES "
            "(?, 'matbot', 'kontrolni', 'attempt-1')", (student_id,)).lastrowid
        conn.execute(
            "INSERT INTO assessment_item_results (attempt_id, item_key) "
            "VALUES (?, 'item-1')", (attempt,))
        imported = conn.execute(
            "INSERT INTO thinkific_progress_imports (report_month, course_key, "
            "course_name, grade, source_sha256, row_count) "
            "VALUES ('2026-08', 'grade_7', 'Matematika 7', 7, 'digest', 1)").lastrowid
        snapshot = conn.execute(
            "INSERT INTO thinkific_progress_snapshots (import_id, student_id, "
            "report_month, course_key, course_name, grade) "
            "VALUES (?, ?, '2026-08', 'grade_7', 'Matematika 7', 7)",
            (imported, student_id)).lastrowid
        conn.execute(
            "INSERT INTO thinkific_progress_sections (snapshot_id, ordinal, "
            "section_name, progress_percent) VALUES (?, 1, 'Razlomci', 50)",
            (snapshot,))
        conn.execute(
            "INSERT INTO student_sessions (student_id, session_date, attendance, "
            "homework_status) VALUES (?, '2026-08-10', 'present', 'done')",
            (student_id,))
        conn.execute("INSERT INTO monthly_reports (id) VALUES (41)")
        conn.commit()
    finally:
        conn.close()

    untouched = (
        "student_accounts", "learning_activity", "assessment_attempts",
        "assessment_item_results", "thinkific_progress_imports",
        "thinkific_progress_snapshots", "thinkific_progress_sections",
        "student_sessions", "monthly_reports",
    )
    before = {table: _table_dump(path, table) for table in untouched}
    profile_before = rows(
        path, "SELECT grade, status, created_at, updated_at, last_seen_at, "
              "grade_confirmed_at, grade_source FROM students WHERE id = ?",
        (student_id,))[0]
    payload = make_csv([person()])
    preview = plan_for(database, payload)

    code, _ = run_stdin(
        database, payload, "--apply", "--confirm-plan", preview.plan_id)

    assert code == 0
    assert rows(path, "SELECT display_name FROM students WHERE id = ?",
                (student_id,)) == [("Ana Anić",)]
    assert rows(
        path, "SELECT grade, status, created_at, updated_at, last_seen_at, "
              "grade_confirmed_at, grade_source FROM students WHERE id = ?",
        (student_id,))[0] == profile_before
    assert {table: _table_dump(path, table) for table in untouched} == before


def test_second_preview_after_success_has_zero_changes(store):
    path, database = store
    add_student(path, "student@example.com")
    payload = make_csv([person()])
    first = plan_for(database, payload)
    assert run_stdin(database, payload, "--apply", "--confirm-plan",
                     first.plan_id)[0] == 0

    second = plan_for(database, payload)

    assert second.totals["would_update"] == 0
    assert second.rows[0].classification == backfill.SKIP_EXISTING_NAME
    assert rows(path, "SELECT COUNT(*) FROM students")[0][0] == 1
    assert rows(path, "SELECT COUNT(*) FROM student_accounts")[0][0] == 1
