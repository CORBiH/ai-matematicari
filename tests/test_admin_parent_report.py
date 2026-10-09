"""Faza 3C — administratorske rute izvještaja za roditelja.

DVIJE TVRDNJE:
  1. SIGURNOST — generisanje, snimanje i PDF su iza administratorske prijave i
     CSRF-a; tutorski token ovdje ne znači ništa.
  2. TROŠAK JE OGRANIČEN — model se zove NAJVIŠE jednom po pokušaju i samo
     na izričito „Generiši". Nema automatskog retryja ni poslije timeouta,
     provider greške ili nepoznatog ishoda. Otvaranje stranice, snimanje
     izmjena i preuzimanje PDF-a ne zovu model NIKAD. To se mjeri brojačem,
     ne pretpostavlja.

Druga tvrdnja je razlog zašto ovaj fajl postoji odvojeno: administrator koji
uređuje tekst klikće često, a svaki klik koji bi tiho platio poziv bio bi kvar
koji se primijeti tek na računu.
"""
import io
import re
import threading
import zipfile

import pytest
from werkzeug.datastructures import MultiDict

from matbot import (activity, admin_reports, parent_report, report_facts, report_input,
                    reporting_db, reporting_schema)
from matbot.student_identity import PROVIDER_THINKIFIC_EMAIL

from tests.test_parent_report import good_narrative, payload
from tests.fixtures.thinkific import build_csv, learner
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


def test_saved_report_keeps_its_name_after_current_roster_name_changes(
        admin, db, student, counter):
    report_input.import_progress_files("2026-07", {"grade_6": build_csv([
        learner("learner@example.com", first="Staro", last="Ime 6")])})
    assert _generate(admin, student).status_code == 302
    assert counter.calls == 1

    report_input.import_progress_files("2026-09", {"grade_7": build_csv([
        learner("learner@example.com", first="Novo", last="Ime 7")])})
    assert rows(db, "SELECT display_name FROM students") == [("Novo Ime 7",)]

    response = admin.get(
        "/admin/reports/student/%d/pdf?month=2026-08" % student)
    text = "\n".join(page.extract_text() for page in
                     pypdf.PdfReader(io.BytesIO(response.data)).pages)
    assert "Staro Ime 6" in text
    assert "Novo Ime 7" not in text


def test_legacy_saved_report_gets_its_old_name_frozen_before_thinkific_rename(
        admin, db, student, counter):
    report_input.import_progress_files("2026-07", {"grade_6": build_csv([
        learner("learner@example.com", first="Historijsko", last="Ime 6")])})
    _seed_draft(student)  # stari format: snapshot još nema student.label
    assert parent_report.load_saved(student, "2026-08")["snapshot"].get(
        "student") is None

    report_input.import_progress_files("2026-09", {"grade_7": build_csv([
        learner("learner@example.com", first="Trenutno", last="Ime 7")])})

    saved = parent_report.load_saved(student, "2026-08")
    assert saved["snapshot"]["student"]["label"] == "Historijsko Ime 6"
    response = admin.get(
        "/admin/reports/student/%d/pdf?month=2026-08" % student)
    text = "\n".join(page.extract_text() for page in
                     pypdf.PdfReader(io.BytesIO(response.data)).pages)
    assert "Historijsko Ime 6" in text
    assert "Trenutno Ime 7" not in text
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


def _add_report_activity(student_id, key, month="2026-08"):
    reporting_db.get_database().record_learning_activity(student_id, [
        activity.ActivityEvent(
            activity.PRACTICE_TASK_PRESENTED, key, mode="practice", grade=6,
            occurred_at=month + "-10 10:00:00")])


def test_bulk_generation_keeps_working_when_one_student_fails(
        admin, db, student, counter):
    database = reporting_db.get_database()
    _add_report_activity(student, "bulk-good")
    unconfirmed = database.get_or_create_student(
        PROVIDER_THINKIFIC_EMAIL, "unconfirmed@example.com")
    database.update_student_profile(unconfirmed, display_name="Nepotvrđen Učenik")
    _add_report_activity(unconfirmed, "bulk-bad")
    token = _csrf_from(admin.get("/admin/reports/students?month=2026-08"))

    response = admin.post("/admin/reports/bulk/generate", data=MultiDict([
        ("csrf_token", token), ("month", "2026-08"),
        ("student_ids", str(student)),
        ("student_ids", str(unconfirmed)),
    ]))

    assert response.status_code == 200
    results = {item["student_id"]: item for item in response.get_json()["results"]}
    assert results[student]["status"] == "ready"
    assert results[unconfirmed]["status"] == "error"
    assert counter.calls == 1
    assert parent_report.load_saved(student, "2026-08") is not None
    assert parent_report.load_saved(unconfirmed, "2026-08") is None


def test_bulk_generation_reuses_a_saved_report_without_a_model_call(
        admin, db, student, counter):
    _add_report_activity(student, "bulk-reuse")
    _seed_draft(student)
    token = _csrf_from(admin.get("/admin/reports/students?month=2026-08"))
    response = admin.post("/admin/reports/bulk/generate", data={
        "csrf_token": token, "month": "2026-08",
        "student_ids": str(student),
    })
    assert response.get_json()["results"][0]["status"] == "reused"
    assert counter.calls == 0


def test_bulk_generation_runs_two_independent_model_waits_concurrently(
        admin, db, student, monkeypatch):
    """Barijera dokazuje preklapanje bez krhkog wall-clock praga."""
    database = reporting_db.get_database()
    other = database.create_student("Drugi Učenik", 7,
                                    "bulk-concurrent@example.com")
    _add_report_activity(student, "bulk-concurrent-one")
    _add_report_activity(other, "bulk-concurrent-two")
    barrier = threading.Barrier(2, timeout=2.0)
    calls = []

    class ConcurrentLLM:
        def report_turn(self, instructions, input_text):
            calls.append(1)
            barrier.wait()
            from matbot.llm import LLMResult
            return LLMResult(output=good_narrative())

    monkeypatch.setattr("matbot.llm.OpenAIPracticeLLM", ConcurrentLLM)
    token = _csrf_from(admin.get("/admin/reports/students?month=2026-08"))
    response = admin.post("/admin/reports/bulk/generate", data=MultiDict([
        ("csrf_token", token), ("month", "2026-08"),
        ("student_ids", str(student)), ("student_ids", str(other)),
    ]))

    assert response.status_code == 200
    assert [item["status"] for item in response.get_json()["results"]] == [
        "ready", "ready"]
    assert len(calls) == 2


def test_process_wide_limit_covers_simultaneous_bulk_and_individual_requests(
        flask_app, admin_env, db, student, monkeypatch):
    """Dva HTTP bulk zahtjeva i individualni put dijele ISTA dva mjesta.

    Drugi zahtjevi završavaju dok su prva dva modela još blokirana. Time test
    ujedno dokazuje da se ne čeka u neograničenom redu i da modelski poziv ne
    drži zajednički DB lock.
    """
    database = reporting_db.get_database()
    others = [
        database.create_student("Učenik %d" % index, 7,
                                "global-limit-%d@example.com" % index)
        for index in range(2, 6)
    ]
    student_ids = [student] + others
    for index, student_id in enumerate(student_ids):
        _add_report_activity(student_id, "global-limit-%d" % index)

    state = {"active": 0, "maximum": 0, "calls": 0}
    state_lock = threading.Lock()
    two_active = threading.Event()
    release = threading.Event()

    class BlockingLLM:
        def report_turn(self, instructions, input_text):
            with state_lock:
                state["active"] += 1
                state["calls"] += 1
                state["maximum"] = max(state["maximum"], state["active"])
                if state["active"] == 2:
                    two_active.set()
            try:
                assert release.wait(5.0)
                from matbot.llm import LLMResult
                return LLMResult(output=good_narrative())
            finally:
                with state_lock:
                    state["active"] -= 1

    monkeypatch.setattr("matbot.llm.OpenAIPracticeLLM", BlockingLLM)

    def logged_in_client():
        client = flask_app.test_client()
        token = _csrf_from(client.get("/admin/reports/login"))
        response = client.post(
            "/admin/reports/login",
            data={"csrf_token": token, "password": PASSWORD})
        assert response.status_code == 302
        return client

    first_client = logged_in_client()
    second_client = logged_in_client()
    individual_client = logged_in_client()
    first_token = _csrf_from(first_client.get(
        "/admin/reports/students?month=2026-08"))
    second_token = _csrf_from(second_client.get(
        "/admin/reports/students?month=2026-08"))
    individual_token = _page_csrf(
        individual_client, student_ids[4])

    first_responses = []
    first_thread = threading.Thread(target=lambda: first_responses.append(
        first_client.post("/admin/reports/bulk/generate", data=MultiDict([
            ("csrf_token", first_token), ("month", "2026-08"),
            ("student_ids", str(student_ids[0])),
            ("student_ids", str(student_ids[1])),
        ]))))
    first_thread.start()
    try:
        assert two_active.wait(5.0)

        second = second_client.post(
            "/admin/reports/bulk/generate", data=MultiDict([
                ("csrf_token", second_token), ("month", "2026-08"),
                ("student_ids", str(student_ids[2])),
                ("student_ids", str(student_ids[3])),
            ]))
        individual = individual_client.post(
            "/admin/reports/student/%d/generate?month=2026-08"
            % student_ids[4], data={"csrf_token": individual_token})

        assert second.status_code == 200
        assert [item["status"] for item in second.get_json()["results"]] == [
            "error", "error"]
        assert {item["message"] for item in second.get_json()["results"]} == {
            admin_reports.ERROR_GENERATION_BUSY}
        assert individual.status_code == 200
        assert admin_reports.ERROR_GENERATION_BUSY in individual.get_data(
            as_text=True)
        assert state["maximum"] == 2
        assert state["calls"] == 2
    finally:
        release.set()
        first_thread.join(5.0)

    assert not first_thread.is_alive()
    assert first_responses[0].status_code == 200
    assert [item["status"]
            for item in first_responses[0].get_json()["results"]] == [
                "ready", "ready"]
    assert state == {"active": 0, "maximum": 2, "calls": 2}


def test_bulk_timeout_is_not_retried_or_saved(
        admin, db, student, monkeypatch):
    from matbot.llm import LLMTimeout

    _add_report_activity(student, "bulk-timeout")
    calls = []

    class TimeoutThenWouldBeReady:
        def report_turn(self, instructions, input_text):
            calls.append(1)
            raise LLMTimeout(
                "timeout",
                diagnostics={"exception_class": "APITimeoutError"})

    monkeypatch.setattr(
        "matbot.llm.OpenAIPracticeLLM", TimeoutThenWouldBeReady)
    token = _csrf_from(admin.get("/admin/reports/students?month=2026-08"))
    response = admin.post("/admin/reports/bulk/generate", data={
        "csrf_token": token, "month": "2026-08",
        "student_ids": str(student),
    })

    assert response.get_json()["results"][0]["status"] == "error"
    assert len(calls) == 1
    assert rows(db, "SELECT COUNT(*) FROM monthly_reports")[0][0] == 0


@pytest.mark.parametrize("exception_class", [
    "APIConnectionError", "RateLimitError", "InternalServerError",
    "AuthenticationError",
])
def test_bulk_does_not_retry_provider_or_connection_failure(
        admin, db, student, monkeypatch, exception_class):
    from matbot.llm import LLMUnavailable

    _add_report_activity(student, "bulk-permanent")
    calls = []

    class ProviderFailure:
        def report_turn(self, instructions, input_text):
            calls.append(1)
            raise LLMUnavailable(
                "provider",
                diagnostics={"exception_class": exception_class})

    monkeypatch.setattr("matbot.llm.OpenAIPracticeLLM", ProviderFailure)
    token = _csrf_from(admin.get("/admin/reports/students?month=2026-08"))
    response = admin.post("/admin/reports/bulk/generate", data={
        "csrf_token": token, "month": "2026-08",
        "student_ids": str(student),
    })

    assert response.get_json()["results"][0]["status"] == "error"
    assert len(calls) == 1
    assert rows(db, "SELECT COUNT(*) FROM monthly_reports")[0][0] == 0


def test_bulk_does_not_retry_an_unknown_model_outcome(
        admin, db, student, monkeypatch):
    _add_report_activity(student, "bulk-unknown")
    calls = []

    class UnknownFailure:
        def report_turn(self, instructions, input_text):
            calls.append(1)
            raise RuntimeError("unknown outcome")

    monkeypatch.setattr("matbot.llm.OpenAIPracticeLLM", UnknownFailure)
    token = _csrf_from(admin.get("/admin/reports/students?month=2026-08"))
    response = admin.post("/admin/reports/bulk/generate", data={
        "csrf_token": token, "month": "2026-08",
        "student_ids": str(student),
    })

    assert response.get_json()["results"][0]["status"] == "error"
    assert len(calls) == 1
    assert rows(db, "SELECT COUNT(*) FROM monthly_reports")[0][0] == 0


def test_same_student_cannot_generate_twice_concurrently(
        db, student, monkeypatch):
    from matbot.llm import LLMResult

    _add_report_activity(student, "same-student-inflight")
    entered = threading.Event()
    release = threading.Event()
    calls = []

    class BlockingLLM:
        def report_turn(self, instructions, input_text):
            calls.append(1)
            entered.set()
            assert release.wait(2.0)
            return LLMResult(output=good_narrative())

    monkeypatch.setattr("matbot.llm.OpenAIPracticeLLM", BlockingLLM)
    first = []
    worker = threading.Thread(target=lambda: first.append(
        admin_reports._generate_one_report(
            student, "2026-08", replace=True)))
    worker.start()
    assert entered.wait(2.0)

    duplicate = admin_reports._generate_one_report(
        student, "2026-08", replace=True)
    release.set()
    worker.join(2.0)

    assert duplicate[1:] == ("error", admin_reports.ERROR_GENERATION_IN_PROGRESS)
    assert first and first[0][1] == "ready"
    assert len(calls) == 1
    assert rows(db, "SELECT COUNT(*) FROM monthly_reports")[0][0] == 1


def test_all_declared_narrative_sections_are_saved_and_reports_are_isolated(
        admin, db, student):
    database = reporting_db.get_database()
    other = database.create_student("Drugi Učenik", 7,
                                    "other-edit@example.com")
    _seed_draft(student)
    _seed_draft(other)
    original_other = parent_report.load_saved(other, "2026-08")["narrative"]
    values = {
        "summary": "Novi pregled.",
        "strengths": "Prva snaga.\nDruga snaga.",
        "focus_areas": "Prvo područje.\nDrugo područje.",
        "next_month_recommendations": "Prva preporuka.\nDruga preporuka.",
    }
    response = admin.post(
        "/admin/reports/student/%d/save?month=2026-08" % student,
        data={"csrf_token": _page_csrf(admin, student),
              **values, "instructor_comment": "Ručni komentar."})
    assert response.status_code == 302
    saved = parent_report.load_saved(student, "2026-08")
    assert set(saved["narrative"]) == {
        field["name"] for field in parent_report.NARRATIVE_FIELD_SPECS}
    assert saved["narrative"]["summary"] == "Novi pregled."
    assert saved["narrative"]["focus_areas"] == [
        "Prvo područje.", "Drugo područje."]
    assert saved["instructor_comment"] == "Ručni komentar."
    assert parent_report.load_saved(other, "2026-08")["narrative"] == original_other


def test_bulk_zip_contains_only_selected_saved_reports(
        admin, db, student, counter):
    database = reporting_db.get_database()
    other = database.create_student("Drugi Učenik", 7,
                                    "other-zip@example.com")
    _add_report_activity(student, "zip-first")
    _add_report_activity(other, "zip-second")
    _seed_draft(student)
    _seed_draft(other)
    token = _csrf_from(admin.get("/admin/reports/students?month=2026-08"))

    response = admin.post("/admin/reports/bulk/download", data={
        "csrf_token": token, "month": "2026-08",
        "student_ids": str(student),
    })

    assert response.status_code == 200
    assert response.mimetype == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        names = archive.namelist()
        assert len(names) == 1
        assert names[0].endswith("-2026-08.pdf")
        assert archive.read(names[0]).startswith(b"%PDF")
    assert response.headers["X-Reports-Included"] == "1"
    assert counter.calls == 0


def test_bulk_zip_rejects_support_and_test_accounts(admin, db, student):
    database = reporting_db.get_database()
    support = database.create_student("Učenik Podrške", 7,
                                      "support-zip@example.com")
    database.set_student_account_types([support], "SUPPORT")
    _add_report_activity(student, "zip-regular")
    _add_report_activity(support, "zip-support")
    _seed_draft(student)
    token = _csrf_from(admin.get(
        "/admin/reports/students?month=2026-08&include_support=1"))

    response = admin.post("/admin/reports/bulk/download", data=MultiDict([
        ("csrf_token", token), ("month", "2026-08"),
        ("student_ids", str(student)), ("student_ids", str(support)),
    ]))
    assert response.status_code == 400


def test_parent_facing_class_observation_has_a_report_level_override(
        admin, db, student, counter):
    report_payload, facts = parent_report.build_facts(student, "2026-08")
    snapshot = parent_report.metrics_snapshot(
        facts, model="m", prompt_version="v", parent_comments=[{
            "date": "2026-08-12", "comment": "Prvobitno zapažanje."}])
    parent_report.save_narrative(
        student, "2026-08", good_narrative(), snapshot)

    response = admin.post(
        "/admin/reports/student/%d/save?month=2026-08" % student,
        data={"csrf_token": _page_csrf(admin, student),
              "summary": "Sažetak.", "strengths": "Redovan rad.",
              "focus_areas": "Uvježbati razlomke.",
              "next_month_recommendations": "Nastaviti vježbanje.",
              "parent_comment_0": "Ispravljeno zapažanje za roditelja.",
              "instructor_comment": ""})
    assert response.status_code == 302
    saved = parent_report.load_saved(student, "2026-08")
    assert saved["parent_comments"] == [{
        "date": "2026-08-12",
        "comment": "Ispravljeno zapažanje za roditelja.",
    }]
    assert saved["snapshot"]["facts"] == facts

    report_page = admin.get(
        "/admin/reports/student/%d?month=2026-08" % student)
    assert "Ispravljeno zapažanje za roditelja.".encode() in report_page.data
    assert "Prosječna aktivnost".encode() not in report_page.data

    pdf = admin.get("/admin/reports/student/%d/pdf?month=2026-08" % student)
    text = "\n".join(page.extract_text() for page in
                     pypdf.PdfReader(io.BytesIO(pdf.data)).pages)
    assert "Ispravljeno zapažanje za roditelja" in text
    assert "Prvobitno zapažanje" not in text
    preview = admin.get(
        "/admin/reports/student/%d/pdf?month=2026-08&preview=1" % student)
    assert preview.headers["Content-Disposition"].startswith("inline;")
    assert counter.calls == 0

    assert _generate(admin, student).status_code == 302
    regenerated = parent_report.load_saved(student, "2026-08")
    assert regenerated["parent_comments"] == saved["parent_comments"]
    assert regenerated["snapshot"]["parent_comments_edited"] is True
    assert regenerated["snapshot"]["facts"]["instruction"][
        "teacher_comments"] == saved["parent_comments"]
    assert counter.calls == 1


def test_grade_seven_bulk_flow_preserves_edit_in_download(
        admin, db, counter):
    database = reporting_db.get_database()
    first = database.create_student("Prvi Sedmi", 7, "first7@example.com")
    second = database.create_student("Drugi Sedmi", 7, "second7@example.com")
    support = database.create_student("Podrška Sedmi", 7,
                                      "support7-flow@example.com")
    test_user = database.create_student("Test Sedmi", 7,
                                        "test7-flow@example.com")
    database.set_student_account_types([support], "SUPPORT")
    database.set_student_account_types([test_user], "TEST")
    for student_id, key in ((first, "flow-first"), (second, "flow-second"),
                            (support, "flow-support"),
                            (test_user, "flow-test")):
        _add_report_activity(student_id, key)

    listing = admin.get(
        "/admin/reports/students?month=2026-08&grade=7")
    html = listing.get_data(as_text=True)
    assert "Prvi Sedmi" in html and "Drugi Sedmi" in html
    assert "Podrška Sedmi" not in html and "Test Sedmi" not in html
    token = _csrf_from(listing)
    selected = MultiDict([
        ("csrf_token", token), ("month", "2026-08"),
        ("student_ids", str(first)), ("student_ids", str(second)),
    ])
    generated = admin.post("/admin/reports/bulk/generate", data=selected)
    assert [item["status"] for item in generated.get_json()["results"]] == [
        "ready", "ready"]
    assert counter.calls == 2

    edited_text = "Ručno uređeni sažetak koji mora biti u ZIP-u."
    saved_edit = admin.post(
        "/admin/reports/student/%d/save?month=2026-08" % first,
        data={"csrf_token": _page_csrf(admin, first),
              "summary": edited_text, "strengths": "Redovan rad.",
              "focus_areas": "Uvježbati razlomke.",
              "next_month_recommendations": "Nastaviti vježbanje.",
              "instructor_comment": ""})
    assert saved_edit.status_code == 302

    reused = admin.post("/admin/reports/bulk/generate", data=selected)
    assert [item["status"] for item in reused.get_json()["results"]] == [
        "reused", "reused"]
    assert counter.calls == 2
    assert parent_report.load_saved(first, "2026-08")["narrative"][
        "summary"] == edited_text

    archive_response = admin.post("/admin/reports/bulk/download", data=selected)
    assert archive_response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive_response.data)) as archive:
        names = archive.namelist()
        assert len(names) == 2 and len(set(names)) == 2
        assert all(name.endswith("-2026-08.pdf") for name in names)
        extracted = []
        for name in names:
            extracted.extend(page.extract_text() for page in
                             pypdf.PdfReader(io.BytesIO(
                                 archive.read(name))).pages)
    combined = "\n".join(extracted)
    assert edited_text in combined
    assert "Podrška Sedmi" not in combined and "Test Sedmi" not in combined
    assert counter.calls == 2


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
