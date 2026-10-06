"""Sigurno usklađivanje registra prema uniji četiri trenutna izvoza."""
import json

import pytest

from matbot import report_input, reporting_db, reporting_schema, thinkific_progress
from matbot import thinkific_roster
from tests.fixtures.thinkific import build_csv, learner
from tests.test_thinkific_progress_import import build_v1, migrate, rows


libsql = pytest.importorskip("libsql")


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = str(tmp_path / "roster.db")
    build_v1(path)
    migrate(path)
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://test.invalid")
    target = reporting_db.ReportingDatabase(
        connect_factory=lambda: libsql.connect(
            path, timeout=10.0, _check_same_thread=False))
    yield target, path
    target.close()


def parsed(month, by_course=None):
    by_course = by_course or {}
    result = {}
    for course in thinkific_roster.REQUIRED_COURSES:
        payload = build_csv(by_course.get(course, []), sections=["OBLAST"])
        result[course] = thinkific_progress.parse_progress_csv(
            payload, course, month)
    return result


def action(plan, kind, label=None):
    found = [item for item in plan.actions
             if item.kind == kind and (label is None or item.label == label)]
    assert len(found) == 1
    return found[0]


def test_changed_name_is_default_but_grade_change_requires_confirmation(database):
    target, path = database
    report_input.import_progress_files(
        "2026-08", {"grade_7": build_csv(
            [learner("amer@example.com", first="Amer", last="7")])},
        database=target)
    student_id = rows(path, "SELECT id FROM students")[0][0]
    target.set_student_grade(student_id, 7)

    plan = thinkific_roster.build_plan(parsed("2026-09", {
        "grade_8": [learner("amer@example.com", first="Amer", last="8")],
    }), target)

    rename = action(plan, thinkific_roster.NAME, "Amer 8")
    promotion = action(plan, thinkific_roster.GRADE, "Amer 8")
    assert rename.default_selected is True
    assert promotion.default_selected is False
    assert promotion.selectable is True
    assert (promotion.current_grades, promotion.proposed_grades) == ((7,), (8,))

    target.apply_roster_reconciliation([rename])
    assert rows(path, "SELECT id, display_name, grade FROM students") == \
        [(student_id, "Amer 8", 7)]
    assert rows(path, "SELECT grade FROM student_current_grades") == [(7,)]

    fresh = thinkific_roster.build_plan(parsed("2026-09", {
        "grade_8": [learner("amer@example.com", first="Amer", last="8")],
    }), target)
    target.apply_roster_reconciliation([
        action(fresh, thinkific_roster.GRADE, "Amer 8")])
    assert rows(path, "SELECT grade FROM student_current_grades") == [(8,)]


@pytest.mark.parametrize(("account_type", "current_grades", "selectable"), [
    (reporting_db.ACCOUNT_TYPE_STUDENT, (), True),
    (reporting_db.ACCOUNT_TYPE_STUDENT, (6,), True),
    (reporting_db.ACCOUNT_TYPE_STUDENT, (6, 7), True),
    (reporting_db.ACCOUNT_TYPE_SUPPORT, (6,), False),
    (reporting_db.ACCOUNT_TYPE_TEST, (6,), False),
])
def test_grade_proposal_selectability_matches_apply_account_type_rule(
        database, account_type, current_grades, selectable):
    target, _path = database
    email = "%s-%d@example.com" % (account_type.lower(), len(current_grades))
    student_id = target.get_or_create_student(
        "thinkific_email", email, "Profil bez razreda")
    if current_grades:
        target.set_student_grades(student_id, current_grades)
    if account_type != reporting_db.ACCOUNT_TYPE_STUDENT:
        target.set_student_account_types([student_id], account_type)

    plan = thinkific_roster.build_plan(parsed("2026-09", {
        "grade_9": [learner(email, first="Profil", last="9")],
    }), target)
    proposal = action(plan, thinkific_roster.GRADE, "Profil 9")

    assert proposal.selectable is selectable
    assert proposal.default_selected is False
    assert bool(proposal.unavailable_reason) is (not selectable)
    assert proposal.public()["requires_explicit_confirmation"] is selectable


def test_forged_nonstudent_grade_action_remains_rejected_by_database(database):
    target, path = database
    student_id = target.get_or_create_student(
        "thinkific_email", "podrska@example.com", "Vedat 7 PODRŠKA")
    target.set_student_grade(student_id, 6)
    target.set_student_account_types(
        [student_id], reporting_db.ACCOUNT_TYPE_SUPPORT)
    plan = thinkific_roster.build_plan(parsed("2026-09", {
        "grade_7": [learner(
            "podrska@example.com", first="Vedat", last="7 PODRŠKA")],
    }), target)
    proposal = action(plan, thinkific_roster.GRADE, "Vedat 7 PODRŠKA")
    assert proposal.selectable is False

    with pytest.raises(reporting_db.ReportingUnavailable) as caught:
        target.apply_roster_reconciliation([proposal])

    assert caught.value.code == "student_grade_disabled"
    assert caught.value.phase == "validate_action"
    assert caught.value.action_type == "grade"
    assert rows(path, "SELECT grade FROM student_current_grades") == [(6,)]


def test_roster_failure_carries_phase_and_action_without_partial_write(
        database, monkeypatch):
    target, path = database
    target.get_or_create_student(
        "thinkific_email", "dijagnostika@example.com", "Staro ime")
    plan = thinkific_roster.build_plan(parsed("2026-09", {
        "grade_7": [learner(
            "dijagnostika@example.com", first="Novo", last="ime")],
    }), target)
    rename = action(plan, thinkific_roster.NAME, "Novo ime")

    def fail_without_leaking(_conn, _names):
        raise RuntimeError(
            "Tajno ime dijagnostika@example.com libsql://tajna raw detalj")

    monkeypatch.setattr(
        target, "_freeze_monthly_report_labels", fail_without_leaking)

    with pytest.raises(reporting_db.ReportingUnavailable) as caught:
        target.apply_roster_reconciliation([rename])

    assert caught.value.code == "roster_apply_failed:RuntimeError"
    assert caught.value.phase == "freeze_historical_names"
    assert caught.value.action_type == "name"
    assert type(caught.value.cause).__name__ == "RuntimeError"
    assert rows(path, "SELECT display_name FROM students") == [("Staro ime",)]


def test_commit_failure_is_labelled_and_rolls_back(database):
    target, path = database
    target.get_or_create_student(
        "thinkific_email", "commit@example.com", "Prije commita")
    plan = thinkific_roster.build_plan(parsed("2026-09", {
        "grade_7": [learner(
            "commit@example.com", first="Poslije", last="commita")],
    }), target)
    rename = action(plan, thinkific_roster.NAME, "Poslije commita")
    connection = target._connection()

    class FailingCommitConnection:
        def __init__(self, delegate):
            self.delegate = delegate

        def __getattr__(self, name):
            return getattr(self.delegate, name)

        def commit(self):
            raise TimeoutError("raw commit detail must not reach the log")

    target._conn = FailingCommitConnection(connection)

    with pytest.raises(reporting_db.ReportingUnavailable) as caught:
        target.apply_roster_reconciliation([rename])

    assert caught.value.code == "roster_apply_failed:TimeoutError"
    assert caught.value.phase == "commit"
    assert caught.value.action_type == ""
    assert type(caught.value.cause).__name__ == "TimeoutError"
    assert rows(path, "SELECT display_name FROM students") == [
        ("Prije commita",)]


def test_union_of_four_courses_does_not_archive_student_in_any_one_export(database):
    target, path = database
    first = target.get_or_create_student(
        "thinkific_email", "prvi@example.com", "Prvi 6")
    second = target.get_or_create_student(
        "thinkific_email", "drugi@example.com", "Drugi 9")

    plan = thinkific_roster.build_plan(parsed("2026-09", {
        "grade_6": [learner("prvi@example.com", first="Prvi", last="6")],
        "grade_9": [learner("drugi@example.com", first="Drugi", last="9")],
    }), target)

    archived = [item.student_id for item in plan.actions
                if item.kind == thinkific_roster.ARCHIVE]
    assert first not in archived and second not in archived
    assert rows(path, "SELECT COUNT(*) FROM students")[0][0] == 2


def test_missing_student_is_only_archived_when_explicitly_selected(database):
    target, path = database
    conn = libsql.connect(path)
    conn.execute("DROP TABLE monthly_reports")
    conn.execute(reporting_schema.MONTHLY_REPORTS_DDL)
    conn.execute(reporting_schema.MONTHLY_REPORTS_INDEX_DDL)
    conn.commit()
    conn.close()

    report_input.import_progress_files(
        "2026-08", {"grade_7": build_csv([
            learner("otisao@example.com", first="Otišao", last="7")])},
        database=target)
    student_id = rows(path, "SELECT id FROM students")[0][0]
    report_id = target.save_monthly_report(
        student_id=student_id, report_month="2026-08",
        metrics_json=json.dumps({"facts": {"marker": "archive-history"}}),
        ai_summary=json.dumps({"summary": "historijski"}),
        instructor_comment="Sačuvaj historiju.")
    conn = libsql.connect(path)
    conn.execute(
        "INSERT INTO learning_activity "
        "(student_id, source, event_type, event_key, grade, occurred_at) "
        "VALUES (?, 'matbot', 'lesson_started', 'archive-event', 7, "
        "'2026-08-10 10:00:00')", (student_id,))
    conn.execute(
        "INSERT INTO assessment_attempts "
        "(student_id, source, assessment_type, external_attempt_id, grade) "
        "VALUES (?, 'matbot', 'kontrolni', 'archive-assessment', 7)",
        (student_id,))
    conn.commit()
    conn.close()
    preserved = {
        "accounts": rows(
            path, "SELECT id, student_id, provider, external_user_id "
                  "FROM student_accounts"),
        "snapshots": rows(
            path, "SELECT id, student_id, import_id, report_month, course_key "
                  "FROM thinkific_progress_snapshots"),
        "reports": rows(
            path, "SELECT id, student_id, report_month FROM monthly_reports"),
        "activity": rows(
            path, "SELECT id, student_id, event_key FROM learning_activity"),
        "assessments": rows(
            path, "SELECT id, student_id, external_attempt_id "
                  "FROM assessment_attempts"),
    }

    plan = thinkific_roster.build_plan(parsed("2026-09"), target)
    archive = action(plan, thinkific_roster.ARCHIVE, "Otišao 7")
    assert archive.default_selected is False
    assert archive.after == reporting_db.STATUS_INACTIVE
    assert rows(path, "SELECT status FROM students") == [("active",)]

    # Prazan izbor je valjan preview/apply i ne arhivira ništa.
    target.apply_roster_reconciliation([])
    assert rows(path, "SELECT status FROM students") == [("active",)]

    target.apply_roster_reconciliation([archive])
    assert rows(path, "SELECT id, status FROM students") == \
        [(student_id, reporting_db.STATUS_INACTIVE)]
    assert student_id not in {
        item["id"] for item in target.list_students(active=True)}
    assert preserved["reports"] == [(report_id, student_id, "2026-08")]
    assert rows(path, "SELECT id, student_id, provider, external_user_id "
                      "FROM student_accounts") == preserved["accounts"]
    assert rows(path, "SELECT id, student_id, import_id, report_month, "
                      "course_key FROM thinkific_progress_snapshots") == \
        preserved["snapshots"]
    assert rows(path, "SELECT id, student_id, report_month "
                      "FROM monthly_reports") == preserved["reports"]
    assert rows(path, "SELECT id, student_id, event_key "
                      "FROM learning_activity") == preserved["activity"]
    assert rows(path, "SELECT id, student_id, external_attempt_id "
                      "FROM assessment_attempts") == preserved["assessments"]


def test_name_grade_and_archive_apply_in_one_transaction(database):
    target, path = database
    promoted_id = target.get_or_create_student(
        "thinkific_email", "promocija@example.com", "Promocija 7")
    target.set_student_grade(promoted_id, 7)
    archived_id = target.get_or_create_student(
        "thinkific_email", "arhiva@example.com", "Arhiva 7")
    target.set_student_grade(archived_id, 7)
    current = parsed("2026-09", {
        "grade_8": [learner(
            "promocija@example.com", first="Promocija", last="8")],
    })
    plan = thinkific_roster.build_plan(current, target)
    rename = action(plan, thinkific_roster.NAME, "Promocija 8")
    grade = action(plan, thinkific_roster.GRADE, "Promocija 8")
    archive = action(plan, thinkific_roster.ARCHIVE, "Arhiva 7")

    result = target.apply_roster_reconciliation([rename, grade, archive])

    assert result == {
        "names_updated": 1,
        "students_created": 0,
        "students_archived": 1,
        "students_reactivated": 0,
        "grades_confirmed": 1,
    }
    assert rows(path, "SELECT id, display_name, status FROM students "
                      "ORDER BY id") == [
        (promoted_id, "Promocija 8", "active"),
        (archived_id, "Arhiva 7", reporting_db.STATUS_INACTIVE),
    ]
    assert rows(path, "SELECT student_id, grade FROM student_current_grades "
                      "ORDER BY student_id") == [
        (promoted_id, 8), (archived_id, 7)]
    assert rows(path, "SELECT COUNT(*) FROM student_accounts")[0][0] == 2


def test_late_archive_failure_rolls_back_names_statuses_and_selected_grade(
        database):
    target, path = database
    promoted_id = target.get_or_create_student(
        "thinkific_email", "rollback-name@example.com", "Prije 7")
    target.set_student_grade(promoted_id, 7)
    first_archive_id = target.get_or_create_student(
        "thinkific_email", "archive-a@example.com", "Arhiva A 7")
    second_archive_id = target.get_or_create_student(
        "thinkific_email", "archive-z@example.com", "Arhiva Z 7")
    current = parsed("2026-09", {
        "grade_8": [learner(
            "rollback-name@example.com", first="Poslije", last="8")],
    })
    plan = thinkific_roster.build_plan(current, target)
    selected = [
        action(plan, thinkific_roster.NAME, "Poslije 8"),
        action(plan, thinkific_roster.GRADE, "Poslije 8"),
        action(plan, thinkific_roster.ARCHIVE, "Arhiva A 7"),
        action(plan, thinkific_roster.ARCHIVE, "Arhiva Z 7"),
    ]
    conn = libsql.connect(path)
    conn.execute(
        "CREATE TRIGGER reject_second_archive BEFORE UPDATE OF status "
        "ON students WHEN NEW.id = %d BEGIN "
        "SELECT RAISE(ABORT, 'synthetic archive failure'); END"
        % second_archive_id)
    conn.commit()
    conn.close()

    with pytest.raises(reporting_db.ReportingUnavailable) as caught:
        target.apply_roster_reconciliation(selected)

    assert caught.value.code == "roster_apply_failed:ValueError"
    assert caught.value.phase == "update_name_or_status"
    assert caught.value.action_type == "archive"
    assert rows(path, "SELECT id, display_name, status FROM students "
                      "ORDER BY id") == [
        (promoted_id, "Prije 7", "active"),
        (first_archive_id, "Arhiva A 7", "active"),
        (second_archive_id, "Arhiva Z 7", "active"),
    ]
    assert rows(path, "SELECT student_id, grade FROM student_current_grades") \
        == [(promoted_id, 7)]


def test_archive_reactivate_and_name_avoid_mutation_returning_fetch(database):
    target, path = database
    student_id = target.get_or_create_student(
        "thinkific_email", "remote-shape@example.com", "Remote 7")
    target.set_student_grade(student_id, 7)
    archive_plan = thinkific_roster.build_plan(parsed("2026-09"), target)
    archive = action(archive_plan, thinkific_roster.ARCHIVE, "Remote 7")
    delegate = target._connection()

    class FailingReturningCursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def __getattr__(self, name):
            return getattr(self.cursor, name)

        def fetchall(self):
            raise ValueError("remote mutation result cannot be decoded")

    class RemoteShapeConnection:
        def __init__(self, connection):
            self.connection = connection
            self.update_sql = []

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def execute(self, sql, parameters=()):
            cursor = self.connection.execute(sql, parameters)
            normalized = " ".join(sql.upper().split())
            if normalized.startswith("UPDATE STUDENTS"):
                self.update_sql.append(normalized)
                if " RETURNING " in normalized:
                    return FailingReturningCursor(cursor)
            return cursor

    remote_shape = RemoteShapeConnection(delegate)
    target._conn = remote_shape

    target.apply_roster_reconciliation([archive])
    reactivation_plan = thinkific_roster.build_plan(parsed("2026-09", {
        "grade_7": [learner(
            "remote-shape@example.com", first="Remote", last="7")],
    }), target)
    target.apply_roster_reconciliation([
        action(reactivation_plan, thinkific_roster.REACTIVATE, "Remote 7")])
    rename_plan = thinkific_roster.build_plan(parsed("2026-09", {
        "grade_7": [learner(
            "remote-shape@example.com", first="Remote Novo", last="7")],
    }), target)
    target.apply_roster_reconciliation([
        action(rename_plan, thinkific_roster.NAME, "Remote Novo 7")])

    assert rows(path, "SELECT id, display_name, status FROM students") == [
        (student_id, "Remote Novo 7", "active")]
    assert remote_shape.update_sql
    assert all(" RETURNING " not in sql for sql in remote_shape.update_sql)


def test_inactive_student_requires_explicit_reactivation_without_other_mutation(
        database):
    target, path = database
    # Pravi oblik historijske tabele omogućava da isti test dokaže i očuvanje
    # izvještaja, ne samo Thinkific snapshot-a.
    conn = libsql.connect(path)
    conn.execute("DROP TABLE monthly_reports")
    conn.execute(reporting_schema.MONTHLY_REPORTS_DDL)
    conn.execute(reporting_schema.MONTHLY_REPORTS_INDEX_DDL)
    conn.commit()
    conn.close()

    report_input.import_progress_files(
        "2026-08", {"grade_7": build_csv([
            learner("arhiviran@example.com", first="Arhiviran", last="7")])},
        database=target)
    student_id = rows(path, "SELECT id FROM students")[0][0]
    target.set_student_grade(student_id, 7)
    report_id = target.save_monthly_report(
        student_id=student_id, report_month="2026-08",
        metrics_json=json.dumps({"facts": {"marker": "sačuvaj"}}),
        ai_summary=json.dumps({"summary": "historijski"}),
        instructor_comment="Sačuvaj komentar.")
    conn = libsql.connect(path)
    conn.execute("UPDATE students SET status = 'inactive' WHERE id = ?",
                 (student_id,))
    conn.commit()
    conn.close()
    snapshot_before = rows(
        path, "SELECT id, student_id, course_key, report_month "
              "FROM thinkific_progress_snapshots")

    current = parsed("2026-09", {
        "grade_8": [learner("  ARHIVIRAN@EXAMPLE.COM  ",
                            first="Arhiviran", last="8")],
    })
    plan = thinkific_roster.build_plan(current, target)
    reactivate = action(plan, thinkific_roster.REACTIVATE, "Arhiviran 8")
    grade = action(plan, thinkific_roster.GRADE, "Arhiviran 8")
    assert not [item for item in plan.actions
                if item.kind == thinkific_roster.ADD]
    assert reactivate.student_id == student_id
    assert reactivate.after == reporting_db.STATUS_ACTIVE
    assert reactivate.default_selected is False
    assert grade.default_selected is False
    assert grade.selectable is True

    defaults = [item for item in plan.actions if item.default_selected]
    target.apply_roster_reconciliation(defaults)
    assert rows(path, "SELECT id, status, grade FROM students") == \
        [(student_id, reporting_db.STATUS_INACTIVE, 7)]

    reviewed = thinkific_roster.build_plan(current, target)
    target.apply_roster_reconciliation([
        action(reviewed, thinkific_roster.REACTIVATE, "Arhiviran 8")])

    assert rows(path, "SELECT id, status, grade FROM students") == \
        [(student_id, "active", 7)]
    assert rows(path, "SELECT COUNT(*) FROM students")[0][0] == 1
    assert rows(path, "SELECT COUNT(*) FROM student_accounts")[0][0] == 1
    assert rows(path, "SELECT grade FROM student_current_grades") == [(7,)]
    assert rows(path, "SELECT id, student_id, course_key, report_month "
                      "FROM thinkific_progress_snapshots") == snapshot_before
    saved = target.fetch_monthly_report(student_id, "2026-08")
    assert saved["id"] == report_id
    assert json.loads(saved["metrics_json"])["facts"] == {"marker": "sačuvaj"}
    assert json.loads(saved["ai_summary"]) == {"summary": "historijski"}
    assert saved["instructor_comment"] == "Sačuvaj komentar."


def test_new_student_is_added_by_email_without_automatic_grade(database):
    target, path = database
    plan = thinkific_roster.build_plan(parsed("2026-09", {
        "grade_8": [learner("nova@example.com", first="Nova", last="8")],
    }), target)
    add = action(plan, thinkific_roster.ADD, "Nova 8")
    suggestion = action(plan, thinkific_roster.GRADE, "Nova 8")
    assert add.default_selected is True
    assert suggestion.default_selected is False

    target.apply_roster_reconciliation([add])
    assert rows(path, "SELECT display_name, grade FROM students") == \
        [("Nova 8", None)]
    assert rows(path, "SELECT provider, external_user_id FROM student_accounts") \
        == [("thinkific_email", "nova@example.com")]
    assert rows(path, "SELECT COUNT(*) FROM student_current_grades")[0][0] == 0


def test_blank_current_csv_name_never_erases_existing_name(database):
    target, path = database
    target.get_or_create_student(
        "thinkific_email", "ime@example.com", "Sačuvano ime")

    plan = thinkific_roster.build_plan(parsed("2026-09", {
        "grade_7": [learner("ime@example.com", first="", last="")],
    }), target)

    assert not [item for item in plan.actions
                if item.kind == thinkific_roster.NAME]
    assert rows(path, "SELECT display_name FROM students") == [("Sačuvano ime",)]


def test_possible_duplicate_name_is_reported_but_never_merged(database):
    target, path = database
    existing = target.create_student("Isto ime", 7)
    plan = thinkific_roster.build_plan(parsed("2026-09", {
        "grade_7": [learner("drugi@example.com", first="Isto", last="ime")],
    }), target)
    add = action(plan, thinkific_roster.ADD, "Isto ime")
    assert add.warning and add.student_id is None

    target.apply_roster_reconciliation([add])
    found = rows(path, "SELECT id, display_name FROM students ORDER BY id")
    assert found[0][0] == existing
    assert len(found) == 2


def test_conflicting_names_for_same_email_across_exports_block_apply(database):
    target, _path = database
    plan = thinkific_roster.build_plan(parsed("2026-09", {
        "grade_7": [learner("isti@example.com", first="Amer", last="7")],
        "grade_8": [learner("isti@example.com", first="Amer", last="8")],
    }), target)
    assert plan.apply_allowed is False
    assert plan.plan_id is None and plan.blockers


def test_same_normalized_email_linked_to_two_students_is_integrity_blocker(database):
    target, path = database
    first = target.get_or_create_student(
        "thinkific_email", "DUPLIKAT@EXAMPLE.COM", "Prvi")
    second = target.get_or_create_student(
        "thinkific_email", "duplikat@example.com", "Drugi")
    assert first != second

    plan = thinkific_roster.build_plan(parsed("2026-09", {
        "grade_7": [learner("duplikat@example.com", first="Trenutni", last="7")],
    }), target)
    assert plan.apply_allowed is False
    assert any("više MAT-BOT učenika" in item for item in plan.blockers)
    assert rows(path, "SELECT COUNT(*) FROM students")[0][0] == 2


def test_plan_requires_all_four_current_exports(database):
    target, _path = database
    sources = parsed("2026-09")
    sources.pop("grade_9")
    with pytest.raises(thinkific_roster.RosterPlanError) as caught:
        thinkific_roster.build_plan(sources, target)
    assert caught.value.code == "all_four_courses_required"
