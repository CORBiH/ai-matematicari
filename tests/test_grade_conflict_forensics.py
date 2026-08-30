"""Faza 3D+ — razlika u korištenom gradivu i porijeklo Thinkific razreda.

DVA ŽIVA NALAZA KOJE OVAJ FAJL ČUVA.

PRVI (poređenje datuma). Revizija je nad 34 stvarna učenika vratila 34/34
CONSISTENT, iako su neki imali Thinkific 6 uz kontrolni 9 i MAT-BOT 9 u ISTOM
mjesecu. Uzrok je bilo POREĐENJE SIROVIH ŽIGOVA: Thinkific pamti `report_month`
(„2026-08"), a kontrolni i aktivnost pune žigove („2026-08-25 18:06:49") —
jednaki ne mogu biti nikad. Normalizacija na mjesec je i dalje obavezna, jer bez
nje se razlika u gradivu ne bi ni vidjela.

DRUGI (šta ta razlika ZNAČI). Forenzika je pokazala da je augustovski uvoz
STVARNO kurs šestog razreda — sekcije iz fajla su SKUPOVI, DJELJIVOST BROJEVA,
RAZLOMCI — i da u njemu legitimno rade i sedmaci koji obnavljaju gradivo. Zato
razlika više NIJE „sukob koji treba riješiti" nego KONTEKST, a tekući razred
potvrđuje isključivo administrator. Preporuka razreda po sadržaju je uklonjena.

TREĆA TVRDNJA: `imports.grade` i `imports.course_name` NISU dokaz o sadržaju
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
# 2) STVARNI PRODUKCIJSKI OBLICI — RAZLIKA U GRADIVU, NE SUKOB
# ===========================================================================
CONFIRMED_AT = "2026-08-29 09:00:00"
ADMIN = student_grades.GRADE_SOURCE_ADMIN


def test_edin_shaped_case_is_unconfirmed_while_nobody_confirmed_it():
    """Thinkific 6 vs kontrolni 9 i MAT-BOT 9 — a razred niko nije potvrdio."""
    evidence = ev(tk=[("2026-08", 6)],
                  asm=[("2026-08-25 18:06:49", 9)],
                  mb=[("2026-08-25 18:06:31", 9)])
    status, content = student_grades.classify(6, None, None, evidence)
    assert status == student_grades.STATUS_UNCONFIRMED
    # Sadrzaj je VIDLJIV, ali ne bira razred.
    assert content["thinkific"]["grade"] == 6
    assert content["assessment"]["grade"] == 9


def test_edin_shaped_case_confirmed_as_ninth_keeps_the_ninth():
    """Devetak koji obnavlja gradivo šestog razreda ostaje devetak."""
    evidence = ev(tk=[("2026-08", 6)],
                  asm=[("2026-08-25 18:06:49", 9)],
                  mb=[("2026-08-25 18:06:31", 9)])
    status, _ = student_grades.classify(9, CONFIRMED_AT, ADMIN, evidence)
    assert status == student_grades.STATUS_CONTENT_MISMATCH


def test_instructor_shaped_case_is_unconfirmed():
    evidence = ev(tk=[("2026-08", 6)],
                  asm=[("2026-08-25 18:06:49", 7)],
                  mb=[("2026-08-25 18:06:31", 7)])
    assert student_grades.classify(6, None, None, evidence)[0] == \
        student_grades.STATUS_UNCONFIRMED


def test_cross_source_difference_needs_the_month_normalisation():
    """Bez normalizacije se razlika ne bi ni vidjela (prvi živi kvar)."""
    evidence = ev(tk=[("2026-08", 6)], asm=[("2026-08-25 10:00:00", 9)])
    status, content = student_grades.classify(9, CONFIRMED_AT, ADMIN, evidence)
    assert status == student_grades.STATUS_CONTENT_MISMATCH
    assert content["thinkific"]["month"] == content["assessment"]["month"]


def test_matbot_content_alone_is_still_only_context():
    """Najslabiji izvor smije PRIKAZATI razliku, ali ne mijenja profil."""
    evidence = ev(tk=[("2026-08", 6)], mb=[("2026-08-25 10:00:00", 9)])
    assert student_grades.classify(9, CONFIRMED_AT, ADMIN, evidence)[0] == \
        student_grades.STATUS_CONTENT_MISMATCH


def test_no_source_ever_yields_a_recommendation():
    result = student_grades.classify(
        6, CONFIRMED_AT, ADMIN, ev(tk=[("2026-08", 6)],
                                   asm=[("2026-08-25 10:00:00", 9)]))
    status, content = result
    # Drugi element su DOKAZI O SADRZAJU, nikad predlozen razred.
    assert isinstance(content, dict)
    assert all(isinstance(item, dict) for item in content.values())
    assert not hasattr(student_grades, "strongest_evidence")
    assert not hasattr(student_grades, "_by_authority")


# ===========================================================================
# 3) SLAGANJE I JEDNOSTAVNI SLUČAJEVI
# ===========================================================================
def test_all_three_sources_agreeing_with_a_confirmed_grade_is_confirmed():
    evidence = ev(tk=[("2026-08", 6)], asm=[("2026-08-25 10:00:00", 6)],
                  mb=[("2026-08-25 10:00:00", 6)])
    assert student_grades.classify(6, CONFIRMED_AT, ADMIN, evidence)[0] == \
        student_grades.STATUS_CONFIRMED


def test_single_source_agreeing_is_confirmed():
    assert student_grades.classify(
        7, CONFIRMED_AT, ADMIN, ev(tk=[("2026-08", 7)]))[0] == \
        student_grades.STATUS_CONFIRMED


def test_single_source_differing_is_content_context_not_an_error():
    assert student_grades.classify(
        6, CONFIRMED_AT, ADMIN, ev(tk=[("2026-08", 7)]))[0] == \
        student_grades.STATUS_CONTENT_MISMATCH


def test_no_content_evidence_leaves_a_confirmed_grade_alone():
    assert student_grades.classify(6, CONFIRMED_AT, ADMIN, ev())[0] == \
        student_grades.STATUS_CONFIRMED


def test_no_content_evidence_does_not_confirm_a_legacy_grade():
    assert student_grades.classify(6, None, None, ev())[0] == \
        student_grades.STATUS_UNCONFIRMED


# ===========================================================================
# 4) SVJEŽINA — prošla školska godina nije tekuće gradivo
# ===========================================================================
def test_last_year_content_does_not_disturb_a_confirmed_grade():
    """Šesti lani i sedmi sada je NAPREDAK, ne razlika."""
    evidence = ev(tk=[("2025-08", 6)], asm=[("2026-08-25 10:00:00", 7)])
    assert student_grades.classify(7, CONFIRMED_AT, ADMIN, evidence)[0] == \
        student_grades.STATUS_CONFIRMED


def test_old_differing_content_does_not_disturb_a_current_match():
    evidence = ev(tk=[("2026-08", 7)], asm=[("2025-10-01 10:00:00", 6)])
    assert student_grades.classify(7, CONFIRMED_AT, ADMIN, evidence)[0] == \
        student_grades.STATUS_CONFIRMED


def test_only_the_newest_month_across_all_sources_is_compared():
    """Gradivo iz ranijih mjeseci se ne skuplja zauvijek."""
    evidence = ev(tk=[("2025-09", 6), ("2026-08", 7)],
                  asm=[("2025-11-01 10:00:00", 6)],
                  mb=[("2026-08-25 10:00:00", 7)])
    assert student_grades.classify(7, CONFIRMED_AT, ADMIN, evidence)[0] == \
        student_grades.STATUS_CONFIRMED


def test_same_source_two_grades_in_one_month_is_still_a_difference():
    evidence = ev(tk=[("2026-08", 6), ("2026-08", 7)])
    assert student_grades.classify(6, CONFIRMED_AT, ADMIN, evidence)[0] == \
        student_grades.STATUS_CONTENT_MISMATCH


# ===========================================================================
# 5) IME NIJE DOKAZ
# ===========================================================================
def test_name_hint_cannot_affect_the_classifier():
    import inspect

    assert set(inspect.signature(student_grades.classify).parameters) == {
        "grade", "grade_confirmed_at", "grade_source", "evidence"}
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
        assert "CONTENT_GRADE_MISMATCH\"" not in source, module
        assert "UNCONFIRMED\"" not in source, module


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
    # ZATECEN OBLIK: razred postoji, potvrde nema — tacno kao 34 stvarna reda.
    conn = db._connection()
    conn.execute("UPDATE students SET grade = 6, grade_confirmed_at = NULL, "
                 " grade_source = NULL WHERE id = ?", (student_id,))
    conn.commit()
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
    assert row["grade_status"] == student_grades.STATUS_UNCONFIRMED
    assert "recommended_grade" not in row
    assert row["content_thinkific_month"] == "2026-08"
    assert row["content_assessment_grade"] == 9

    text = thinkific_grade_forensics.format_report(data)
    assert "STUDENTS NEEDING A LOOK (1)" in text
    assert "THINKIFIC HISTORY" in text
    assert "NO_RECOMMENDED_GRADE" in text
    assert "grade_6" in text
    # NIKAD identitet.
    assert "tajna@example.com" not in text and "@" not in text


def test_audit_cli_uses_the_shared_confirmation_classifier(db):
    student_id = db.create_student("Obnavlja Sesti", 9)
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
    # Potvrdjen deveti razred + gradivo sestog iz uvoza = RAZLIKA U GRADIVU.
    assert rows[0]["status"] == student_grades.STATUS_CONTENT_MISMATCH
    tally = student_grade_audit.summarize(rows)
    assert tally[student_grades.STATUS_CONTENT_MISMATCH] == 1
    assert tally[student_grades.STATUS_CONFIRMED] == 0
    assert tally[student_grades.STATUS_UNCONFIRMED] == 0
