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

from matbot import activity, admin_auth, report_input, reporting_db
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


def _upload(client, month="2026-09", files=None, csrf=None):
    data = {"csrf_token": csrf if csrf is not None else _csrf(client),
            "report_month": month}
    for key, (payload, name) in (files or {}).items():
        import io as _io
        data[key] = (_io.BytesIO(payload), name)
    return client.post("/admin/reports/import", data=data,
                       content_type="multipart/form-data")


# ---------------------------------------------------------------------------
# 1-7) Autorizacija i sigurnost
# ---------------------------------------------------------------------------
def test_unauthenticated_get_is_redirected_to_login(client, admin_env):
    response = client.get("/admin/reports")
    assert response.status_code == 302
    assert "/admin/reports/login" in response.headers["Location"]


def test_unauthenticated_import_post_is_denied(client, admin_env, db):
    response = _upload(client, files={"grade_6": (simple_csv(), "a.csv")}, csrf="x")
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
    response = _upload(admin, files={"grade_6": (simple_csv(), "a.csv")}, csrf="")
    assert response.status_code == 403 or response.status_code == 400
    assert rows(db, "SELECT COUNT(*) FROM thinkific_progress_snapshots")[0][0] == 0


def test_post_with_foreign_csrf_is_rejected(admin, db):
    response = _upload(admin, files={"grade_6": (simple_csv(), "a.csv")},
                       csrf="tudji-token-koji-nije-iz-sesije")
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

    response = _upload(client, files={"grade_6": (simple_csv(), "a.csv")})
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


def test_population_rejects_malformed_month(admin, db):
    assert admin.get("/admin/reports/students?month=rujan").status_code == 400


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
