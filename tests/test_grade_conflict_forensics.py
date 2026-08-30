"""Faza 3D — sukob dokaza o tekućem razredu i porijeklo Thinkific razreda.

ŽIVI PRODUKCIJSKI KVAR KOJI OVAJ FAJL ČUVA: revizija je nad 34 stvarna učenika
vratila 34/34 CONSISTENT, iako su neki imali Thinkific 6 uz kontrolni 9 i
MAT-BOT 9 u ISTOM mjesecu.

Uzrok nije bio autoritet izvora nego POREĐENJE DATUMA: Thinkific pamti
`report_month` („2026-08"), a kontrolni i aktivnost pune žigove
(„2026-08-25 18:06:49"). Grana za sukob poredila je te SIROVE nizove na
jednakost, pa se nije mogla okinuti nijednom. Revizija je time bila bezvrijedna
— i, gore, izgledala je uvjerljivo.

DRUGA TVRDNJA: `imports.grade` i `imports.course_name` NISU dokaz o sadržaju
fajla — izvode se iz slota koji je administrator izabrao. Jedini trag iz samog
fajla su NAZIVI SEKCIJA, pa ih forenzika mora ispisati.

PII: svi učenici su sintetički.
"""
import ast
from pathlib import Path

import pytest

from matbot import (reporting_db, student_grade_audit, student_grades,
                    thinkific_grade_forensics)
from matbot.student_identity import PROVIDER_THINKIFIC_EMAIL

from tests.test_thinkific_progress_import import build_v1, migrate

libsql = pytest.importorskip("libsql")

ROOT = Path(__file__).resolve().parent.parent


def ev(tk=None, asm=None, mb=None):
    return student_grades.evidence_from_rows(
        thinkific_rows=tk, assessment_rows=asm, matbot_rows=mb)


# ===========================================================================
# 1) NORMALIZACIJA DATUMA
# ===========================================================================
@pytest.mark.parametrize("value, expected", [
    ("2026-08", "2026-08"),                 # Thinkific report_month
    ("2026-08-25 18:06:49", "2026-08"),     # kontrolni completed_at
    ("2026-08-25 18:06:31", "2026-08"),     # MAT-BOT occurred_at
    ("2026-08-25", "2026-08"),
    ("2025-12-31 23:59:59", "2025-12"),
    ("bad", None), ("", None), (None, None), ("2026", None),
])
def test_every_source_normalizes_to_a_month(value, expected):
    assert student_grades.evidence_month(value) == expected


def test_month_is_carried_alongside_the_original_trace():
    """Sirovi žig ostaje ZA PRIKAZ; poređenje ide preko mjeseca."""
    evidence = ev(tk=[("2026-08", 6)], asm=[("2026-08-25 18:06:49", 9)])
    assert evidence["thinkific"]["when"] == "2026-08"
    assert evidence["thinkific"]["month"] == "2026-08"
    assert evidence["assessment"]["when"] == "2026-08-25 18:06:49"
    assert evidence["assessment"]["month"] == "2026-08"


# ===========================================================================
# 2) STVARNI PRODUKCIJSKI OBLICI — MORAJU BITI SUKOB
# ===========================================================================
def test_edin_shaped_case_is_conflicting_not_consistent():
    """Thinkific 6 vs kontrolni 9 i MAT-BOT 9 u istom mjesecu."""
    evidence = ev(tk=[("2026-08", 6)],
                  asm=[("2026-08-25 18:06:49", 9)],
                  mb=[("2026-08-25 18:06:31", 9)])
    assert student_grades.classify(6, evidence) == (
        student_grades.STATUS_CONFLICTING, None)


def test_instructor_shaped_case_is_conflicting():
    evidence = ev(tk=[("2026-08", 6)],
                  asm=[("2026-08-25 18:06:49", 7)],
                  mb=[("2026-08-25 18:06:31", 7)])
    assert student_grades.classify(6, evidence) == (
        student_grades.STATUS_CONFLICTING, None)


def test_thinkific_versus_assessment_alone_is_conflicting():
    evidence = ev(tk=[("2026-08", 6)], asm=[("2026-08-25 10:00:00", 9)])
    assert student_grades.classify(6, evidence)[0] == \
        student_grades.STATUS_CONFLICTING


def test_thinkific_versus_matbot_alone_is_conflicting():
    """Slabiji izvor ne smije PREPORUČITI, ali smije OBORITI tvrdnju."""
    evidence = ev(tk=[("2026-08", 6)], mb=[("2026-08-25 10:00:00", 9)])
    assert student_grades.classify(6, evidence)[0] == \
        student_grades.STATUS_CONFLICTING


def test_conflict_yields_no_recommendation():
    _, recommended = student_grades.classify(
        6, ev(tk=[("2026-08", 6)], asm=[("2026-08-25 10:00:00", 9)]))
    assert recommended is None, "sukob traži čovjeka, ne automatski izbor"


# ===========================================================================
# 3) SLAGANJE I JEDNOSTAVNI SLUČAJEVI
# ===========================================================================
def test_all_three_sources_agreeing_is_consistent():
    evidence = ev(tk=[("2026-08", 6)], asm=[("2026-08-25 10:00:00", 6)],
                  mb=[("2026-08-25 10:00:00", 6)])
    assert student_grades.classify(6, evidence) == (
        student_grades.STATUS_CONSISTENT, 6)


def test_single_strong_source_agreeing_is_consistent():
    assert student_grades.classify(7, ev(tk=[("2026-08", 7)])) == (
        student_grades.STATUS_CONSISTENT, 7)


def test_single_strong_source_disagreeing_is_stale():
    assert student_grades.classify(6, ev(tk=[("2026-08", 7)])) == (
        student_grades.STATUS_LIKELY_STALE, 7)


def test_no_usable_evidence_is_insufficient():
    assert student_grades.classify(6, ev()) == (
        student_grades.STATUS_INSUFFICIENT, None)


def test_assessment_outranks_matbot_for_the_recommendation():
    status, recommended = student_grades.classify(
        6, ev(asm=[("2026-08-25 10:00:00", 8)], mb=[("2026-08-25 10:00:00", 8)]))
    assert status == student_grades.STATUS_LIKELY_STALE
    assert recommended == 8
    ordered = student_grades._by_authority(
        ev(tk=[("2026-08", 7)], asm=[("2026-08-25 10:00:00", 8)],
           mb=[("2026-08-25 10:00:00", 9)]))
    assert [item["source"] for item in ordered] == [
        student_grades.SOURCE_THINKIFIC, student_grades.SOURCE_ASSESSMENT,
        student_grades.SOURCE_MATBOT]


# ===========================================================================
# 4) SVJEŽINA — prošla školska godina nije tekući sukob
# ===========================================================================
def test_last_year_evidence_does_not_create_a_current_conflict():
    """Šesti lani i sedmi sada je NAPREDAK, ne sukob."""
    evidence = ev(tk=[("2025-08", 6)], asm=[("2026-08-25 10:00:00", 7)])
    assert student_grades.classify(6, evidence) == (
        student_grades.STATUS_LIKELY_STALE, 7)


def test_old_disagreeing_evidence_does_not_disturb_a_current_match():
    evidence = ev(tk=[("2026-08", 7)], asm=[("2025-10-01 10:00:00", 6)])
    assert student_grades.classify(7, evidence) == (
        student_grades.STATUS_CONSISTENT, 7)


def test_only_the_newest_month_across_all_sources_is_compared():
    """Dokaz iz ranijih mjeseci se ne skuplja zauvijek."""
    evidence = ev(tk=[("2025-09", 6), ("2026-08", 7)],
                  asm=[("2025-11-01 10:00:00", 6)],
                  mb=[("2026-08-25 10:00:00", 7)])
    assert student_grades.classify(7, evidence) == (
        student_grades.STATUS_CONSISTENT, 7)


def test_same_source_ambiguity_in_one_month_is_still_a_conflict():
    evidence = ev(tk=[("2026-08", 6), ("2026-08", 7)])
    assert student_grades.classify(6, evidence)[0] == \
        student_grades.STATUS_CONFLICTING


# ===========================================================================
# 5) IME NIJE DOKAZ
# ===========================================================================
def test_name_hint_cannot_affect_the_classifier():
    import inspect

    assert set(inspect.signature(student_grades.classify).parameters) == {
        "stored_grade", "evidence"}
    source = (ROOT / "matbot" / "student_grades.py").read_text(encoding="utf-8")
    body = source.split("def classify(")[1].split("\ndef ")[0]
    assert "name_grade_hint" not in body and "display_name" not in body


# ===========================================================================
# 6) OBJE KOMANDNE LINIJE DIJELE ISTU LOGIKU
# ===========================================================================
def _classifier_calls(module_name):
    source = (ROOT / "matbot" / (module_name + ".py")).read_text(encoding="utf-8")
    tree = ast.parse(source)
    return {node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}


def test_both_clis_use_the_shared_classifier():
    for module in ("student_grade_audit", "thinkific_grade_forensics"):
        assert "classify" in _classifier_calls(module), module
    # Nijedan ne reimplementira pravila.
    for module in ("student_grade_audit", "thinkific_grade_forensics"):
        source = (ROOT / "matbot" / (module + ".py")).read_text(encoding="utf-8")
        assert "CONFLICTING_EVIDENCE\"" not in source, module


# ===========================================================================
# 7) FORENZIKA — samo čitanje
# ===========================================================================
def _forensics_calls():
    source = (ROOT / "matbot" / "thinkific_grade_forensics.py").read_text(
        encoding="utf-8")
    tree = ast.parse(source)
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Attribute):
                called.add(target.attr)
            elif isinstance(target, ast.Name):
                called.add(target.id)
    return called, source


def test_forensics_cli_issues_no_write_and_has_no_apply():
    called, source = _forensics_calls()
    for forbidden in ("execute", "executemany", "commit", "set_student_grade",
                      "insert_session", "update_session", "delete_session",
                      "migrate", "_connection"):
        assert forbidden not in called, forbidden
    assert {"fetch_progress_imports", "fetch_import_sections",
            "list_students", "fetch_grade_evidence",
            "fetch_student_thinkific_history"} <= called
    assert "add_argument" not in called, "CLI ne smije nuditi nijednu opciju"
    with pytest.raises(SystemExit):
        thinkific_grade_forensics.main(["--apply"])


def test_forensics_readers_are_select_only():
    source = (ROOT / "matbot" / "reporting_db.py").read_text(encoding="utf-8")
    for name in ("fetch_progress_imports", "fetch_import_sections",
                 "fetch_student_thinkific_history"):
        body = source.split("def %s(" % name)[1].split("\n    def ")[0]
        for verb in ("INSERT ", "UPDATE ", "DELETE ", "CREATE ", "DROP ", "ALTER "):
            assert verb not in body.upper(), (name, verb)
        assert "commit()" not in body, name


# ===========================================================================
# 8) FORENZIKA NAD BAZOM
# ===========================================================================
@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "reporting.db")
    build_v1(path)
    migrate(path)
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://test.invalid")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token-not-real")
    database = reporting_db.ReportingDatabase(
        connect_factory=lambda: libsql.connect(path, timeout=10.0,
                                               _check_same_thread=False))
    reporting_db.set_database(database)
    yield database
    reporting_db.wait_for_pending_writes()
    reporting_db.set_database(None)


def _seed_import(db, *, import_id, month, slot, grade, sha, sections,
                 student_ids):
    conn = db._connection()
    conn.execute(
        "INSERT INTO thinkific_progress_imports (id, report_month, course_key, "
        " course_name, grade, source_sha256, row_count, imported_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (import_id, month, slot, "Matematika za %d. razred" % grade, grade,
         sha, len(student_ids), "2026-08-01 09:00:00"))
    for student_id in student_ids:
        conn.execute(
            "INSERT INTO thinkific_progress_snapshots (import_id, student_id, "
            " report_month, course_key, course_name, grade, percent_viewed, "
            " percent_completed) VALUES (?, ?, ?, ?, ?, ?, 40, 10)",
            (import_id, student_id, month, slot,
             "Matematika za %d. razred" % grade, grade))
        snapshot_id = conn.execute(
            "SELECT MAX(id) FROM thinkific_progress_snapshots").fetchall()[0][0]
        for ordinal, name in enumerate(sections, start=1):
            conn.execute(
                "INSERT INTO thinkific_progress_sections (snapshot_id, ordinal, "
                " section_name, progress_percent) VALUES (?, ?, ?, 10)",
                (snapshot_id, ordinal, name))
    conn.commit()


def test_forensics_reports_imports_sections_and_hashes(db):
    student_a = db.create_student("Sintetički A", 6)
    student_b = db.create_student("Sintetički B", 6)
    _seed_import(db, import_id=1, month="2026-08", slot="grade_6", grade=6,
                 sha="a" * 64, sections=["SKUPOVI", "DJELJIVOST"],
                 student_ids=[student_a])
    # ISTI fajl podmetnut i u drugi slot — tačno greška koju tražimo.
    _seed_import(db, import_id=2, month="2026-08", slot="grade_7", grade=7,
                 sha="a" * 64, sections=["SKUPOVI", "DJELJIVOST"],
                 student_ids=[student_b])

    data = thinkific_grade_forensics.collect(database=db)
    text = thinkific_grade_forensics.format_report(data)

    assert "THINKIFIC_IMPORTS_TOTAL: 2" in text
    assert "SNAPSHOTS_BY_GRADE" in text
    # Nazivi sekcija IZ FAJLA moraju biti vidljivi.
    assert "SKUPOVI" in text and "DJELJIVOST" in text
    # Isti sha u dva slota mora biti istaknut.
    assert "DISTINCT_SOURCE_FILES: 1" in text
    assert "ISTI FAJL U VISE SLOTOVA" in text
    assert "grade_6" in text and "grade_7" in text


def test_forensics_flags_the_production_shape_and_shows_history(db):
    student_id = db.get_or_create_student(PROVIDER_THINKIFIC_EMAIL,
                                          "tajna@example.com")
    db.set_student_grade(student_id, 6)
    _seed_import(db, import_id=1, month="2026-08", slot="grade_6", grade=6,
                 sha="b" * 64, sections=["SKUPOVI"], student_ids=[student_id])
    conn = db._connection()
    conn.execute(
        "INSERT INTO assessment_attempts (student_id, source, assessment_type, "
        " external_attempt_id, grade, score_percent, correct_count, total_count, "
        " completed_at) VALUES (?, 'matbot', 'kontrolni', 'e1', 9, 60.0, 3, 5, "
        " '2026-08-25 18:06:49')", (student_id,))
    conn.execute(
        "INSERT INTO learning_activity (student_id, source, event_type, "
        " event_key, grade, occurred_at) "
        "VALUES (?, 'matbot', 'practice_answer_correct', 'k1', 9, "
        " '2026-08-25 18:06:31')", (student_id,))
    conn.commit()

    data = thinkific_grade_forensics.collect(database=db)
    row = data["students"][0]
    assert row["status_after_fixed_classifier"] == \
        student_grades.STATUS_CONFLICTING
    assert row["recommended_grade"] is None
    assert row["latest_thinkific_month"] == "2026-08"
    assert row["latest_assessment_grade"] == 9

    text = thinkific_grade_forensics.format_report(data)
    assert "NON-CONSISTENT STUDENTS (1)" in text
    assert "THINKIFIC HISTORY" in text
    assert "grade_6" in text
    # NIKAD identitet.
    assert "tajna@example.com" not in text and "@" not in text


def test_audit_cli_uses_the_corrected_classifier(db):
    student_id = db.create_student("Sporni", 6)
    _seed_import(db, import_id=1, month="2026-08", slot="grade_6", grade=6,
                 sha="c" * 64, sections=["SKUPOVI"], student_ids=[student_id])
    conn = db._connection()
    conn.execute(
        "INSERT INTO assessment_attempts (student_id, source, assessment_type, "
        " external_attempt_id, grade, score_percent, correct_count, total_count, "
        " completed_at) VALUES (?, 'matbot', 'kontrolni', 'e1', 9, 60.0, 3, 5, "
        " '2026-08-25 18:06:49')", (student_id,))
    conn.commit()

    rows = student_grade_audit.collect(database=db)
    assert rows[0]["status"] == student_grades.STATUS_CONFLICTING
    tally = student_grade_audit.summarize(rows)
    assert tally[student_grades.STATUS_CONFLICTING] == 1
    assert tally[student_grades.STATUS_CONSISTENT] == 0
