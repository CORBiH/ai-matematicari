"""Faza 3C — administratorske rute izvještaja za roditelja.

DVIJE TVRDNJE:
  1. SIGURNOST — generisanje, snimanje i PDF su iza administratorske prijave i
     CSRF-a; tutorski token ovdje ne znači ništa.
  2. TROŠAK JE OGRANIČEN — model se zove TAČNO jednom, i to samo na izričito
     „Generiši". Otvaranje stranice, snimanje izmjena i preuzimanje PDF-a ne
     zovu model NIKAD. To se mjeri brojačem, ne pretpostavlja.

Druga tvrdnja je razlog zašto ovaj fajl postoji odvojeno: administrator koji
uređuje tekst klikće često, a svaki klik koji bi tiho platio poziv bio bi kvar
koji se primijeti tek na računu.
"""
import re

import pytest

from matbot import parent_report, report_facts, reporting_db, reporting_schema
from matbot.student_identity import PROVIDER_THINKIFIC_EMAIL

from tests.test_parent_report import good_narrative, payload
from tests.test_thinkific_progress_import import build_v1, migrate, rows

libsql = pytest.importorskip("libsql")
pypdf = pytest.importorskip("pypdf")

PASSWORD = "test-admin-lozinka-1234"


@pytest.fixture(autouse=True)
def fresh_login_limiter(flask_app):
    from matbot.admin_reports import LOGIN_LIMITER_KEY

    flask_app.config.pop(LOGIN_LIMITER_KEY, None)
    yield
    flask_app.config.pop(LOGIN_LIMITER_KEY, None)


@pytest.fixture
def admin_env(monkeypatch):
    monkeypatch.setenv("MATBOT_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.setenv("MATBOT_ADMIN_COOKIE_SECURE", "disabled")
    return PASSWORD


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
    yield path
    reporting_db.wait_for_pending_writes()
    reporting_db.set_database(None)


@pytest.fixture
def student(db):
    student_id = reporting_db.get_database().get_or_create_student(
        PROVIDER_THINKIFIC_EMAIL, "learner@example.com")
    reporting_db.get_database().update_student_profile(
        student_id, display_name="Đžemal Šćepanović")
    # NOV IZVJESTAJ TRAZI POTVRDJEN RAZRED (verzija 4). Potvrda je
    # administratorska radnja, pa je i ovdje ide kroz istu funkciju.
    reporting_db.get_database().set_student_grade(student_id, 6)
    return student_id


class CountingLLM:
    """Broji svaki poziv modela. Nula je očekivana vrijednost gotovo svugdje."""

    def __init__(self):
        self.calls = 0

    def report_turn(self, instructions, input_text):
        self.calls += 1
        from matbot.llm import LLMResult

        return LLMResult(output=good_narrative())


@pytest.fixture
def counter(monkeypatch):
    """Zamijeni PRAVI OpenAI adapter brojačem — nijedan test ne smije na mrežu."""
    spy = CountingLLM()
    monkeypatch.setattr("matbot.llm.OpenAIPracticeLLM", lambda *a, **k: spy)
    return spy


@pytest.fixture
def admin(client, admin_env):
    token = _csrf_from(client.get("/admin/reports/login"))
    response = client.post("/admin/reports/login",
                           data={"csrf_token": token, "password": PASSWORD})
    assert response.status_code == 302
    return client


def _csrf_from(response):
    match = re.search(r'name="csrf_token" value="([^"]+)"',
                      response.get_data(as_text=True))
    return match.group(1) if match else ""


def _page_csrf(admin, student_id, month="2026-08"):
    return _csrf_from(admin.get("/admin/reports/student/%d?month=%s"
                                % (student_id, month)))


def _generate(admin, student_id, month="2026-08", csrf=None):
    return admin.post(
        "/admin/reports/student/%d/generate?month=%s" % (student_id, month),
        data={"csrf_token": csrf if csrf is not None
              else _page_csrf(admin, student_id, month)})


def _seed_draft(student_id, month="2026-08"):
    facts = report_facts.build_ai_facts(payload())
    snapshot = parent_report.metrics_snapshot(
        facts, model="m", prompt_version="v")
    parent_report.save_narrative(student_id, month, good_narrative(), snapshot)
    return facts


# ---------------------------------------------------------------------------
# Sigurnost
# ---------------------------------------------------------------------------
def test_unauthenticated_generate_is_denied(client, admin_env, db, student, counter):
    response = client.post(
        "/admin/reports/student/%d/generate?month=2026-08" % student,
        data={"csrf_token": "x"})
    assert response.status_code == 403
    assert counter.calls == 0
    assert rows(db, "SELECT COUNT(*) FROM monthly_reports")[0][0] == 0


def test_unauthenticated_save_is_denied(client, admin_env, db, student):
    response = client.post(
        "/admin/reports/student/%d/save?month=2026-08" % student,
        data={"csrf_token": "x", "summary": "upad"})
    assert response.status_code == 403
    assert rows(db, "SELECT COUNT(*) FROM monthly_reports")[0][0] == 0


def test_unauthenticated_pdf_is_denied(client, admin_env, db, student):
    _seed_draft(student)
    response = client.get("/admin/reports/student/%d/pdf?month=2026-08" % student)
    assert response.status_code in (302, 403)
    assert b"%PDF" not in response.data


def test_tutor_token_cannot_generate_a_report(client, admin_env, db, student,
                                              counter):
    """Tutorski token je pripisivanje, ne ovlaštenje (Faza 1 doktrina)."""
    from matbot import auth

    response = client.post(
        "/admin/reports/student/%d/generate?month=2026-08" % student,
        data={"csrf_token": "x"},
        headers={"X-Tutor-Token": auth.issue_token(None)})
    assert response.status_code == 403
    assert counter.calls == 0


def test_generate_without_csrf_is_rejected(admin, db, student, counter):
    response = _generate(admin, student, csrf="pogresan-token")
    assert response.status_code == 400
    assert counter.calls == 0
    assert rows(db, "SELECT COUNT(*) FROM monthly_reports")[0][0] == 0


def test_save_without_csrf_is_rejected(admin, db, student):
    _seed_draft(student)
    response = admin.post(
        "/admin/reports/student/%d/save?month=2026-08" % student,
        data={"csrf_token": "pogresan", "summary": "upad"})
    assert response.status_code == 400
    saved = parent_report.load_saved(student, "2026-08")
    assert saved["narrative"]["summary"].startswith("Tokom mjeseca")


# ---------------------------------------------------------------------------
# Trošak: koliko poziva modela košta koji klik
# ---------------------------------------------------------------------------
def test_opening_the_page_makes_no_model_call(admin, db, student, counter):
    response = admin.get("/admin/reports/student/%d?month=2026-08" % student)
    assert response.status_code == 200
    assert counter.calls == 0


def test_generate_makes_exactly_one_model_call(admin, db, student, counter):
    response = _generate(admin, student)
    assert response.status_code == 302
    assert counter.calls == 1
    assert rows(db, "SELECT COUNT(*) FROM monthly_reports")[0][0] == 1


def test_saving_edits_makes_no_model_call(admin, db, student, counter):
    _seed_draft(student)
    response = admin.post(
        "/admin/reports/student/%d/save?month=2026-08" % student,
        data={"csrf_token": _page_csrf(admin, student),
              "summary": "Ručno uređen sažetak.",
              "strengths": "Prva stavka.\nDruga stavka.",
              "focus_areas": "", "next_month_recommendations": "",
              "instructor_comment": "Komentar instruktora."})
    assert response.status_code == 302
    assert counter.calls == 0


def test_pdf_download_makes_no_model_call(admin, db, student, counter):
    _seed_draft(student)
    response = admin.get("/admin/reports/student/%d/pdf?month=2026-08" % student)
    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF")
    assert counter.calls == 0


def test_regenerating_the_pdf_still_makes_no_model_call(admin, db, student, counter):
    _seed_draft(student)
    for _ in range(3):
        assert admin.get("/admin/reports/student/%d/pdf?month=2026-08"
                         % student).status_code == 200
    assert counter.calls == 0


# ---------------------------------------------------------------------------
# Tok: generiši → uredi → ponovo otvori → PDF
# ---------------------------------------------------------------------------
def test_generated_draft_is_visible_when_the_page_is_reopened(admin, db, student,
                                                              counter):
    _generate(admin, student)
    html = admin.get("/admin/reports/student/%d?month=2026-08"
                     % student).get_data(as_text=True)
    assert "Tokom mjeseca" in html
    assert "Komentar instruktora" in html
    assert counter.calls == 1


def test_admin_edits_survive_reopening(admin, db, student, counter):
    _generate(admin, student)
    admin.post("/admin/reports/student/%d/save?month=2026-08" % student,
               data={"csrf_token": _page_csrf(admin, student),
                     "summary": "Rečenica koju je napisao instruktor.",
                     "strengths": "Redovan rad.",
                     "focus_areas": "Vrijedi uvježbati razlomke.",
                     "next_month_recommendations": "Kraće vježbanje.",
                     "instructor_comment": "Komentar koji mora ostati."})
    html = admin.get("/admin/reports/student/%d?month=2026-08"
                     % student).get_data(as_text=True)
    assert "Rečenica koju je napisao instruktor." in html
    assert "Komentar koji mora ostati." in html
    assert counter.calls == 1


def test_regeneration_keeps_the_instructor_comment(admin, db, student, counter):
    _generate(admin, student)
    admin.post("/admin/reports/student/%d/save?month=2026-08" % student,
               data={"csrf_token": _page_csrf(admin, student),
                     "summary": "Prvi tekst.", "strengths": "",
                     "focus_areas": "", "next_month_recommendations": "",
                     "instructor_comment": "Komentar koji mora preživjeti."})
    _generate(admin, student)

    saved = parent_report.load_saved(student, "2026-08")
    assert saved["instructor_comment"] == "Komentar koji mora preživjeti."
    assert saved["narrative"]["summary"].startswith("Tokom mjeseca")
    assert counter.calls == 2


def test_multiline_list_fields_become_separate_items(admin, db, student):
    _seed_draft(student)
    admin.post("/admin/reports/student/%d/save?month=2026-08" % student,
               data={"csrf_token": _page_csrf(admin, student),
                     "summary": "Sažetak.",
                     "strengths": "Prva.\n\nDruga.\n   \nTreća.",
                     "focus_areas": "", "next_month_recommendations": "",
                     "instructor_comment": ""})
    saved = parent_report.load_saved(student, "2026-08")
    assert saved["narrative"]["strengths"] == ["Prva.", "Druga.", "Treća."]


def test_pdf_without_a_saved_draft_is_not_invented(admin, db, student, counter):
    response = admin.get("/admin/reports/student/%d/pdf?month=2026-08" % student)
    assert response.status_code == 404
    assert counter.calls == 0


def test_pdf_uses_the_saved_snapshot_not_todays_data(admin, db, student, counter):
    """Dokument mora ostati ono što je administrator odobrio (Dio 14)."""
    _generate(admin, student)
    response = admin.get("/admin/reports/student/%d/pdf?month=2026-08" % student)
    import io as _io

    text = "\n".join(p.extract_text()
                     for p in pypdf.PdfReader(_io.BytesIO(response.data)).pages)
    assert "Đžemal Šćepanović" in text
    assert "@" not in text
    assert counter.calls == 1


def test_pdf_filename_carries_no_identifier(admin, db, student):
    _seed_draft(student)
    disposition = admin.get("/admin/reports/student/%d/pdf?month=2026-08"
                            % student).headers["Content-Disposition"]
    assert "izvjestaj-" in disposition and disposition.endswith('.pdf"')
    assert "@" not in disposition


# ---------------------------------------------------------------------------
# Izolacija kvara
# ---------------------------------------------------------------------------
def test_model_failure_shows_a_safe_message_and_writes_nothing(admin, db, student,
                                                               monkeypatch):
    from matbot.llm import LLMTimeout

    class Failing:
        def report_turn(self, instructions, input_text):
            raise LLMTimeout("timeout")

    monkeypatch.setattr("matbot.llm.OpenAIPracticeLLM", lambda *a, **k: Failing())
    response = _generate(admin, student)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert parent_report.SAFE_AI_ERROR in html
    # Nikad interni kod, nikad trag greške u HTML-u (pravilo 7).
    for leak in ("LLMTimeout", "report_ai_call_failed", "Traceback",
                 "report_ai_rejected"):
        assert leak not in html, leak
    assert rows(db, "SELECT COUNT(*) FROM monthly_reports")[0][0] == 0


def test_model_failure_leaves_an_existing_draft_untouched(admin, db, student,
                                                          monkeypatch):
    from matbot.llm import LLMTimeout

    _generate(admin, student)
    admin.post("/admin/reports/student/%d/save?month=2026-08" % student,
               data={"csrf_token": _page_csrf(admin, student),
                     "summary": "Tekst koji mora preživjeti.", "strengths": "",
                     "focus_areas": "", "next_month_recommendations": "",
                     "instructor_comment": "I komentar."})

    class Failing:
        def report_turn(self, instructions, input_text):
            raise LLMTimeout("timeout")

    monkeypatch.setattr("matbot.llm.OpenAIPracticeLLM", lambda *a, **k: Failing())
    _generate(admin, student)

    saved = parent_report.load_saved(student, "2026-08")
    assert saved["narrative"]["summary"] == "Tekst koji mora preživjeti."
    assert saved["instructor_comment"] == "I komentar."


def test_rejected_model_output_never_reaches_the_page(admin, db, student,
                                                      monkeypatch):
    class Inventing:
        def report_turn(self, instructions, input_text):
            from matbot.llm import LLMResult

            return LLMResult(output=good_narrative(
                summary="Tačnost je iznosila 91 posto ovog mjeseca."))

    monkeypatch.setattr("matbot.llm.OpenAIPracticeLLM", lambda *a, **k: Inventing())
    html = _generate(admin, student).get_data(as_text=True)

    assert parent_report.SAFE_AI_ERROR in html
    assert "91 posto" not in html
    assert rows(db, "SELECT COUNT(*) FROM monthly_reports")[0][0] == 0


def test_report_failure_does_not_affect_the_tutor(admin, client, db, student,
                                                  monkeypatch, fake_llm):
    """Izvještajni put i tutorski put ne dijele ni stanje ni izvršavanje."""
    from matbot.llm import LLMTimeout

    class Failing:
        def report_turn(self, instructions, input_text):
            raise LLMTimeout("timeout")

    monkeypatch.setattr("matbot.llm.OpenAIPracticeLLM", lambda *a, **k: Failing())
    _generate(admin, student)

    from matbot import auth

    response = client.post("/api/ai-tutor/chat",
                           json={"grade": 6, "mode": "explain",
                                 "student_message": "Šta je razlomak?"},
                           headers={"X-Tutor-Token": auth.issue_token(None)})
    assert response.status_code == 200


def test_admin_page_never_shows_an_email(admin, db, student, counter):
    _generate(admin, student)
    html = admin.get("/admin/reports/student/%d?month=2026-08"
                     % student).get_data(as_text=True)
    assert not re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", html)
