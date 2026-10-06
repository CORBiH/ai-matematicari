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
    report_input.import_progress_files(
        "2026-08", {"grade_7": build_csv([
            learner("otisao@example.com", first="Otišao", last="7")])},
        database=target)
    student_id = rows(path, "SELECT id FROM students")[0][0]
    before_snapshots = rows(
        path, "SELECT COUNT(*) FROM thinkific_progress_snapshots")[0][0]

    plan = thinkific_roster.build_plan(parsed("2026-09"), target)
    archive = action(plan, thinkific_roster.ARCHIVE, "Otišao 7")
    assert archive.default_selected is False
    assert rows(path, "SELECT status FROM students") == [("active",)]

    # Prazan izbor je valjan preview/apply i ne arhivira ništa.
    target.apply_roster_reconciliation([])
    assert rows(path, "SELECT status FROM students") == [("active",)]

    target.apply_roster_reconciliation([archive])
    assert rows(path, "SELECT id, status FROM students") == \
        [(student_id, "archived")]
    assert rows(path, "SELECT COUNT(*) FROM thinkific_progress_snapshots")[0][0] \
        == before_snapshots


def test_archived_student_requires_explicit_reactivation_without_other_mutation(
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
    conn.execute("UPDATE students SET status = 'archived' WHERE id = ?",
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
    assert reactivate.default_selected is False
    assert grade.default_selected is False

    defaults = [item for item in plan.actions if item.default_selected]
    target.apply_roster_reconciliation(defaults)
    assert rows(path, "SELECT id, status, grade FROM students") == \
        [(student_id, "archived", 7)]

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
