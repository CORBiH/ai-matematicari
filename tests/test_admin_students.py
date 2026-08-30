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
                                     "zauzet@example.com")
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
            "homework_status": "done", "area_name": "Djeljivost brojeva",
            "lesson_name": "Djeljivost zbira, razlike i proizvoda", "comment": "Dobar rad."}
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


# ---------------------------------------------------------------------------
# Faza 3D otvrdnjavanje: atomičan identitet i kanonsko gradivo kroz RUTU
# ---------------------------------------------------------------------------
def _students_in_db(db):
    conn = db._connection()
    return conn.execute("SELECT COUNT(*) FROM students").fetchall()[0][0]


def test_conflicting_email_leaves_no_new_student_through_the_route(admin, db):
    """ŽIVI DEFEKT: ranije bi učenik ostao upisan iako nalog nije povezan."""
    from matbot.student_identity import PROVIDER_THINKIFIC_EMAIL

    owner = db.get_or_create_student(PROVIDER_THINKIFIC_EMAIL,
                                     "zauzet@example.com")
    before = _students_in_db(db)

    token = _csrf_from(admin.get("/admin/students"))
    page = admin.post("/admin/students/create",
                      data={"csrf_token": token, "display_name": "Duplikat",
                            "grade": "6", "thinkific_email": "zauzet@example.com"},
                      follow_redirects=True)

    assert _students_in_db(db) == before, "ostao je duplikat"
    assert "već povezan sa drugim učenikom".encode() in page.data
    # Veza na POSTOJEĆI zapis, bez ponavljanja adrese.
    assert ("/admin/students/%d" % owner).encode() in page.data
    assert b"zauzet@example.com" not in page.data


def test_mixed_case_email_is_normalized_by_the_route(admin, db):
    token = _csrf_from(admin.get("/admin/students"))
    admin.post("/admin/students/create",
               data={"csrf_token": token, "display_name": "Prvi", "grade": "6",
                     "thinkific_email": "Ucenik@Example.COM"})
    first = _students_in_db(db)
    # Ista adresa drukčijim slovima je ISTI identitet — drugi upis pada.
    token = _csrf_from(admin.get("/admin/students"))
    admin.post("/admin/students/create",
               data={"csrf_token": token, "display_name": "Drugi", "grade": "6",
                     "thinkific_email": "  ucenik@example.com "})
    assert _students_in_db(db) == first


def test_session_form_offers_only_this_grade_curriculum(admin, db):
    from matbot import topics

    student_id = db.create_student("Šestak", 6)
    page = admin.get("/admin/students/%d" % student_id).data.decode("utf-8")
    for area in topics.curriculum_areas(6)[:3]:
        assert area in page
    # Oblast SEDMOG razreda se ne nudi šestaku.
    seventh_only = [a for a in topics.curriculum_areas(7)
                    if a not in topics.curriculum_areas(6)]
    assert seventh_only, "razredi dijele sve oblasti — test bi bio prazan"
    assert seventh_only[0] not in page


def test_non_canonical_curriculum_is_refused_by_the_route(admin, db, student):
    token = _csrf_from(admin.get("/admin/students/%d" % student))
    for area, lesson in (("Izmišljena oblast", "Bilo šta"),
                         ("Djeljivost brojeva", "Izmišljena lekcija"),
                         ("<script>x</script>", "y")):
        admin.post("/admin/students/%d/sessions" % student,
                   data=_session_form(token, area_name=area, lesson_name=lesson))
    assert db.fetch_sessions(student) == []


def test_lesson_from_another_grade_is_refused_by_the_route(admin, db, student):
    """Razred dolazi IZ BAZE, pa klijent ne može izabrati povoljniji."""
    token = _csrf_from(admin.get("/admin/students/%d" % student))
    admin.post("/admin/students/%d/sessions" % student,
               data=_session_form(token, area_name="Cijeli brojevi",
                                  lesson_name="Skup cijelih brojeva Z"))
    assert db.fetch_sessions(student) == []


# ---------------------------------------------------------------------------
# TEKUĆI RAZRED: ispravka, blokada bez razreda, upozorenje iz dokaza
# ---------------------------------------------------------------------------
def _grade_of(db, student_id):
    return db.fetch_student_profile(student_id)["grade"]


def test_admin_can_change_the_current_grade(admin, db, student):
    token = _csrf_from(admin.get("/admin/students/%d" % student))
    response = admin.post("/admin/students/%d/grade" % student,
                          data={"csrf_token": token, "grade": "7"})
    assert response.status_code == 302
    assert _grade_of(db, student) == 7


def test_changing_grade_requires_csrf(admin, db, student):
    response = admin.post("/admin/students/%d/grade" % student,
                          data={"grade": "7"})
    assert response.status_code == 400
    assert _grade_of(db, student) == 6


def test_changing_grade_requires_admin(client, db, student, admin_env):
    assert client.post("/admin/students/%d/grade" % student,
                       data={"grade": "7"}).status_code == 403
    assert _grade_of(db, student) == 6


def test_get_cannot_change_the_grade(admin, db, student):
    assert admin.get("/admin/students/%d/grade?grade=7"
                     % student).status_code in (404, 405)
    assert _grade_of(db, student) == 6


@pytest.mark.parametrize("grade", ["5", "10", "", "sedmi", "0"])
def test_invalid_grade_is_refused(admin, db, student, grade):
    token = _csrf_from(admin.get("/admin/students/%d" % student))
    admin.post("/admin/students/%d/grade" % student,
               data={"csrf_token": token, "grade": grade})
    assert _grade_of(db, student) == 6


def test_session_form_is_blocked_until_the_grade_is_confirmed(admin, db):
    """Bez potvrđenog razreda nema kurikuluma — ni nasumičnog."""
    from matbot.student_identity import PROVIDER_THINKIFIC_EMAIL

    unknown = db.get_or_create_student(PROVIDER_THINKIFIC_EMAIL,
                                       "bez-razreda@example.com")
    page = admin.get("/admin/students/%d" % unknown)
    assert ("Potrebno je potvrditi trenutni razred učenika prije unosa "
            "časa.").encode() in page.data
    # Forma gradiva se ne nudi.
    assert b'name="area_name"' not in page.data

    token = _csrf_from(page)
    admin.post("/admin/students/%d/sessions" % unknown,
               data=_session_form(token))
    assert db.fetch_sessions(unknown) == []


def test_confirming_the_grade_unlocks_the_session_form(admin, db):
    from matbot.student_identity import PROVIDER_THINKIFIC_EMAIL

    unknown = db.get_or_create_student(PROVIDER_THINKIFIC_EMAIL,
                                       "bez-razreda@example.com")
    token = _csrf_from(admin.get("/admin/students/%d" % unknown))
    admin.post("/admin/students/%d/grade" % unknown,
               data={"csrf_token": token, "grade": "6"})
    page = admin.get("/admin/students/%d" % unknown)
    assert b'name="area_name"' in page.data
    assert "Potrebno je potvrditi trenutni razred".encode() not in page.data


def _legacy(db, student_id, grade):
    """Zatečen red: razred postoji, potvrde nema.

    Ide direktno kroz SQL jer ga nijedna funkcija sloja više ne može
    proizvesti — a upravo tako izgleda svih 34 stvarna učenika."""
    conn = db._connection()
    conn.execute("UPDATE students SET grade = ?, grade_confirmed_at = NULL, "
                 " grade_source = NULL WHERE id = ?", (grade, student_id))
    conn.commit()
    return student_id


def _add_thinkific_grade(db, student_id, month, grade):
    conn = db._connection()
    conn.execute(
        "INSERT INTO thinkific_progress_imports (report_month, course_key, "
        " course_name, grade, source_sha256, row_count) "
        "VALUES (?, ?, 'M', ?, 'x', 1)", (month, "grade_%d" % grade, grade))
    import_id = conn.execute(
        "SELECT MAX(id) FROM thinkific_progress_imports").fetchall()[0][0]
    conn.execute(
        "INSERT INTO thinkific_progress_snapshots (import_id, student_id, "
        " report_month, course_key, course_name, grade) "
        "VALUES (?, ?, ?, ?, 'M', ?)",
        (import_id, student_id, month, "grade_%d" % grade, grade))
    conn.commit()


def test_confirmed_student_shows_as_confirmed(admin, db):
    student_id = db.create_student("Uredan", 7)
    _add_thinkific_grade(db, student_id, "2026-09", 7)
    assert "nepotvrđeno".encode() not in admin.get("/admin/students").data
    profile = admin.get("/admin/students/%d" % student_id)
    assert "Trenutni razred nije potvrđen".encode() not in profile.data


def test_legacy_student_shows_as_unconfirmed(admin, db):
    """Zatečen red: razred postoji, potvrde nema — i to se VIDI na listi."""
    student_id = _legacy(db, db.create_student("Zatecen", 6), 6)
    _add_thinkific_grade(db, student_id, "2026-09", 7)

    listing = admin.get("/admin/students")
    assert "nepotvrđeno".encode() in listing.data
    assert ("/admin/students/%d" % student_id).encode() in listing.data
    # Dugme za potvrdu zatecene vrijednosti postoji na samoj listi.
    assert "Potvrdi 6. razred".encode() in listing.data

    profile = admin.get("/admin/students/%d" % student_id)
    assert "Trenutni razred nije potvrđen".encode() in profile.data
    assert b"2026-09" in profile.data


def test_confirm_button_on_the_listing_confirms_the_stored_grade(admin, db):
    student_id = _legacy(db, db.create_student("Zatecen", 6), 6)
    token = _csrf_from(admin.get("/admin/students"))
    admin.post("/admin/students/%d/grade/confirm" % student_id,
               data={"csrf_token": token})
    saved = db.fetch_student_profile(student_id)
    assert saved["grade"] == 6, "potvrda ne smije promijeniti vrijednost"
    assert saved["grade_source"] == "admin"
    assert saved["grade_confirmed_at"]


def test_confirmation_state_never_exposes_the_email(admin, db):
    from matbot.student_identity import PROVIDER_THINKIFIC_EMAIL

    student_id = db.get_or_create_student(PROVIDER_THINKIFIC_EMAIL,
                                          "tajna@example.com")
    _legacy(db, student_id, 6)
    _add_thinkific_grade(db, student_id, "2026-09", 7)
    for page in (admin.get("/admin/students"),
                 admin.get("/admin/students/%d" % student_id)):
        assert b"tajna@example.com" not in page.data


def test_name_hint_never_confirms_or_changes_a_grade(admin, db):
    """„Adjan 7 PLUS" ostaje nepotvrđen ŠESTI — ime ništa ne mijenja."""
    student_id = _legacy(db, db.create_student("Adjan 7 PLUS", 6), 6)
    listing = admin.get("/admin/students")
    assert "Adjan 7 PLUS".encode() in listing.data
    assert "nepotvrđeno".encode() in listing.data
    assert db.fetch_student_profile(student_id)["grade"] == 6
    # Broj iz imena se smije POKAZATI covjeku, ali kao pitanje, ne kao odluka.
    profile = admin.get("/admin/students/%d" % student_id)
    assert "provjerite s instruktorom".encode() in profile.data


# ---------------------------------------------------------------------------
# SUKOB DOKAZA: upozorenje mora pasti i kad se Thinkific SLAŽE sa profilom
# ---------------------------------------------------------------------------
def _add_exam_and_activity(db, student_id, grade, when="2026-08-25 18:06:49"):
    conn = db._connection()
    conn.execute(
        "INSERT INTO assessment_attempts (student_id, source, assessment_type, "
        " external_attempt_id, grade, score_percent, correct_count, total_count, "
        " completed_at) VALUES (?, 'matbot', 'kontrolni', ?, ?, 60.0, 3, 5, ?)",
        (student_id, "e%d" % grade, grade, when))
    conn.execute(
        "INSERT INTO learning_activity (student_id, source, event_type, "
        " event_key, grade, occurred_at) "
        "VALUES (?, 'matbot', 'practice_answer_correct', ?, ?, ?)",
        (student_id, "k%d" % grade, grade, "2026-08-25 18:06:31"))
    conn.commit()


def test_edin_shaped_case_stays_unconfirmed_until_a_human_decides(admin, db):
    """Thinkific 6 slaže se s profilom, ali kontrolni i MAT-BOT kažu 9."""
    student_id = _legacy(db, db.create_student("Sintetički Sporni", 6), 6)
    _add_thinkific_grade(db, student_id, "2026-08", 6)
    _add_exam_and_activity(db, student_id, 9)

    listing = admin.get("/admin/students")
    assert "nepotvrđeno".encode() in listing.data

    profile = admin.get("/admin/students/%d" % student_id)
    assert "Trenutni razred nije potvrđen".encode() in profile.data
    # Sadrzaj se PRIKAZUJE, ali nijedan razred se ne predlaze.
    assert "Korišteno gradivo".encode() in profile.data
    assert "Prijedlog".encode() not in profile.data


def test_confirmed_ninth_grader_using_sixth_grade_content_keeps_the_profile(admin, db):
    """SRŽ FAZE: gradivo šestog razreda ne obara potvrđeni deveti."""
    student_id = db.create_student("Sintetički Devetak", 9)
    _add_thinkific_grade(db, student_id, "2026-08", 6)
    _add_exam_and_activity(db, student_id, 9)

    profile = admin.get("/admin/students/%d" % student_id)
    assert "Trenutni razred nije potvrđen".encode() not in profile.data
    assert "obnavljanja".encode() in profile.data
    assert db.fetch_student_profile(student_id)["grade"] == 9


def test_confirmed_student_whose_content_agrees_shows_no_difference(admin, db):
    student_id = db.create_student("Sintetički Uredan", 6)
    _add_thinkific_grade(db, student_id, "2026-08", 6)
    _add_exam_and_activity(db, student_id, 6)
    listing = admin.get("/admin/students")
    assert "nepotvrđeno".encode() not in listing.data
    profile = admin.get("/admin/students/%d" % student_id)
    assert "Trenutni razred nije potvrđen".encode() not in profile.data
    assert "obnavljanja".encode() not in profile.data


def test_old_year_evidence_does_not_raise_a_false_warning(admin, db):
    """Šesti lani, šesti sada — napredak nije razlika."""
    student_id = db.create_student("Sintetički Stari", 6)
    _add_thinkific_grade(db, student_id, "2026-08", 6)
    _add_exam_and_activity(db, student_id, 6, when="2026-08-20 10:00:00")
    conn = db._connection()
    conn.execute(
        "INSERT INTO assessment_attempts (student_id, source, assessment_type, "
        " external_attempt_id, grade, score_percent, correct_count, total_count, "
        " completed_at) VALUES (?, 'matbot', 'kontrolni', 'old', 5, 60.0, 3, 5, "
        " '2025-10-01 10:00:00')", (student_id,))
    conn.commit()
    profile = admin.get("/admin/students/%d" % student_id)
    assert "Trenutni razred nije potvrđen".encode() not in profile.data
    assert "obnavljanja".encode() not in profile.data
