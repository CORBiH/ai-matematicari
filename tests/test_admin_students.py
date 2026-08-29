"""Faza 3D — administratorske rute registra i evidencije časova.

TVRDNJA: registar i evidencija su ADMIN površine. Tutorski token ih ne otvara,
GET ništa ne mijenja, svaka izmjena traži CSRF, a e-mail ne izlazi ni u URL ni
u prikaz.

PII: svi učenici su sintetički.
"""
import re

import pytest

from matbot import reporting_db, reporting_schema

from tests.test_thinkific_progress_import import build_v1, migrate

libsql = pytest.importorskip("libsql")

PASSWORD = "administratorska-lozinka-123"


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
    conn.commit()
    conn.close()
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://test.invalid")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token-not-real")
    database = reporting_db.ReportingDatabase(
        connect_factory=lambda: libsql.connect(path, timeout=10.0,
                                               _check_same_thread=False))
    reporting_db.set_database(database)
    yield database
    reporting_db.wait_for_pending_writes()
    reporting_db.set_database(None)


def _csrf_from(response):
    match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
    assert match, "CSRF token nije u formularu"
    return match.group(1).decode()


@pytest.fixture
def admin(client, admin_env):
    token = _csrf_from(client.get("/admin/reports/login"))
    assert client.post("/admin/reports/login",
                       data={"csrf_token": token,
                             "password": PASSWORD}).status_code == 302
    return client


# ---------------------------------------------------------------------------
# Pristup
# ---------------------------------------------------------------------------
def test_registry_requires_admin(client, db, admin_env):
    """Neprijavljen GET ide na prijavu, POST se odbija — nikad se ne obrađuje."""
    response = client.get("/admin/students")
    assert response.status_code == 302
    assert "/admin/reports/login" in response.headers["Location"]
    assert client.post("/admin/students/create",
                       data={"display_name": "X", "grade": "6"}).status_code == 403
    assert db.list_students() == []


def test_registry_is_invisible_when_admin_is_disabled(client, db, monkeypatch):
    """Bez lozinke se ni POSTOJANJE stranice ne otkriva."""
    monkeypatch.delenv("MATBOT_ADMIN_PASSWORD", raising=False)
    assert client.get("/admin/students").status_code == 404


def test_tutor_token_does_not_open_the_registry(client, db, admin_env):
    """Tutorski token je za učenike. Registar nije njegova površina."""
    response = client.get("/admin/students",
                          headers={"X-Tutor-Token": "bilo-sta"})
    assert response.status_code == 302
    assert "/admin/reports/login" in response.headers["Location"]


def test_registry_lists_students(admin, db):
    db.create_student("Sintetički Učenik", 6)
    response = admin.get("/admin/students")
    assert response.status_code == 200
    assert "Sintetički Učenik".encode() in response.data


# ---------------------------------------------------------------------------
# Ručni upis i povezivanje
# ---------------------------------------------------------------------------
def test_manual_student_can_be_created(admin, db):
    token = _csrf_from(admin.get("/admin/students"))
    response = admin.post("/admin/students/create",
                          data={"csrf_token": token,
                                "display_name": "Novi Učenik", "grade": "7"})
    assert response.status_code == 302
    assert any(s["display_name"] == "Novi Učenik" for s in db.list_students())


def test_creating_a_student_requires_csrf(admin, db):
    response = admin.post("/admin/students/create",
                          data={"display_name": "Bez Tokena", "grade": "6"})
    assert response.status_code == 400
    assert db.list_students() == []


@pytest.mark.parametrize("grade", ["5", "10", "", "šest"])
def test_invalid_grade_is_refused(admin, db, grade):
    token = _csrf_from(admin.get("/admin/students"))
    admin.post("/admin/students/create",
               data={"csrf_token": token, "display_name": "Neko", "grade": grade})
    assert db.list_students() == []


@pytest.mark.parametrize("name", ["", "   ", "<b>x</b>"])
def test_invalid_name_is_refused(admin, db, name):
    token = _csrf_from(admin.get("/admin/students"))
    admin.post("/admin/students/create",
               data={"csrf_token": token, "display_name": name, "grade": "6"})
    assert db.list_students() == []


def test_optional_thinkific_email_links_an_account(admin, db):
    token = _csrf_from(admin.get("/admin/students"))
    admin.post("/admin/students/create",
               data={"csrf_token": token, "display_name": "Povezani",
                     "grade": "6", "thinkific_email": "Ucenik@Example.COM"})
    student = db.list_students()[0]
    assert student["thinkific_linked"] is True


def test_student_without_email_gets_no_account(admin, db):
    token = _csrf_from(admin.get("/admin/students"))
    admin.post("/admin/students/create",
               data={"csrf_token": token, "display_name": "Bez Naloga",
                     "grade": "6"})
    assert db.list_students()[0]["thinkific_linked"] is False


def test_duplicate_thinkific_account_fails_closed(admin, db):
    from matbot.student_identity import PROVIDER_THINKIFIC_EMAIL

    owner = db.get_or_create_student(PROVIDER_THINKIFIC_EMAIL,
                                     "zauzet@example.com", grade=6)
    student_id = db.create_student("Drugi", 6)
    token = _csrf_from(admin.get("/admin/students/%d" % student_id))
    page = admin.post("/admin/students/%d/link" % student_id,
                      data={"csrf_token": token,
                            "thinkific_email": "zauzet@example.com"},
                      follow_redirects=True)
    # Nalog NIJE preuzet, a administrator dobija uputu koji zapis da pogleda.
    assert db.student_has_thinkific(student_id) is False
    assert "već povezan sa drugim učenikom".encode() in page.data
    assert str(owner).encode() in page.data


def test_email_never_appears_in_a_url_or_in_the_listing(admin, db):
    token = _csrf_from(admin.get("/admin/students"))
    admin.post("/admin/students/create",
               data={"csrf_token": token, "display_name": "Tajni",
                     "grade": "6", "thinkific_email": "tajna@example.com"})
    listing = admin.get("/admin/students")
    assert b"tajna@example.com" not in listing.data
    student_id = db.list_students()[0]["student_id"]
    profile = admin.get("/admin/students/%d" % student_id)
    assert b"tajna@example.com" not in profile.data
    assert b"povezan" in profile.data


# ---------------------------------------------------------------------------
# Evidencija časova
# ---------------------------------------------------------------------------
@pytest.fixture
def student(db):
    return db.create_student("Sintetički Učenik", 6)


def _session_form(token, **over):
    data = {"csrf_token": token, "session_date": "2026-08-05",
            "attendance": "present", "activity_rating": "4",
            "homework_status": "done", "area_name": "Razlomci",
            "lesson_name": "Sabiranje razlomaka", "comment": "Dobar rad."}
    data.update(over)
    return data


def test_session_can_be_recorded(admin, db, student):
    token = _csrf_from(admin.get("/admin/students/%d" % student))
    response = admin.post("/admin/students/%d/sessions" % student,
                          data=_session_form(token))
    assert response.status_code == 302
    rows = db.fetch_sessions(student)
    assert len(rows) == 1 and rows[0]["activity_rating"] == 4


def test_recording_a_session_requires_csrf(admin, db, student):
    data = {k: v for k, v in _session_form("x").items() if k != "csrf_token"}
    response = admin.post("/admin/students/%d/sessions" % student, data=data)
    assert response.status_code == 400
    assert db.fetch_sessions(student) == []


def test_absent_with_activity_is_refused(admin, db, student):
    token = _csrf_from(admin.get("/admin/students/%d" % student))
    admin.post("/admin/students/%d/sessions" % student,
               data=_session_form(token, attendance="absent", activity_rating="1"))
    assert db.fetch_sessions(student) == []


def test_absent_without_activity_is_accepted(admin, db, student):
    token = _csrf_from(admin.get("/admin/students/%d" % student))
    admin.post("/admin/students/%d/sessions" % student,
               data=_session_form(token, attendance="absent", activity_rating="",
                                  homework_status="not_assigned"))
    rows = db.fetch_sessions(student)
    assert len(rows) == 1 and rows[0]["activity_rating"] is None


@pytest.mark.parametrize("value", ["0", "6", "sedam"])
def test_activity_outside_the_scale_is_refused(admin, db, student, value):
    token = _csrf_from(admin.get("/admin/students/%d" % student))
    admin.post("/admin/students/%d/sessions" % student,
               data=_session_form(token, activity_rating=value))
    assert db.fetch_sessions(student) == []


def test_invalid_date_is_refused(admin, db, student):
    token = _csrf_from(admin.get("/admin/students/%d" % student))
    admin.post("/admin/students/%d/sessions" % student,
               data=_session_form(token, session_date="05.08.2026"))
    assert db.fetch_sessions(student) == []


def test_session_can_be_edited(admin, db, student):
    token = _csrf_from(admin.get("/admin/students/%d" % student))
    admin.post("/admin/students/%d/sessions" % student, data=_session_form(token))
    session_id = db.fetch_sessions(student)[0]["id"]
    admin.post("/admin/students/%d/sessions/%d" % (student, session_id),
               data=_session_form(token, activity_rating="2"))
    assert db.fetch_sessions(student)[0]["activity_rating"] == 2


def test_session_delete_requires_csrf_and_post(admin, db, student):
    token = _csrf_from(admin.get("/admin/students/%d" % student))
    admin.post("/admin/students/%d/sessions" % student, data=_session_form(token))
    session_id = db.fetch_sessions(student)[0]["id"]

    # GET NIKAD ne mijenja stanje.
    assert admin.get("/admin/students/%d/sessions/%d/delete"
                     % (student, session_id)).status_code in (404, 405)
    assert len(db.fetch_sessions(student)) == 1

    assert admin.post("/admin/students/%d/sessions/%d/delete"
                      % (student, session_id), data={}).status_code == 400
    assert len(db.fetch_sessions(student)) == 1

    admin.post("/admin/students/%d/sessions/%d/delete" % (student, session_id),
               data={"csrf_token": token})
    assert db.fetch_sessions(student) == []


def test_another_students_session_cannot_be_edited_through_the_wrong_route(
        admin, db, student):
    """IDOR: pogođen `session_id` u tuđoj ruti ne smije proći."""
    other = db.create_student("Drugi Učenik", 6)
    token = _csrf_from(admin.get("/admin/students/%d" % student))
    admin.post("/admin/students/%d/sessions" % student, data=_session_form(token))
    session_id = db.fetch_sessions(student)[0]["id"]

    response = admin.post("/admin/students/%d/sessions/%d" % (other, session_id),
                          data=_session_form(token, activity_rating="1"))
    assert response.status_code == 404
    assert db.fetch_sessions(student)[0]["activity_rating"] == 4

    assert admin.post("/admin/students/%d/sessions/%d/delete" % (other, session_id),
                      data={"csrf_token": token}).status_code == 404
    assert len(db.fetch_sessions(student)) == 1


def test_class_history_is_visible_on_the_profile(admin, db, student):
    token = _csrf_from(admin.get("/admin/students/%d" % student))
    admin.post("/admin/students/%d/sessions" % student,
               data=_session_form(token, comment="Samostalno riješio primjere."))
    page = admin.get("/admin/students/%d" % student)
    assert "Samostalno riješio primjere.".encode() in page.data
    assert b"2026-08-05" in page.data


def test_comment_markup_is_escaped_in_the_admin_history(admin, db, student):
    token = _csrf_from(admin.get("/admin/students/%d" % student))
    admin.post("/admin/students/%d/sessions" % student,
               data=_session_form(token, comment="<script>alert(1)</script>"))
    page = admin.get("/admin/students/%d" % student)
    assert b"<script>alert(1)</script>" not in page.data
    assert b"&lt;script&gt;" in page.data


def test_unknown_student_profile_is_404(admin, db):
    assert admin.get("/admin/students/99999").status_code == 404
