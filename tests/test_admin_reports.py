"""Faza 3B — privatna administratorska stranica uvoza Thinkific napretka.

TRI ODVOJENE TVRDNJE:
  1. SIGURNOST — stranica nije javna, tutorski token je ne otvara, POST bez CSRF
     ne prolazi, a bez konfigurisane lozinke rute se ponašaju kao da ne postoje.
  2. KONTROLER JE TANAK — sva pravila ostaju u Fazi 3A; ruta samo validira
     oblik zahtjeva i prikazuje rezultat.
  3. PRIKAZ JE ISKREN — djelimičan uspjeh se ne prikazuje kao uspjeh, nedostajuć
     podatak se ne prikazuje kao nula, a e-mail se ne prikazuje nikad.

Svi CSV-ovi su SINTETIČKI (`tests/fixtures/thinkific`). Stvarni izvoz s PII-jem
se ovdje ne čita.
"""
import logging
import re

import pytest

from matbot import (activity, admin_auth, admin_reports, report_input,
                    reporting_db, student_identity)
from matbot import thinkific_progress as progress
from matbot import thinkific_upload
from matbot.api import _kontrolni_attempt
from matbot.student_identity import PROVIDER_THINKIFIC_EMAIL

from tests.fixtures.thinkific import GRADE6_SECTIONS, build_csv, learner, with_sections
from tests.test_thinkific_progress_import import build_v1, migrate, rows, simple_csv

libsql = pytest.importorskip("libsql")

PASSWORD = "test-admin-lozinka-1234"
E1 = "student1@example.com"
E2 = "student2@example.com"


@pytest.fixture(autouse=True)
def fresh_login_limiter(flask_app):
    """Brojaci prijave se ne smiju prenositi izmedju testova."""
    from matbot.admin_reports import LOGIN_LIMITER_KEY

    flask_app.config.pop(LOGIN_LIMITER_KEY, None)
    yield
    flask_app.config.pop(LOGIN_LIMITER_KEY, None)


@pytest.fixture
def admin_env(monkeypatch):
    monkeypatch.setenv("MATBOT_ADMIN_PASSWORD", PASSWORD)
    # Testni klijent poštuje `Secure` i ne bi slao kolačić preko http.
    monkeypatch.setenv("MATBOT_ADMIN_COOKIE_SECURE", "disabled")
    return PASSWORD


def _make_db(tmp_path, monkeypatch, *, migrated=True):
    path = str(tmp_path / "reporting.db")
    build_v1(path)
    if migrated:
        migrate(path)
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://test.invalid")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token-not-real")
    database = reporting_db.ReportingDatabase(
        connect_factory=lambda: libsql.connect(path, timeout=10.0,
                                               _check_same_thread=False))
    reporting_db.set_database(database)
    return path


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = _make_db(tmp_path, monkeypatch, migrated=True)
    yield path
    reporting_db.wait_for_pending_writes()
    reporting_db.set_database(None)


@pytest.fixture
def db_v1(tmp_path, monkeypatch):
    """Baza koja je JOŠ na šemi v1 — uvoz mora pasti zatvoreno."""
    path = _make_db(tmp_path, monkeypatch, migrated=False)
    yield path
    reporting_db.wait_for_pending_writes()
    reporting_db.set_database(None)


@pytest.fixture
def admin(client, admin_env):
    """Prijavljen administratorski klijent (koristi postojeći `client`)."""
    token = _csrf_from(client.get("/admin/reports/login"))
    response = client.post("/admin/reports/login",
                           data={"csrf_token": token, "password": PASSWORD})
    assert response.status_code == 302
    return client


def _csrf_from(response):
    html = response.get_data(as_text=True)
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return match.group(1) if match else ""


def _csrf(client, path="/admin/reports"):
    return _csrf_from(client.get(path))


def _upload(client, month="2026-09", files=None, csrf=None, *, approve=True):
    # Većina legacy-route testova provjerava uvoz napretka POZNATOG učenika.
    # Odobrenje je izričita testna priprema; `approve=False` dokazuje da sama
    # ruta više ne može napraviti identitet.
    if approve:
        database = reporting_db.get_database()
        for course_key, (payload, filename) in (files or {}).items():
            if not str(filename).lower().endswith(".csv"):
                continue
            try:
                parsed = progress.parse_progress_csv(payload, course_key, month)
            except progress.ProgressFormatError:
                continue
            for row in parsed.rows:
                email = student_identity.normalize_email(row.email)
                if email:
                    database.get_or_create_student(
                        PROVIDER_THINKIFIC_EMAIL, email, row.display_name)
    data = {"csrf_token": csrf if csrf is not None else _csrf(client),
            "report_month": month}
    for key, (payload, name) in (files or {}).items():
        import io as _io
        data[key] = (_io.BytesIO(payload), name)
    return client.post("/admin/reports/import", data=data,
                       content_type="multipart/form-data")


def _roster_upload(client, route, by_course=None, *, plan_id=None,
                   action_keys=None, csrf=None):
    import io as _io

    by_course = by_course or {}
    data = {"csrf_token": csrf if csrf is not None else _csrf(client),
            "report_month": "2026-09"}
    for grade in range(6, 10):
        key = "grade_%d" % grade
        payload = build_csv(by_course.get(key, []), sections=["OBLAST"])
        data[key] = (_io.BytesIO(payload), key + ".csv")
    if plan_id is not None:
        data["plan_id"] = plan_id
    if action_keys is not None:
        data["action_keys"] = list(action_keys)
    return client.post(route, data=data, content_type="multipart/form-data")


def _course_csv(course_key, email, *, first="Ucenik", last="Test"):
    return build_csv(
        [learner(email, first=first, last=last)],
        sections=thinkific_upload.COURSE_SECTION_HEADERS[course_key])


def _multi_roster_upload(client, route, ordered_files, *, manual_keys=None,
                         plan_id=None, action_keys=None, csrf=None):
    import io as _io

    data = {
        "csrf_token": csrf if csrf is not None else _csrf(client),
        "report_month": "2026-09",
        "thinkific_files": [(_io.BytesIO(payload), filename)
                             for filename, payload in ordered_files],
    }
    if manual_keys is not None:
        data["file_course_keys"] = list(manual_keys)
    if plan_id is not None:
        data["plan_id"] = plan_id
    if action_keys is not None:
        data["action_keys"] = list(action_keys)
    return client.post(route, data=data, content_type="multipart/form-data")


# ---------------------------------------------------------------------------
# 1-7) Autorizacija i sigurnost
# ---------------------------------------------------------------------------
def test_unauthenticated_get_is_redirected_to_login(client, admin_env):
    response = client.get("/admin/reports")
    assert response.status_code == 302
    assert "/admin/reports/login" in response.headers["Location"]


def test_unauthenticated_import_post_is_denied(client, admin_env, db):
    response = _upload(client, files={"grade_6": (simple_csv(), "a.csv")},
                       csrf="x", approve=False)
    assert response.status_code == 403
    assert rows(db, "SELECT COUNT(*) FROM thinkific_progress_snapshots")[0][0] == 0


def test_wrong_password_is_rejected(client, admin_env):
    token = _csrf_from(client.get("/admin/reports/login"))
    response = client.post("/admin/reports/login",
                           data={"csrf_token": token, "password": "pogresna-lozinka"})
    assert response.status_code == 401
    assert client.get("/admin/reports").status_code == 302


def test_correct_password_is_accepted(client, admin_env):
    token = _csrf_from(client.get("/admin/reports/login"))
    assert client.post("/admin/reports/login",
                       data={"csrf_token": token, "password": PASSWORD}
                       ).status_code == 302
    assert client.get("/admin/reports").status_code == 200


def test_post_without_csrf_is_rejected(admin, db):
    response = _upload(admin, files={"grade_6": (simple_csv(), "a.csv")},
                       csrf="", approve=False)
    assert response.status_code == 403 or response.status_code == 400
    assert rows(db, "SELECT COUNT(*) FROM thinkific_progress_snapshots")[0][0] == 0


def test_post_with_foreign_csrf_is_rejected(admin, db):
    response = _upload(admin, files={"grade_6": (simple_csv(), "a.csv")},
                       csrf="tudji-token-koji-nije-iz-sesije", approve=False)
    assert response.status_code == 400
    assert rows(db, "SELECT COUNT(*) FROM thinkific_progress_snapshots")[0][0] == 0


def test_tutor_token_does_not_grant_admin_access(client, admin_env):
    """Tutorski token ima SVAKI učenik — on ne smije otvoriti ovu stranicu."""
    from matbot import auth as tutor_auth

    response = client.get("/admin/reports",
                          headers={tutor_auth.TOKEN_HEADER: tutor_auth.issue_token()})
    assert response.status_code == 302
    assert "/admin/reports/login" in response.headers["Location"]


def test_admin_routes_are_absent_without_configured_password(client, monkeypatch):
    monkeypatch.delenv("MATBOT_ADMIN_PASSWORD", raising=False)
    assert client.get("/admin/reports").status_code == 404
    assert client.get("/admin/reports/login").status_code == 404


def test_short_password_does_not_enable_admin(client, monkeypatch):
    monkeypatch.setenv("MATBOT_ADMIN_PASSWORD", "kratka")
    assert admin_auth.admin_enabled() is False
    assert client.get("/admin/reports").status_code == 404


def test_disabling_password_invalidates_an_existing_session(admin, monkeypatch):
    assert admin.get("/admin/reports").status_code == 200
    monkeypatch.delenv("MATBOT_ADMIN_PASSWORD", raising=False)
    assert admin.get("/admin/reports").status_code == 404


def test_login_is_rate_limited(client, admin_env):
    codes = []
    for _ in range(8):
        token = _csrf_from(client.get("/admin/reports/login"))
        codes.append(client.post("/admin/reports/login",
                                 data={"csrf_token": token, "password": "x" * 20}
                                 ).status_code)
    assert 429 in codes, "prijava nije ograničena po stopi"


def test_learner_routes_are_unaffected(client, admin_env, fake_llm):
    from tests.conftest import queue_two_call

    queue_two_call(fake_llm)
    response = client.post("/api/ai-tutor/chat", json={
        "session_id": "s", "client_turn_id": "t1", "grade": 6, "mode": "practice",
        "entry_source": "manual_topic_choice", "selected_topic": "6-01-005",
        "selected_oblast": "", "conversation_history": [],
        "student_message": "Daj mi jedan zadatak za vjezbu iz ove teme."})
    assert response.status_code == 200
    assert response.get_json()["status"] == "ready"


def test_session_cookie_is_hardened_in_production_defaults(monkeypatch):
    from flask import Flask

    monkeypatch.delenv("MATBOT_ADMIN_COOKIE_SECURE", raising=False)
    app = admin_auth.apply_cookie_hardening(Flask(__name__))
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app.config["SESSION_COOKIE_SECURE"] is True


# ---------------------------------------------------------------------------
# 8-16) Validacija forme i fajlova
# ---------------------------------------------------------------------------
def test_valid_month_and_one_file_import(admin, db):
    response = _upload(admin, files={"grade_6": (simple_csv(), "izvoz.csv")})
    assert response.status_code == 200
    assert "Import potpuno uspješan" in response.get_data(as_text=True)
    assert rows(db, "SELECT COUNT(*) FROM thinkific_progress_snapshots")[0][0] == 1


def test_legacy_import_skips_unknown_student_without_creating_identity(admin, db):
    response = _upload(
        admin, files={"grade_6": (simple_csv(), "neodobren.csv")},
        approve=False)

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Import djelimično uspješan" in html
    assert "Import potpuno uspješan" not in html
    assert "neodobrenih preskočeno" in html
    assert rows(db, "SELECT COUNT(*) FROM students")[0][0] == 0
    assert rows(db, "SELECT COUNT(*) FROM student_accounts")[0][0] == 0
    assert rows(db, "SELECT COUNT(*) FROM thinkific_progress_snapshots")[0][0] == 0


def test_legacy_import_still_updates_progress_for_approved_student(admin, db):
    database = reporting_db.get_database()
    student_id = database.get_or_create_student(
        PROVIDER_THINKIFIC_EMAIL, E1, "Poznati 6")

    response = _upload(
        admin, files={"grade_6": (simple_csv(completed=48), "poznati.csv")},
        approve=False)

    assert response.status_code == 200
    assert "Import potpuno uspješan" in response.get_data(as_text=True)
    assert rows(db, "SELECT student_id, percent_completed FROM "
                    "thinkific_progress_snapshots") == [(student_id, 48.0)]
    assert rows(db, "SELECT COUNT(*) FROM students")[0][0] == 1


@pytest.mark.parametrize("bad", ["", "2026", "2026-13", "rujan"])
def test_malformed_month_is_rejected(admin, db, bad):
    response = _upload(admin, month=bad, files={"grade_6": (simple_csv(), "a.csv")})
    assert response.status_code == 400
    assert rows(db, "SELECT COUNT(*) FROM thinkific_progress_snapshots")[0][0] == 0


def test_no_file_selected_is_rejected(admin, db):
    response = _upload(admin, files={})
    assert response.status_code == 400
    assert "Odaberi bar jedan CSV" in response.get_data(as_text=True)


def test_all_four_slots_import(admin, db):
    files = {}
    for index, key in enumerate(("grade_6", "grade_7", "grade_8", "grade_9")):
        files[key] = (build_csv([learner("s%d@example.com" % index)],
                                sections=["OBLAST %d" % index]), "x.csv")
    response = _upload(admin, files=files)

    assert response.status_code == 200
    assert sorted(r[0] for r in rows(db, "SELECT course_key FROM "
                                         "thinkific_progress_snapshots")) == \
        ["grade_6", "grade_7", "grade_8", "grade_9"]


def test_wrong_extension_is_rejected(admin, db):
    response = _upload(admin, files={"grade_6": (simple_csv(), "izvoz.xlsx")})
    assert response.status_code == 400
    assert "Fajl mora biti .csv" in response.get_data(as_text=True)
    assert rows(db, "SELECT COUNT(*) FROM thinkific_progress_snapshots")[0][0] == 0


def test_empty_file_is_rejected(admin, db):
    response = _upload(admin, files={"grade_6": (b"", "prazan.csv")})
    assert response.status_code == 400
    assert "Fajl je prazan" in response.get_data(as_text=True)


def test_oversized_file_is_rejected(admin, db):
    from matbot.admin_reports import MAX_CSV_BYTES

    huge = b"a," * (MAX_CSV_BYTES // 2 + 32)
    response = _upload(admin, files={"grade_6": (huge, "veliki.csv")})
    assert response.status_code == 400
    assert "prevelik" in response.get_data(as_text=True)
    assert rows(db, "SELECT COUNT(*) FROM thinkific_progress_snapshots")[0][0] == 0


def test_malicious_filename_cannot_escape_or_choose_the_course(admin, db):
    """Ime fajla se NE koristi ni za putanju ni za razred."""
    payload = simple_csv()
    response = _upload(admin, files={"grade_6": (payload, "../../etc/passwd.csv")})

    assert response.status_code == 200
    stored = rows(db, "SELECT course_key, grade FROM thinkific_progress_snapshots")
    assert stored == [("grade_6", 6)], "ime fajla je uticalo na kurs"
    html = response.get_data(as_text=True)
    assert "etc/passwd" not in html
    import pathlib
    assert not pathlib.Path("etc/passwd.csv").exists()


def test_filename_claiming_another_grade_does_not_override_the_slot(admin, db):
    _upload(admin, files={"grade_6": (simple_csv(), "grade_9_progress.csv")})
    assert rows(db, "SELECT course_key, grade FROM thinkific_progress_snapshots") == \
        [("grade_6", 6)]


# ---------------------------------------------------------------------------
# 17-27) Uvoz i djelimičan uspjeh
# ---------------------------------------------------------------------------
def test_repeated_identical_upload_is_idempotent(admin, db):
    payload = simple_csv()
    _upload(admin, files={"grade_6": (payload, "a.csv")})
    _upload(admin, files={"grade_6": (payload, "a.csv")})

    assert rows(db, "SELECT COUNT(*) FROM thinkific_progress_snapshots")[0][0] == 1
    assert rows(db, "SELECT COUNT(*) FROM students")[0][0] == 1


def test_updated_same_month_upload_updates_the_snapshot(admin, db):
    _upload(admin, files={"grade_6": (simple_csv(completed=31), "a.csv")})
    _upload(admin, files={"grade_6": (simple_csv(completed=48), "b.csv")})

    stored = rows(db, "SELECT percent_completed FROM thinkific_progress_snapshots")
    assert stored == [(48.0,)]


def test_roster_preview_and_apply_refresh_name_but_not_unconfirmed_grade(admin, db):
    _upload(admin, month="2026-08", files={
        "grade_7": (build_csv([
            learner(E1, first="Amer", last="7")]), "old.csv")})
    student_id = rows(db, "SELECT id FROM students")[0][0]
    reporting_db.get_database().set_student_grade(student_id, 7)
    current = {
        "grade_8": [learner(E1, first="Amer", last="8")],
    }

    preview = _roster_upload(
        admin, "/admin/reports/roster/preview", current)
    assert preview.status_code == 200
    plan = preview.get_json()
    assert plan["status"] == "ready"
    assert plan["summary"]["name_changes"] == 1
    assert plan["summary"]["grade_suggestions"] == 1
    selected = [item["key"] for item in plan["actions"]
                if item["default_selected"]]
    assert all(item["kind"] != "grade" for item in plan["actions"]
               if item["default_selected"])

    applied = _roster_upload(
        admin, "/admin/reports/roster/apply", current,
        plan_id=plan["plan_id"], action_keys=selected)
    assert applied.status_code == 200
    assert applied.get_json()["status"] == "applied"
    assert rows(db, "SELECT id, display_name, grade FROM students") == \
        [(student_id, "Amer 8", 7)]
    assert rows(db, "SELECT grade FROM student_current_grades") == [(7,)]


def test_roster_nonstudent_grade_is_informational_and_forged_selection_fails(
        admin, db):
    database = reporting_db.get_database()
    student_id = database.get_or_create_student(
        PROVIDER_THINKIFIC_EMAIL, "vedat@example.com", "Vedat 7 PODRŠKA")
    database.set_student_grade(student_id, 6)
    database.set_student_account_types(
        [student_id], reporting_db.ACCOUNT_TYPE_SUPPORT)
    current = {
        "grade_7": [learner(
            "vedat@example.com", first="Vedat", last="7 PODRŠKA")],
    }

    preview = _roster_upload(
        admin, "/admin/reports/roster/preview", current).get_json()
    proposal = next(item for item in preview["actions"]
                    if item["kind"] == "grade")
    assert proposal["selectable"] is False
    assert proposal["default_selected"] is False
    assert proposal["requires_explicit_confirmation"] is False
    assert proposal["unavailable_reason"] == \
        "Razred se može potvrditi samo redovnom učeniku."

    forged = _roster_upload(
        admin, "/admin/reports/roster/apply", current,
        plan_id=preview["plan_id"], action_keys=[proposal["key"]])

    assert forged.status_code == 400
    assert forged.get_json()["message"] == "Izbor promjena nije ispravan."
    assert rows(db, "SELECT id, display_name, account_type FROM students") == [
        (student_id, "Vedat 7 PODRŠKA", reporting_db.ACCOUNT_TYPE_SUPPORT)]
    assert rows(db, "SELECT grade FROM student_current_grades") == [(6,)]


def test_roster_valid_student_grade_explicit_selection_succeeds(admin, db):
    database = reporting_db.get_database()
    student_id = database.get_or_create_student(
        PROVIDER_THINKIFIC_EMAIL, "ucenik@example.com", "Učenik 7")
    database.set_student_grade(student_id, 7)
    current = {
        "grade_8": [learner(
            "ucenik@example.com", first="Učenik", last="8")],
    }

    preview = _roster_upload(
        admin, "/admin/reports/roster/preview", current).get_json()
    proposal = next(item for item in preview["actions"]
                    if item["kind"] == "grade")
    assert proposal["selectable"] is True
    assert proposal["default_selected"] is False

    applied = _roster_upload(
        admin, "/admin/reports/roster/apply", current,
        plan_id=preview["plan_id"], action_keys=[proposal["key"]])

    assert applied.status_code == 200
    assert applied.get_json()["status"] == "applied"
    assert rows(db, "SELECT grade FROM student_current_grades") == [(8,)]
    assert rows(db, "SELECT display_name FROM students") == [("Učenik 7",)]


def test_roster_ui_never_renders_or_submits_unavailable_action_checkbox(
        admin, db):
    html = admin.get("/admin/reports").get_data(as_text=True)

    assert "if(item.selectable===false)" in html
    assert "Nije dostupno" in html
    assert ".roster-action:checked:not(:disabled)" in html


def test_roster_apply_logs_only_safe_failure_diagnostics(
        admin, db, monkeypatch, caplog):
    _upload(admin, month="2026-08", files={
        "grade_7": (build_csv([
            learner(E1, first="StaroTajno", last="7")]), "old.csv")})
    current = {
        "grade_8": [learner(E1, first="NovoTajno", last="8")],
    }
    preview = _roster_upload(
        admin, "/admin/reports/roster/preview", current).get_json()
    selected = [item["key"] for item in preview["actions"]
                if item["default_selected"]]
    raw_detail = (
        "RAW_DB_DETAIL StaroTajno student1@example.com "
        "libsql://private.example?authToken=token-secret "
        "csrf=csrf-secret cookie=session-secret")

    def fail_apply(_actions):
        try:
            raise RuntimeError(raw_detail)
        except RuntimeError as cause:
            raise reporting_db.ReportingUnavailable(
                "roster_apply_failed:RuntimeError", cause,
                phase="update_name_or_status", action_type="name") from None

    monkeypatch.setattr(
        reporting_db.get_database(), "apply_roster_reconciliation", fail_apply)
    caplog.set_level(logging.ERROR, logger="matbot.admin_reports")

    response = _roster_upload(
        admin, "/admin/reports/roster/apply", current,
        plan_id=preview["plan_id"], action_keys=selected)

    assert response.status_code == 503
    assert response.get_json() == {
        "status": "error",
        "message": "Promjene nisu sačuvane. Pokušaj ponovo.",
    }
    records = [record for record in caplog.records
               if "thinkific_roster_apply_failed" in record.getMessage()]
    assert len(records) == 1 and records[0].levelno == logging.ERROR
    message = records[0].getMessage()
    assert "code=roster_apply_failed:RuntimeError" in message
    assert "phase=update_name_or_status" in message
    assert "action_type=name" in message
    assert "exception_type=RuntimeError" in message
    assert re.search(r"traceback=test_admin_reports\.py:fail_apply:\d+", message)
    for forbidden in (
            raw_detail, "RAW_DB_DETAIL", "StaroTajno", E1,
            "libsql://private.example", "token-secret", "csrf-secret",
            "session-secret"):
        assert forbidden not in message


def test_roster_preview_keeps_unrelated_same_first_name_archive_and_add(admin, db):
    """Ime nije identitet: dva Darisa s različitim adresama su dva učenika."""
    database = reporting_db.get_database()
    old_id = database.get_or_create_student(
        PROVIDER_THINKIFIC_EMAIL, "old.daris@example.com", "Daris 7")

    preview = _roster_upload(admin, "/admin/reports/roster/preview", {
        "grade_8": [learner(
            "  NEW.DARIS@EXAMPLE.COM  ", first="Daris", last="8")],
    })

    assert preview.status_code == 200
    plan = preview.get_json()
    assert plan["status"] == "ready"
    assert plan["summary"]["missing_students"] == 1
    assert plan["summary"]["new_students"] == 1

    archived = [item for item in plan["actions"]
                if item["kind"] == "archive"]
    added = [item for item in plan["actions"] if item["kind"] == "add"]
    assert [(item["student_id"], item["label"], item["default_selected"])
            for item in archived] == [(old_id, "Daris 7", False)]
    assert [(item["student_id"], item["label"], item["default_selected"])
            for item in added] == [(None, "Daris 8", True)]


def test_roster_preview_requires_all_four_exports(admin, db):
    import io as _io

    preview = admin.post("/admin/reports/roster/preview", data={
        "csrf_token": _csrf(admin), "report_month": "2026-09",
        "grade_6": (_io.BytesIO(build_csv([])), "only-one.csv"),
    }, content_type="multipart/form-data")
    assert preview.status_code == 400
    assert "sva četiri" in preview.get_json()["message"]


def _detected_four(order=("grade_6", "grade_7", "grade_8", "grade_9")):
    names = {"grade_6": "alfa.csv", "grade_7": "beta.csv",
             "grade_8": "gama.csv", "grade_9": "delta.csv"}
    return [(names[key], _course_csv(
        key, "%s@example.com" % key.replace("grade_", "ucenik")))
            for key in order]


def test_multi_upload_random_order_detects_all_four_courses(admin, db):
    files = _detected_four(("grade_8", "grade_6", "grade_9", "grade_7"))
    response = _multi_roster_upload(
        admin, "/admin/reports/roster/classify", files)

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ready"
    assert {item["filename"]: item["course_key"] for item in payload["files"]} == {
        "gama.csv": "grade_8", "alfa.csv": "grade_6",
        "delta.csv": "grade_9", "beta.csv": "grade_7",
    }
    assert [(item["grade"], item["status"]) for item in payload["grades"]] == [
        (6, "found"), (7, "found"), (8, "found"), (9, "found")]


def test_multi_upload_order_does_not_change_roster_plan(admin, db):
    first = _multi_roster_upload(
        admin, "/admin/reports/roster/preview",
        _detected_four(("grade_9", "grade_6", "grade_8", "grade_7")))
    second = _multi_roster_upload(
        admin, "/admin/reports/roster/preview",
        _detected_four(("grade_7", "grade_8", "grade_6", "grade_9")))

    assert first.status_code == second.status_code == 200
    one, two = first.get_json(), second.get_json()
    assert one["plan_id"] == two["plan_id"]
    assert one["summary"] == two["summary"]
    assert one["actions"] == two["actions"]


def test_multi_upload_duplicate_detected_grade_blocks_preview(admin, db):
    files = [
        ("a.csv", _course_csv("grade_6", "a@example.com")),
        ("b.csv", _course_csv("grade_7", "b@example.com")),
        ("c.csv", _course_csv("grade_7", "c@example.com")),
        ("d.csv", _course_csv("grade_8", "d@example.com")),
    ]
    classified = _multi_roster_upload(
        admin, "/admin/reports/roster/classify", files).get_json()
    preview = _multi_roster_upload(
        admin, "/admin/reports/roster/preview", files,
        manual_keys=["", "", "", ""])

    assert classified["status"] == "blocked"
    assert any("7. razred" in item["message"] and "dva fajla" in item["message"]
               for item in classified["errors"])
    assert preview.status_code == 400
    assert "7. razred" in preview.get_json()["message"]


def test_multi_upload_missing_grade_nine_blocks_preview(admin, db):
    files = _detected_four(("grade_6", "grade_7", "grade_8"))
    classified = _multi_roster_upload(
        admin, "/admin/reports/roster/classify", files).get_json()
    preview = _multi_roster_upload(
        admin, "/admin/reports/roster/preview", files,
        manual_keys=["", "", ""])

    assert classified["status"] == "blocked"
    assert any(item["message"] == "Nedostaje CSV za 9. razred."
               for item in classified["errors"])
    assert preview.status_code == 400
    assert "9. razred" in preview.get_json()["message"]


def test_multi_upload_more_than_four_files_is_blocked(admin, db):
    files = _detected_four() + [
        ("peti.csv", _course_csv("grade_6", "peti@example.com"))]
    payload = _multi_roster_upload(
        admin, "/admin/reports/roster/classify", files).get_json()

    assert payload["status"] == "blocked"
    assert payload["errors"] == [
        {"message": "Možeš učitati najviše četiri CSV fajla."}]


def test_multi_upload_invalid_csv_has_clear_error(admin, db):
    files = _detected_four(("grade_6", "grade_7", "grade_8")) + [
        ("pokvaren.csv", b"nije,thinkific\n1,2\n")]
    payload = _multi_roster_upload(
        admin, "/admin/reports/roster/classify", files).get_json()

    assert payload["status"] == "blocked"
    invalid = next(item for item in payload["files"]
                   if item["filename"] == "pokvaren.csv")
    assert invalid["status"] == "invalid"
    assert invalid["message"] == \
        "Fajl nije ispravan Thinkific Student Progress CSV."


def test_ambiguous_file_requires_and_accepts_only_manual_mapping(admin, db):
    files = _detected_four(("grade_6", "grade_7", "grade_8")) + [
        ("nepoznat.csv", build_csv(
            [learner("manual@example.com")], sections=["NOVA OBLAST"]))]

    classified = _multi_roster_upload(
        admin, "/admin/reports/roster/classify", files).get_json()
    blocked = _multi_roster_upload(
        admin, "/admin/reports/roster/preview", files,
        manual_keys=["", "", "", ""])
    resolved = _multi_roster_upload(
        admin, "/admin/reports/roster/preview", files,
        manual_keys=["", "", "", "grade_9"])

    ambiguous = next(item for item in classified["files"]
                     if item["filename"] == "nepoznat.csv")
    assert classified["status"] == "needs_mapping"
    assert ambiguous["status"] == "ambiguous"
    assert "Nije moguće automatski odrediti razred" in ambiguous["message"]
    assert blocked.status_code == 400
    assert resolved.status_code == 200


def test_manual_mapping_cannot_override_a_detected_course(admin, db):
    files = _detected_four()
    response = _multi_roster_upload(
        admin, "/admin/reports/roster/preview", files,
        manual_keys=["grade_9", "", "", ""])

    assert response.status_code == 400
    assert "ne može prepisati" in response.get_json()["message"]


def test_student_name_number_never_determines_detected_course(admin, db):
    files = [("grade-9-progress.csv", _course_csv(
        "grade_6", "faris@example.com", first="Faris", last="8"))]
    payload = _multi_roster_upload(
        admin, "/admin/reports/roster/classify", files).get_json()

    assert payload["files"][0]["course_key"] == "grade_6"
    assert payload["files"][0]["grade"] == 6


def test_detected_mapping_drives_existing_reconciliation_and_progress_import(
        admin, db):
    files = _detected_four(("grade_8", "grade_6", "grade_9", "grade_7"))
    preview = _multi_roster_upload(
        admin, "/admin/reports/roster/preview", files).get_json()
    selected = [item["key"] for item in preview["actions"]
                if item["kind"] == "add"]

    applied = _multi_roster_upload(
        admin, "/admin/reports/roster/apply", files,
        plan_id=preview["plan_id"], action_keys=selected,
        manual_keys=["", "", "", ""])

    assert applied.status_code == 200
    assert applied.get_json()["status"] == "applied"
    stored = rows(db, "SELECT a.external_user_id, p.course_key, p.grade "
                      "FROM thinkific_progress_snapshots p "
                      "JOIN student_accounts a ON a.student_id = p.student_id "
                      "ORDER BY a.external_user_id")
    assert stored == [
        ("ucenik6@example.com", "grade_6", 6),
        ("ucenik7@example.com", "grade_7", 7),
        ("ucenik8@example.com", "grade_8", 8),
        ("ucenik9@example.com", "grade_9", 9),
    ]


def test_admin_page_has_one_multiple_thinkific_file_input(admin, db):
    html = admin.get("/admin/reports").get_data(as_text=True)
    assert 'name="thinkific_files"' in html
    assert 'id="thinkific_files"' in html and "multiple" in html
    for grade in range(6, 10):
        assert 'name="grade_%d"' % grade not in html


def test_roster_file_classification_requires_admin_and_csrf(client, admin_env,
                                                            admin, db):
    files = _detected_four()
    unauthenticated = client.application.test_client()
    denied = _multi_roster_upload(
        unauthenticated, "/admin/reports/roster/classify", files, csrf="x")
    bad_csrf = _multi_roster_upload(
        admin, "/admin/reports/roster/classify", files, csrf="pogresan")

    assert denied.status_code == 403
    assert bad_csrf.status_code == 400


def test_unselected_new_roster_student_is_not_created_by_progress_import(admin, db):
    current = {
        "grade_8": [learner("nova@example.com", first="Nova", last="8")],
    }
    preview = _roster_upload(
        admin, "/admin/reports/roster/preview", current).get_json()
    assert preview["summary"]["new_students"] == 1

    applied = _roster_upload(
        admin, "/admin/reports/roster/apply", current,
        plan_id=preview["plan_id"], action_keys=[])
    assert applied.status_code == 200
    assert applied.get_json()["import"]["students_skipped"] == 1
    assert rows(db, "SELECT COUNT(*) FROM students")[0][0] == 0


def test_partial_success_is_never_shown_as_success(admin, db):
    files = {"grade_6": (simple_csv(), "ok.csv"),
             "grade_7": (build_csv([learner(E2, viewed="besmislica")],
                                   sections=["OBLAST"]), "lose.csv")}
    response = _upload(admin, files=files)
    html = response.get_data(as_text=True)

    assert "Import djelimično uspješan" in html
    assert "Import potpuno uspješan" not in html
    # Odbijen fajl nije upisao NIŠTA; ispravan jest.
    assert [r[0] for r in rows(db, "SELECT course_key FROM "
                                   "thinkific_progress_snapshots")] == ["grade_6"]


def test_rejected_file_shows_row_and_column_without_pii(admin, db):
    bad = build_csv([learner(E2, viewed="besmislica", first="Tajna", last="Osoba")],
                    sections=["OBLAST"])
    response = _upload(admin, files={"grade_7": (bad, "lose.csv")})
    html = response.get_data(as_text=True)

    assert "Import neuspješan" in html
    assert "percent_malformed" in html and "red 2" in html
    # NIKAD sirovi red, e-mail ni ime.
    assert E2 not in html and "Tajna" not in html and "besmislica" not in html


def test_import_errors_contain_no_database_details(admin, db):
    response = _upload(admin, files={"grade_6": (b"Email\nnije-email\n", "x.csv")})
    html = response.get_data(as_text=True)
    for forbidden in ("Traceback", "sqlite", "libsql", "SELECT ", "INSERT "):
        assert forbidden not in html


# ---------------------------------------------------------------------------
# 28-30) Sigurnost verzije šeme
# ---------------------------------------------------------------------------
def test_schema_v1_disables_import_and_writes_nothing(client, admin_env, db_v1):
    token = _csrf_from(client.get("/admin/reports/login"))
    client.post("/admin/reports/login", data={"csrf_token": token, "password": PASSWORD})

    page = client.get("/admin/reports")
    assert "Uvoz nije moguć" in page.get_data(as_text=True)
    assert "disabled" in page.get_data(as_text=True)

    response = _upload(client, files={"grade_6": (simple_csv(), "a.csv")},
                       approve=False)
    assert response.status_code == 409
    # NIJEDNA tabela nije kreirana iz web zahtjeva.
    tables = {r[0] for r in rows(db_v1, "SELECT name FROM sqlite_master "
                                        "WHERE type='table'")}
    assert "thinkific_progress_snapshots" not in tables


def test_schema_v2_allows_import(admin, db):
    page = admin.get("/admin/reports")
    assert "Uvoz nije moguć" not in page.get_data(as_text=True)
    assert _upload(admin, files={"grade_6": (simple_csv(), "a.csv")}).status_code == 200


# ---------------------------------------------------------------------------
# 31-35) Populacija
# ---------------------------------------------------------------------------
def _seed_matbot_only(db_path, email=E2, month="2026-09"):
    database = reporting_db.get_database()
    student_id = database.get_or_create_student(PROVIDER_THINKIFIC_EMAIL, email)
    database.record_learning_activity(student_id, [
        activity.ActivityEvent(activity.PRACTICE_TASK_PRESENTED, "p1",
                               mode="practice", grade=6, lesson_id="6-01-005",
                               occurred_at="%s-05 10:00:00" % month)])
    return student_id


def test_population_lists_both_sources_once(admin, db):
    _upload(admin, files={"grade_6": (simple_csv(), "a.csv")})
    matbot_only = _seed_matbot_only(db)

    response = admin.get("/admin/reports/students?month=2026-09")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Učenici za izvještaj: 2" in html
    assert html.count("Pregled →") == 2
    assert "Učenik #%d" % matbot_only in html          # bez imena -> neutralno
    assert "nije importovano" in html                   # MAT-BOT-only red


def test_population_never_falls_back_to_email(admin, db):
    _seed_matbot_only(db)
    html = admin.get("/admin/reports/students?month=2026-09").get_data(as_text=True)

    # `@` se legitimno pojavljuje u CSS-u (`@media`), pa se trazi ADRESA.
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", html),         "e-mail se pojavio u administratorskom HTML-u"
    assert E2 not in html


def test_population_uses_display_name_when_present(admin, db):
    _upload(admin, files={"grade_6": (simple_csv(first="Ana", last="Anić"), "a.csv")})
    html = admin.get("/admin/reports/students?month=2026-09").get_data(as_text=True)
    assert "Ana Anić" in html


def test_dashboard_and_month_list_show_saved_report_status(
        admin, db, monkeypatch):
    from tests.test_monthly_reports_contract import (
        PRODUCTION_DDL, PRODUCTION_INDEX)

    database = reporting_db.get_database()
    conn = database._connection()
    conn.execute("DROP TABLE monthly_reports")
    conn.execute(PRODUCTION_DDL)
    conn.execute(PRODUCTION_INDEX)
    conn.commit()
    student_id = _seed_matbot_only(db)
    database.save_monthly_report(
        student_id=student_id, report_month="2026-09",
        metrics_json="{}", ai_summary="{}")

    assert database.fetch_monthly_report_student_ids("2026-09") == {student_id}
    monkeypatch.setattr(admin_reports, "_default_month", lambda: "2026-09")

    dashboard = admin.get("/admin/reports").get_data(as_text=True)
    listing = admin.get(
        "/admin/reports/students?month=2026-09").get_data(as_text=True)

    assert "1 / 1" in dashboard
    assert "Izvještaji za ovaj mjesec" in dashboard
    assert "Sačuvan" in listing


def test_population_rejects_malformed_month(admin, db):
    assert admin.get("/admin/reports/students?month=rujan").status_code == 400


def _seed_filter_student(name, grade, account_type, event_key, email):
    database = reporting_db.get_database()
    student_id = database.create_student(name, grade, email)
    if account_type != reporting_db.ACCOUNT_TYPE_STUDENT:
        database.set_student_account_types([student_id], account_type)
    database.record_learning_activity(student_id, [
        activity.ActivityEvent(
            activity.PRACTICE_TASK_PRESENTED, event_key, mode="practice",
            grade=grade, occurred_at="2026-09-10 10:00:00")])
    return student_id


def test_report_filters_share_grade_and_account_type_rules(admin, db):
    _seed_filter_student("Redovni Sedam", 7, "STUDENT", "filter-r7",
                         "regular7@example.com")
    _seed_filter_student("Redovni Šest", 6, "STUDENT", "filter-r6",
                         "regular6@example.com")
    _seed_filter_student("Podrška Sedam", 7, "SUPPORT", "filter-s7",
                         "support7@example.com")
    _seed_filter_student("Test Sedam", 7, "TEST", "filter-t7",
                         "test7@example.com")

    default = admin.get(
        "/admin/reports/students?month=2026-09&grade=7").get_data(as_text=True)
    assert "Redovni Sedam" in default
    assert "Redovni Šest" not in default
    assert "Podrška Sedam" not in default
    assert "Test Sedam" not in default

    included = admin.get(
        "/admin/reports/students?month=2026-09&grade=7&"
        "include_support=1&include_test=1").get_data(as_text=True)
    assert "Redovni Sedam" in included
    assert "Podrška Sedam" in included
    assert "Test Sedam" in included
    assert included.count("disabled title=\"Izvještaji su isključeni") == 2
    assert "Posebni nalozi prikazani su samo informativno" in included


def test_student_without_period_data_is_not_silently_hidden(admin, db):
    database = reporting_db.get_database()
    database.create_student("Bez Podataka", 7, "no-data@example.com")
    _seed_filter_student("Ima Podatke", 7, "STUDENT", "has-period-data",
                         "has-data@example.com")
    html = admin.get(
        "/admin/reports/students?month=2026-09&grade=7").get_data(as_text=True)
    assert "Ima Podatke" in html
    assert "Bez Podataka" not in html
    assert "Prikazani su učenici koji imaju podatke za odabrani period" in html
    assert "bez časa, aktivnosti, kontrolnog ili Thinkific podataka" in html


def test_report_search_accepts_email_without_returning_account_data(admin, db):
    _seed_filter_student("Traženi Učenik", 7, "STUDENT", "filter-search",
                         "known-address@example.com")
    _seed_filter_student("Drugi Učenik", 7, "STUDENT", "filter-other",
                         "other-address@example.com")
    html = admin.get(
        "/admin/reports/students?month=2026-09&search=known-address%40example.com"
        ).get_data(as_text=True)
    assert "Traženi Učenik" in html
    assert "Drugi Učenik" not in html


def test_filtered_csv_uses_the_same_grade_and_type_selection(admin, db):
    _seed_filter_student("CSV Redovni", 7, "STUDENT", "csv-r7",
                         "csv-r7@example.com")
    _seed_filter_student("CSV Podrška", 7, "SUPPORT", "csv-s7",
                         "csv-s7@example.com")
    _seed_filter_student("CSV Šesti", 6, "STUDENT", "csv-r6",
                         "csv-r6@example.com")

    response = admin.get(
        "/admin/reports/students.csv?month=2026-09&grade=7")
    text = response.data.decode("utf-8-sig")
    assert response.mimetype == "text/csv"
    assert "CSV Redovni" in text
    assert "CSV Podrška" not in text
    assert "CSV Šesti" not in text


# ---------------------------------------------------------------------------
# 36-41) Pregled jednog učenika
# ---------------------------------------------------------------------------
def test_preview_renders_thinkific_and_matbot_facts(admin, db):
    _upload(admin, month="2026-08", files={"grade_6": (build_csv(
        [with_sections(learner(E1, viewed=40, completed=31), {"SKUPOVI": 50})],
        sections=["SKUPOVI"]), "aug.csv")})
    _upload(admin, month="2026-09", files={"grade_6": (build_csv(
        [with_sections(learner(E1, viewed=62, completed=48), {"SKUPOVI": 75})],
        sections=["SKUPOVI"]), "sep.csv")})
    student_id = rows(db, "SELECT id FROM students")[0][0]

    database = reporting_db.get_database()
    database.record_learning_activity(student_id, [
        activity.ActivityEvent(activity.PRACTICE_TASK_PRESENTED, "p1", mode="practice",
                               grade=6, lesson_id="6-01-005",
                               occurred_at="2026-09-05 10:00:00"),
        activity.ActivityEvent(activity.PRACTICE_ANSWER_CORRECT, "a1", mode="practice",
                               grade=6, lesson_id="6-01-005",
                               occurred_at="2026-09-05 10:00:00")])

    html = admin.get("/admin/reports/student/%d?month=2026-09" % student_id
                     ).get_data(as_text=True)

    assert "48%" in html and "62%" in html          # Thinkific, bez suvisne decimale
    assert "+17 p.p." in html                        # delta zavrsenog
    assert "SKUPOVI" in html and "75%" in html
    assert "Poređeno s mjesecom 2026-08" in html
    assert "100%" in html                            # MAT-BOT tacnost 1/1
    # `@media` u CSS-u je legitiman; trazi se ADRESA.
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", html)


def test_preview_shows_missing_baseline_instead_of_zero(admin, db):
    _upload(admin, files={"grade_6": (simple_csv(), "a.csv")})
    student_id = rows(db, "SELECT id FROM students")[0][0]

    html = admin.get("/admin/reports/student/%d?month=2026-09" % student_id
                     ).get_data(as_text=True)
    assert "Prethodni mjesec nije dostupan" in html
    assert "nije napredovao" not in html


def test_preview_shows_missing_snapshot_for_matbot_only_learner(admin, db):
    student_id = _seed_matbot_only(db)
    html = admin.get("/admin/reports/student/%d?month=2026-09" % student_id
                     ).get_data(as_text=True)

    assert "Thinkific Progress snapshot nije importovan za ovaj mjesec" in html
    # Nedostajuci snapshot se NE prikazuje kao izmjerena nula.
    thinkific_block = html.split("MAT-BOT aktivnost")[0]
    assert "0%" not in thinkific_block.split("Thinkific napredak")[-1]


def test_preview_shows_no_matbot_activity_for_thinkific_only_learner(admin, db):
    _upload(admin, files={"grade_6": (simple_csv(), "a.csv")})
    student_id = rows(db, "SELECT id FROM students")[0][0]

    html = admin.get("/admin/reports/student/%d?month=2026-09" % student_id
                     ).get_data(as_text=True)
    assert "Nema zabilježene MAT-BOT aktivnosti u ovom mjesecu" in html


def test_preview_never_renders_null_accuracy_as_zero_percent(admin, db):
    """Učenik bez ijednog odgovora nema 0 % tačnosti — nema mjerenja."""
    _upload(admin, files={"grade_6": (simple_csv(), "a.csv")})
    student_id = rows(db, "SELECT id FROM students")[0][0]
    database = reporting_db.get_database()
    database.record_learning_activity(student_id, [
        activity.ActivityEvent(activity.PRACTICE_TASK_PRESENTED, "p1", mode="practice",
                               grade=6, occurred_at="2026-09-05 10:00:00")])

    html = admin.get("/admin/reports/student/%d?month=2026-09" % student_id
                     ).get_data(as_text=True)
    assert "tačnost (nema odgovora)" in html


def test_preview_marks_low_evidence_lesson_outcomes(admin, db):
    _upload(admin, files={"grade_6": (simple_csv(), "a.csv")})
    student_id = rows(db, "SELECT id FROM students")[0][0]
    reporting_db.get_database().record_assessment_completed(
        student_id,
        _kontrolni_attempt("exam-1", grade=6, total_count=1, correct_count=0,
                           score_percent=0, completed_at="2026-09-20 12:00:00"),
        [{"item_key": "q1", "ordinal": 1, "is_correct": False,
          "lesson_id": "6-04-005", "lesson_name": "Složeni", "difficulty": "harder"}])

    html = admin.get("/admin/reports/student/%d?month=2026-09" % student_id
                     ).get_data(as_text=True)
    assert "malo dokaza" in html


# ---------------------------------------------------------------------------
# 42-45) Privatnost
# ---------------------------------------------------------------------------
def test_no_raw_csv_is_persisted_anywhere(admin, db, tmp_path):
    _upload(admin, files={"grade_6": (simple_csv(company="Tajna firma"), "a.csv")})

    for table in ("thinkific_progress_imports", "thinkific_progress_snapshots",
                  "thinkific_progress_sections"):
        dump = str(rows(db, "SELECT * FROM " + table))
        assert "@" not in dump and "Tajna firma" not in dump
        assert "First Name" not in dump
    # Nijedan fajl nije dospio na disk pored same baze.
    stray = [p.name for p in tmp_path.iterdir() if p.suffix == ".csv"]
    assert stray == []


def test_admin_html_contains_no_learner_email(admin, db):
    _upload(admin, files={"grade_6": (simple_csv(), "a.csv")})
    student_id = rows(db, "SELECT id FROM students")[0][0]

    for path in ("/admin/reports",
                 "/admin/reports/students?month=2026-09",
                 "/admin/reports/student/%d?month=2026-09" % student_id):
        html = admin.get(path).get_data(as_text=True)
        assert E1 not in html, path
        assert "student1" not in html, path


def test_admin_logs_carry_no_email_or_password(admin, db, caplog):
    with caplog.at_level(logging.DEBUG):
        _upload(admin, files={"grade_6": (simple_csv(first="Tajna",
                                                     last="Osoba"), "a.csv")})
    assert E1 not in caplog.text and "Tajna" not in caplog.text
    assert PASSWORD not in caplog.text


def test_failed_login_never_logs_the_attempted_password(client, admin_env, caplog):
    token = _csrf_from(client.get("/admin/reports/login"))
    with caplog.at_level(logging.DEBUG):
        client.post("/admin/reports/login",
                    data={"csrf_token": token, "password": "SUPER-TAJNA-LOZINKA"})
    assert "SUPER-TAJNA-LOZINKA" not in caplog.text


def test_admin_page_is_marked_noindex(admin, db):
    html = admin.get("/admin/reports").get_data(as_text=True)
    assert 'name="robots"' in html and "noindex" in html
