"""Faza 3D — TEKUĆI razred: nema tihe šestice, ima svjesna ispravka.

ŽIVI NALAZ KOJI OVAJ FAJL ČUVA: `students.grade` je bio zapiši-jednom, a
tutorski padajući meni je u markupu imao `6` kao unaprijed izabranu opciju — pa
je učenik koji ga nikad nije dodirnuo trajno dobijao šesti razred. Kad je Faza
3D počela birati kurikulum po tom polju, instruktor je za sedmaka dobijao
gradivo šestog razreda.

TRI GRANICE:
  1. tutorski zahtjev NE PIŠE profil (razred turnusa != razred profila),
  2. nepoznat razred se NE POPUNJAVA šesticom nego blokira unos gradiva,
  3. dokaz se PRIKAZUJE, a ispravku radi administrator — nikad automat.

PII: svi učenici su sintetički.
"""
import json
import re
from pathlib import Path

import pytest

from matbot import (reporting_db, student_grade_audit, student_grades,
                    student_identity)

from tests.test_thinkific_progress_import import build_v1, migrate

libsql = pytest.importorskip("libsql")

ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = ROOT / "templates" / "index.html"

TK = "thinkific"
AS = "assessment"
MB = "matbot"


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


def evidence(tk=None, asm=None, mb=None):
    return student_grades.evidence_from_rows(
        thinkific_rows=tk, assessment_rows=asm, matbot_rows=mb)


# ===========================================================================
# 1) TIHA ŠESTICA JE UKLONJENA
# ===========================================================================
def test_frontend_no_longer_falls_back_to_grade_six():
    """Tri mjesta su tiho pretvarala nepoznat razred u 6."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert "|| '6'" not in html, "tihi fallback na 6 je i dalje prisutan"
    assert 'grade: \'\', mode:' in html, "početno stanje mora biti nepoznat razred"


def test_grade_selector_requires_an_explicit_choice():
    """Ranije je `<option value=\"6\" selected>` slao 6 i bez ijednog klika."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert '<option value="6" selected>' not in html
    assert '<option value="" selected disabled>' in html
    # Svi razredi su i dalje ponuđeni — stvarni šestaci nisu pokvareni.
    for grade in (6, 7, 8, 9):
        assert '<option value="%d">%d. razred</option>' % (grade, grade) in html


def test_tutor_identity_never_writes_a_profile_grade(db):
    """Razred zahtjeva je PARAMETAR TURNUSA, ne tvrdnja o profilu."""
    identity = {"provider": student_identity.PROVIDER_THINKIFIC_EMAIL,
                "external_user_id": "ucenik@example.com"}
    student_id = student_identity.resolve_student(identity)
    assert student_id is not None
    profile = db.fetch_student_profile(student_id)
    assert profile["grade"] is None, "tutorski put je upisao razred u profil"


def test_resolve_student_no_longer_accepts_a_grade_argument():
    """Potpis je uži namjerno: nema načina da razred procuri iz turnusa."""
    import inspect

    assert "grade" not in inspect.signature(
        student_identity.resolve_student).parameters


def test_explicitly_created_grade_six_student_still_stores_six(db):
    """Stvarni šestaci nisu pokvareni ovom ispravkom."""
    student_id = db.create_student("Pravi Šestak", 6)
    assert db.fetch_student_profile(student_id)["grade"] == 6


@pytest.mark.parametrize("grade", [6, 7, 8, 9])
def test_every_supported_grade_round_trips(db, grade):
    student_id = db.create_student("Učenik", grade)
    assert db.fetch_student_profile(student_id)["grade"] == grade


def test_no_backend_default_invents_grade_six(db):
    """Identitet bez razreda ostaje NULL, ne 6."""
    student_id = db.get_or_create_student(
        student_identity.PROVIDER_THINKIFIC_EMAIL, "bez-razreda@example.com")
    assert db.fetch_student_profile(student_id)["grade"] is None


# ===========================================================================
# 2) KLASIFIKACIJA — samo strukturni dokaz
# ===========================================================================
def test_consistent_when_stored_matches_strongest_evidence():
    status, recommended = student_grades.classify(
        7, evidence(tk=[("2026-08", 7)]))
    assert status == student_grades.STATUS_CONSISTENT
    assert recommended == 7


def test_stale_when_thinkific_is_newer_and_different():
    status, recommended = student_grades.classify(
        6, evidence(tk=[("2026-05", 6), ("2026-08", 7)]))
    assert status == student_grades.STATUS_LIKELY_STALE
    assert recommended == 7


def test_null_stored_grade_is_stale_not_consistent():
    status, recommended = student_grades.classify(
        None, evidence(tk=[("2026-08", 7)]))
    assert status == student_grades.STATUS_LIKELY_STALE
    assert recommended == 7


def test_insufficient_without_any_structured_evidence():
    status, recommended = student_grades.classify(6, evidence())
    assert status == student_grades.STATUS_INSUFFICIENT
    assert recommended is None


def test_same_month_conflicting_thinkific_grades_is_a_conflict():
    """Dva razreda u ISTOM najsvježijem mjesecu se ne biraju nasumično."""
    status, recommended = student_grades.classify(
        6, evidence(tk=[("2026-08", 6), ("2026-08", 7)]))
    assert status == student_grades.STATUS_CONFLICTING
    assert recommended is None


def test_equally_recent_sources_that_disagree_is_a_conflict():
    status, recommended = student_grades.classify(
        6, evidence(tk=[("2026-08", 7)], asm=[("2026-08", 6)]))
    assert status == student_grades.STATUS_CONFLICTING
    assert recommended is None


def test_previous_school_year_alone_is_not_a_conflict():
    """Šesti lani i sedmi sada je NAPREDAK, ne sukob."""
    status, recommended = student_grades.classify(
        6, evidence(tk=[("2025-10", 6), ("2026-09", 7)]))
    assert status == student_grades.STATUS_LIKELY_STALE
    assert recommended == 7


def test_authority_order_thinkific_beats_assessment_beats_matbot():
    strongest = student_grades.strongest_evidence(
        evidence(tk=[("2026-08", 7)], asm=[("2026-09", 8)], mb=[("2026-09", 9)]))
    assert strongest["grade"] == 7 and strongest["source"] == "thinkific_progress"
    # Bez Thinkifica prednost ima kontrolni.
    strongest = student_grades.strongest_evidence(
        evidence(asm=[("2026-09", 8)], mb=[("2026-09", 9)]))
    assert strongest["source"] == "assessment"


def test_weak_matbot_evidence_alone_still_classifies():
    status, recommended = student_grades.classify(6, evidence(mb=[("2026-09", 7)]))
    assert status == student_grades.STATUS_LIKELY_STALE and recommended == 7


def test_latest_evidence_ordering_is_deterministic():
    forward = evidence(tk=[("2026-01", 6), ("2026-09", 7)])
    backward = evidence(tk=[("2026-09", 7), ("2026-01", 6)])
    assert forward["thinkific"] == backward["thinkific"]
    assert forward["thinkific"]["grade"] == 7


# ===========================================================================
# 3) IME NIJE DOKAZ
# ===========================================================================
@pytest.mark.parametrize("name, hint", [
    ("Adjan 7 PLUS", 7), ("Amar 7 Septembar", 7), ("Andrej 7", 7),
    ("Bez broja", None), ("Grupa 7/8", None), ("Ucenik 2026", None),
])
def test_name_hint_is_extracted_for_humans_only(name, hint):
    assert student_grades.name_grade_hint(name) == hint


def test_name_hint_never_changes_the_classification():
    """„Adjan 7 PLUS" bez strukturnog dokaza ostaje INSUFFICIENT."""
    status, recommended = student_grades.classify(6, evidence())
    assert status == student_grades.STATUS_INSUFFICIENT
    assert recommended is None
    # Ime se ni ne prosljeđuje klasifikatoru — potpis to dokazuje.
    import inspect

    params = inspect.signature(student_grades.classify).parameters
    assert set(params) == {"stored_grade", "evidence"}


def test_classifier_source_never_reads_display_name():
    source = (ROOT / "matbot" / "student_grades.py").read_text(encoding="utf-8")
    classify_body = source.split("def classify(")[1].split("\ndef ")[0]
    assert "name_grade_hint" not in classify_body
    assert "display_name" not in classify_body


# ===========================================================================
# 4) ADMIN ISPRAVKA — mijenja SAMO profil
# ===========================================================================
def test_admin_can_change_the_current_grade(db):
    student_id = db.create_student("Sedmak", 6)
    assert db.set_student_grade(student_id, 7) is True
    assert db.fetch_student_profile(student_id)["grade"] == 7
    assert db.set_student_grade(student_id, 8) is True
    assert db.fetch_student_profile(student_id)["grade"] == 8


@pytest.mark.parametrize("grade", [0, 5, 10, 99, -1])
def test_invalid_grade_is_refused_by_the_database_layer(db, grade):
    student_id = db.create_student("Učenik", 6)
    with pytest.raises(reporting_db.ReportingUnavailable):
        db.set_student_grade(student_id, grade)
    assert db.fetch_student_profile(student_id)["grade"] == 6


def test_changing_grade_leaves_history_untouched(db):
    """TVRDNJA: promocija 6→7 ne smije prepisati ijedno OPAŽANJE."""
    student_id = db.create_student("Napredni", 6)
    conn = db._connection()
    conn.execute(
        "INSERT INTO learning_activity (student_id, source, event_type, "
        " event_key, grade, occurred_at) "
        "VALUES (?, 'matbot', 'practice_answer_correct', 'k1', 6, "
        " '2026-05-01 10:00:00')", (student_id,))
    conn.execute(
        "INSERT INTO assessment_attempts (student_id, source, assessment_type, "
        " external_attempt_id, grade, score_percent, correct_count, "
        " total_count, completed_at) "
        "VALUES (?, 'matbot', 'kontrolni', 'e1', 6, 60.0, 3, 5, "
        " '2026-05-02 10:00:00')", (student_id,))
    conn.commit()

    from matbot import student_sessions

    session = student_sessions.validate_session(
        session_date="2026-05-03", attendance="present", activity_rating=4,
        homework_status="done", area_name="Djeljivost brojeva",
        lesson_name="Djeljivost zbira, razlike i proizvoda", grade=6)
    db.insert_session(student_id, session)

    db.set_student_grade(student_id, 7)

    assert conn.execute("SELECT grade FROM learning_activity").fetchall()[0][0] == 6
    assert conn.execute("SELECT grade FROM assessment_attempts").fetchall()[0][0] == 6
    stored = db.fetch_sessions(student_id)[0]
    assert stored["area_name"] == "Djeljivost brojeva"
    assert stored["lesson_name"] == "Djeljivost zbira, razlike i proizvoda"
    assert db.fetch_student_profile(student_id)["grade"] == 7


def test_new_session_uses_the_new_current_grade(db):
    from matbot import student_sessions

    student_id = db.create_student("Prešao U Sedmi", 6)
    db.set_student_grade(student_id, 7)
    # Gradivo SEDMOG razreda sada prolazi...
    record = student_sessions.validate_session(
        session_date="2026-09-05", attendance="present", activity_rating=4,
        homework_status="done", area_name="Cijeli brojevi",
        lesson_name="Skup cijelih brojeva Z", grade=7)
    assert db.insert_session(student_id, record)
    # ...a gradivo šestog više ne pripada njegovom tekućem razredu.
    with pytest.raises(student_sessions.SessionValidationError):
        student_sessions.validate_session(
            session_date="2026-09-06", attendance="present", activity_rating=4,
            homework_status="done", area_name="Djeljivost brojeva",
            lesson_name="Djeljivost zbira, razlike i proizvoda", grade=7)


# ===========================================================================
# 5) DOKAZ IZ BAZE
# ===========================================================================
def test_evidence_reader_returns_dated_structured_rows(db):
    student_id = db.create_student("S Dokazom", 6)
    conn = db._connection()
    conn.execute(
        "INSERT INTO thinkific_progress_imports (report_month, course_key, "
        " course_name, grade, source_sha256, row_count) "
        "VALUES ('2026-09', 'grade_7', 'Matematika 7', 7, 'x', 1)")
    conn.execute(
        "INSERT INTO thinkific_progress_snapshots (import_id, student_id, "
        " report_month, course_key, course_name, grade) "
        "VALUES (1, ?, '2026-09', 'grade_7', 'Matematika 7', 7)", (student_id,))
    conn.commit()

    found = db.fetch_grade_evidence(student_id)
    assert found["thinkific"]["grade"] == 7
    assert found["thinkific"]["when"] == "2026-09"
    status, recommended = student_grades.classify(6, found)
    assert status == student_grades.STATUS_LIKELY_STALE and recommended == 7


def test_evidence_reader_exposes_no_identifier(db):
    student_id = db.get_or_create_student(
        student_identity.PROVIDER_THINKIFIC_EMAIL, "tajna@example.com")
    blob = json.dumps(db.fetch_grade_evidence(student_id), ensure_ascii=False)
    assert "@" not in blob and "tajna" not in blob


# ===========================================================================
# 6) REVIZIJSKI CLI — SAMO ČITANJE
# ===========================================================================
def test_audit_module_issues_no_write_statement():
    """Alat koji prijavljuje ne smije moći ništa promijeniti.

    Provjerava se KOD, ne komentari: modul uopšte ne izvršava SQL — ide kroz
    `list_students` i `fetch_grade_evidence`, koji su oboje čitajući."""
    import ast

    source = (ROOT / "matbot" / "student_grade_audit.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    # IMENA KOJA SE STVARNO POZIVAJU — ne komentari i ne dokumentacija. Ranija
    # verzija ovog testa je gledala sirov tekst i padala na vlastitom
    # docstringu; tekst nije kod.
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Attribute):
                called.add(target.attr)
            elif isinstance(target, ast.Name):
                called.add(target.id)

    for forbidden in ("execute", "executemany", "commit", "set_student_grade",
                      "insert_session", "update_session", "delete_session",
                      "save_monthly_report", "migrate", "_connection"):
        assert forbidden not in called, forbidden

    # Ono što SMIJE zvati je čitajuće.
    assert {"list_students", "fetch_grade_evidence"} <= called

    # NEMA `--apply`: provjerava se ARGPARSE, ne tekst — docstring modula tu
    # riječ spominje upravo da bi objasnio da opcije nema.
    assert "add_argument" not in called, "CLI ne smije nuditi nijednu opciju"
    with pytest.raises(SystemExit):
        student_grade_audit.main(["--apply"])


def test_audit_collects_and_classifies(db):
    stale = db.create_student("Zastario", 6)
    consistent = db.create_student("Uredan", 7)
    conn = db._connection()
    conn.execute(
        "INSERT INTO thinkific_progress_imports (report_month, course_key, "
        " course_name, grade, source_sha256, row_count) "
        "VALUES ('2026-09', 'grade_7', 'M7', 7, 'x', 1)")
    for student_id in (stale, consistent):
        conn.execute(
            "INSERT INTO thinkific_progress_snapshots (import_id, student_id, "
            " report_month, course_key, course_name, grade) "
            "VALUES (1, ?, '2026-09', 'grade_7', 'M7', 7)", (student_id,))
    conn.commit()

    rows = student_grade_audit.collect(database=db)
    by_id = {row["student_id"]: row for row in rows}
    assert by_id[stale]["status"] == student_grades.STATUS_LIKELY_STALE
    assert by_id[stale]["recommended_grade"] == 7
    assert by_id[consistent]["status"] == student_grades.STATUS_CONSISTENT


def test_audit_summary_counts_are_correct(db):
    db.create_student("Bez dokaza A", 6)
    db.create_student("Bez dokaza B", 7)
    rows = student_grade_audit.collect(database=db)
    tally = student_grade_audit.summarize(rows)
    assert tally["TOTAL"] == 2
    assert tally[student_grades.STATUS_INSUFFICIENT] == 2
    assert tally[student_grades.STATUS_LIKELY_STALE] == 0


def test_audit_report_hides_identifiers_and_lists_candidates(db):
    student_id = db.get_or_create_student(
        student_identity.PROVIDER_THINKIFIC_EMAIL, "tajna@example.com")
    db.set_student_grade(student_id, 6)
    conn = db._connection()
    conn.execute(
        "INSERT INTO thinkific_progress_imports (report_month, course_key, "
        " course_name, grade, source_sha256, row_count) "
        "VALUES ('2026-09', 'grade_7', 'M7', 7, 'x', 1)")
    conn.execute(
        "INSERT INTO thinkific_progress_snapshots (import_id, student_id, "
        " report_month, course_key, course_name, grade) "
        "VALUES (1, ?, '2026-09', 'grade_7', 'M7', 7)", (student_id,))
    conn.commit()

    text = student_grade_audit.format_report(
        student_grade_audit.collect(database=db))
    assert "@" not in text and "tajna" not in text
    assert "SAFE_REVIEW_CANDIDATES (1)" in text
    assert "READ_ONLY" in text
    assert student_grades.STATUS_LIKELY_STALE in text


def test_audit_cli_exits_zero_on_a_healthy_database(db, capsys):
    assert student_grade_audit.main([]) == 0
    out = capsys.readouterr().out
    assert "=== SUMMARY ===" in out


def test_audit_cli_fails_safely_when_the_database_is_unreadable(monkeypatch, capsys):
    class Broken:
        def list_students(self, *args, **kwargs):
            raise reporting_db.ReportingUnavailable("student_list_failed:Boom")

        def close(self):
            pass

    reporting_db.set_database(Broken())
    try:
        assert student_grade_audit.main([]) == 1
    finally:
        reporting_db.set_database(None)
    out = capsys.readouterr().out
    assert "audit: FAILED" in out
    for forbidden in ("libsql://", "TURSO", "token"):
        assert forbidden not in out
