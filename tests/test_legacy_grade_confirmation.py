"""Faza 3D+ — POTVRDA ZATEČENOG RAZREDA MORA BITI IZRIČITA.

ŠTA JE BILO OPASNO. Prva verzija administratorskog registra je za nepotvrđenog
učenika nudila jedan klik: „Potvrdi 6. razred". Za 34 zatečena reda ta šestica
NIJE bila ničija odluka — upisala ju je stara automatika (Thinkific uvoz i
unaprijed izabrana opcija u tutorskom meniju). Jedan klik bi je pretvorio u
POTVRĐENU šesticu, a da administrator nijednom nije izabrao broj: tiho
ozvaničenje upravo one vrijednosti zbog koje cijela faza postoji.

ŠTA JE SADA. Jedna radnja, `Sačuvaj i potvrdi`, i ona UVIJEK nosi izabrani
razred. Zatečena vrijednost se PRIKAZUJE („Trenutno zapisano: 6. razred
(nepotvrđeno)") ali se nikad ne predizabire.

BROJ U IMENU. Operator je izričito potvrdio da je broj u imenima ZATEČENOG
skupa namjeran i da označava stvarni tekući razred („Amar 7 Septembar" = sedmi).
Zato smije PREDIZABRATI vrijednost u formularu — i ništa više:

  • iscrtavanje stranice ne mijenja nijedan red;
  • backend upisuje POSLANI razred, nikad broj iz imena;
  • `classify()` ime ne vidi ni u potpisu ni u tijelu;
  • identitet, uvoz i izvještaj i dalje ne smiju izvesti razred iz imena.

PII: svi učenici su sintetički.
"""
import re

import pytest

from matbot import (admin_students, report_input, reporting_db,
                    reporting_schema, student_grades, student_sessions)

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


def csrf_from(response):
    match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
    assert match, "CSRF token nije u formularu"
    return match.group(1).decode()


@pytest.fixture
def admin(client, admin_env):
    token = csrf_from(client.get("/admin/reports/login"))
    assert client.post("/admin/reports/login",
                       data={"csrf_token": token,
                             "password": PASSWORD}).status_code == 302
    return client


def legacy(db, name, grade=6):
    """Zatečen red: ime, stara automatska šestica, NIKAKVA potvrda.

    Ide direktno kroz SQL jer ga nijedna funkcija sloja više ne može
    proizvesti — a upravo tako izgleda svih 34 stvarna reda."""
    student_id = db.create_student(name, grade)
    conn = db._connection()
    conn.execute("UPDATE students SET grade = ?, grade_confirmed_at = NULL, "
                 " grade_source = NULL WHERE id = ?", (grade, student_id))
    conn.commit()
    return student_id


def selector(page):
    """Sadržaj `<select name="grade">` s administratorske stranice."""
    match = re.search(rb'<select name="grade"[^>]*>(.*?)</select>', page.data,
                      re.S)
    assert match, "selektor razreda nije prikazan"
    return match.group(1).decode()


def preselected(page):
    """Koja je opcija PREDIZABRANA? `None` znači „— izaberi razred —"."""
    block = selector(page)
    chosen = re.findall(r'<option value="([^"]*)"[^>]*\bselected\b', block)
    assert len(chosen) == 1, "tačno jedna opcija smije biti predizabrana"
    return int(chosen[0]) if chosen[0] else None


# ===========================================================================
# 1-6) PREDIZBOR IZ IMENA, UPIS IZ FORMULARA
# ===========================================================================
def test_1_legacy_six_with_name_hint_seven_preselects_seven(admin, db):
    student_id = legacy(db, "Amar 7 Septembar")
    page = admin.get("/admin/students/%d" % student_id)

    assert "Trenutno zapisano: 6. razred (nepotvrđeno)".encode() in page.data
    assert "Broj u imenu: 7. razred".encode() in page.data
    assert preselected(page) == 7, "predizabran je zatečeni razred umjesto imena"
    assert "Sačuvaj i potvrdi".encode() in page.data


def test_2_rendering_the_page_changes_nothing(admin, db):
    """Nagovještaj je PRIKAZ. Sam pogled ne smije upisati ni jedno polje."""
    student_id = legacy(db, "Amar 7 Septembar")
    before = db.fetch_student_profile(student_id)

    for _ in range(3):
        admin.get("/admin/students/%d" % student_id)
        admin.get("/admin/students")

    assert db.fetch_student_profile(student_id) == before
    assert before["grade"] == 6 and before["grade_confirmed_at"] is None


@pytest.mark.parametrize("name, hint", [
    ("Amar 7 Septembar", 7),      # 3
    ("Muhamed 8 PLUS", 8),        # 4
    ("student L9", 9),            # 5
    ("Sesti 6 razred", 6),        # 6
])
def test_3_to_6_submitting_the_selected_grade_confirms_atomically(
        admin, db, name, hint):
    student_id = legacy(db, name)
    page = admin.get("/admin/students/%d" % student_id)
    assert preselected(page) == hint

    admin.post("/admin/students/%d/grade" % student_id,
               data={"csrf_token": csrf_from(page), "grade": str(hint)})

    saved = db.fetch_student_profile(student_id)
    assert saved["grade"] == hint
    assert saved["grade_confirmed_at"], "vrijeme potvrde nije upisano"
    assert saved["grade_source"] == student_grades.GRADE_SOURCE_ADMIN
    assert student_grades.is_confirmed(saved["grade"], saved["grade_confirmed_at"],
                                       saved["grade_source"])


def test_3b_admin_may_submit_a_grade_that_contradicts_the_name(admin, db):
    """Ime PREDLAŽE, čovjek ODLUČUJE — i njegova odluka je ta koja se upisuje."""
    student_id = legacy(db, "Amar 7 Septembar")
    page = admin.get("/admin/students/%d" % student_id)
    admin.post("/admin/students/%d/grade" % student_id,
               data={"csrf_token": csrf_from(page), "grade": "9"})
    assert db.fetch_student_profile(student_id)["grade"] == 9


# ===========================================================================
# 7) BEZ NAGOVJEŠTAJA NEMA PREDIZBORA
# ===========================================================================
@pytest.mark.parametrize("name", [
    "Edin Mujacic", "Elma Opijač", "Farah L", "rezervna gimnazija",
    "Ucenik 2026",
])
def test_7_a_student_without_a_hint_never_preselects_the_legacy_value(
        admin, db, name):
    """Zatečena šestica se VIDI, ali se ne nudi kao izbor."""
    student_id = legacy(db, name)
    page = admin.get("/admin/students/%d" % student_id)

    assert "Trenutno zapisano: 6. razred (nepotvrđeno)".encode() in page.data
    assert preselected(page) is None
    assert "izaberi razred".encode() in page.data
    assert "Broj u imenu".encode() not in page.data


def test_7b_an_empty_selection_is_refused_and_writes_nothing(admin, db):
    student_id = legacy(db, "Edin Mujacic")
    page = admin.get("/admin/students/%d" % student_id)
    answer = admin.post("/admin/students/%d/grade" % student_id,
                        data={"csrf_token": csrf_from(page), "grade": ""})

    assert answer.status_code == 302
    saved = db.fetch_student_profile(student_id)
    assert saved["grade"] == 6 and saved["grade_confirmed_at"] is None


# ===========================================================================
# 8-9) BROJ IZVAN 6–9
# ===========================================================================
def test_8_an_out_of_range_hint_is_shown_but_never_preselected(admin, db):
    student_id = legacy(db, "Student 10 Gimnazija")
    page = admin.get("/admin/students/%d" % student_id)

    assert "Broj u imenu: 10 — izvan podržanih razreda 6–9".encode() in page.data
    # NE zaokružuje se na 9 i NE svodi na 6.
    assert preselected(page) is None
    assert student_grades.name_grade_hint("Student 10 Gimnazija") is None


def test_9_an_out_of_range_student_stays_unconfirmed_without_a_decision(admin, db):
    student_id = legacy(db, "Student 10 Gimnazija")
    page = admin.get("/admin/students/%d" % student_id)

    # Neispravan izbor se odbija i ostavlja red nepotvrđenim.
    for refused in ("10", "5", "0", "-1", ""):
        admin.post("/admin/students/%d/grade" % student_id,
                   data={"csrf_token": csrf_from(page), "grade": refused})
        saved = db.fetch_student_profile(student_id)
        assert saved["grade_confirmed_at"] is None, refused

    # Tek izričita PODRŽANA odluka potvrđuje.
    admin.post("/admin/students/%d/grade" % student_id,
               data={"csrf_token": csrf_from(page), "grade": "9"})
    saved = db.fetch_student_profile(student_id)
    assert saved["grade"] == 9 and saved["grade_confirmed_at"]


def test_9b_two_numbers_in_a_name_never_preselect(admin, db):
    student_id = legacy(db, "Grupa 7/8")
    page = admin.get("/admin/students/%d" % student_id)
    assert "nije jednoznačno".encode() in page.data
    assert preselected(page) is None


# ===========================================================================
# 10-11) GRANICA NAGOVJEŠTAJA
# ===========================================================================
def test_10_the_name_hint_never_reaches_the_classifier():
    import inspect
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    assert set(inspect.signature(student_grades.classify).parameters) == {
        "grade", "grade_confirmed_at", "grade_source", "evidence"}
    source = (root / "matbot" / "student_grades.py").read_text(encoding="utf-8")
    body = source.split("def classify(")[1].split("\ndef ")[0]
    assert "name_grade_hint" not in body and "name_grade_note" not in body
    assert "display_name" not in body


def test_10b_only_the_admin_form_may_consult_the_name(db):
    """Identitet, uvoz i izvještaj ne smiju ni pomenuti nagovještaj."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for module in ("reporting_db.py", "report_input.py", "report_facts.py",
                   "parent_report.py", "student_identity.py",
                   "thinkific_progress.py", "admin_reports.py"):
        text = (root / "matbot" / module).read_text(encoding="utf-8")
        assert "name_grade_hint" not in text, module
        assert "name_grade_note" not in text, module


def test_11_the_hint_writes_nothing_without_a_post(admin, db):
    """Bez POST-a nema izmjene — ni poslije proizvoljno mnogo pogleda."""
    student_id = legacy(db, "Andrej 7")
    admin.get("/admin/students")
    admin.get("/admin/students/%d" % student_id)
    admin.get("/admin/students?confirmed=nepotvrdjen")

    saved = db.fetch_student_profile(student_id)
    assert saved["grade"] == 6
    assert saved["grade_confirmed_at"] is None and saved["grade_source"] is None


# ===========================================================================
# 12) POTVRĐEN UČENIK SE NE DIRA NAGOVJEŠTAJEM
# ===========================================================================
def test_12_a_confirmed_student_is_not_altered_by_a_later_name_hint(admin, db):
    """Potvrđena vrijednost je autoritet dok je čovjek izričito ne promijeni."""
    student_id = db.create_student("Amar 7 Septembar", 9)   # potvrđen deveti
    page = admin.get("/admin/students/%d" % student_id)

    assert preselected(page) == 9, "nagovještaj je pregazio potvrđenu vrijednost"
    assert "Broj u imenu".encode() not in page.data
    assert "Trenutno zapisano".encode() not in page.data
    assert db.fetch_student_profile(student_id)["grade"] == 9

    listing = admin.get("/admin/students")
    assert "Sačuvaj i potvrdi".encode() not in listing.data


# ===========================================================================
# 13-16) SIGURNOST
# ===========================================================================
@pytest.mark.parametrize("value", ["5", "10", "0", "-1", "99", "abc", "",
                                   "7; DROP TABLE students", "6.0"])
def test_13_the_post_accepts_only_six_to_nine(admin, db, value):
    student_id = legacy(db, "Edin Mujacic")
    token = csrf_from(admin.get("/admin/students/%d" % student_id))
    admin.post("/admin/students/%d/grade" % student_id,
               data={"csrf_token": token, "grade": value})
    saved = db.fetch_student_profile(student_id)
    assert saved["grade"] == 6 and saved["grade_confirmed_at"] is None


def test_14_confirmation_requires_csrf(admin, db):
    student_id = legacy(db, "Amar 7 Septembar")
    answer = admin.post("/admin/students/%d/grade" % student_id,
                        data={"grade": "7"})
    assert answer.status_code == 400
    assert db.fetch_student_profile(student_id)["grade_confirmed_at"] is None


def test_15_confirmation_requires_admin_auth(client, db, admin_env):
    student_id = legacy(db, "Amar 7 Septembar")
    answer = client.post("/admin/students/%d/grade" % student_id,
                         data={"grade": "7"})
    assert answer.status_code in (302, 401, 403)
    assert db.fetch_student_profile(student_id)["grade_confirmed_at"] is None
    # GET nikad ne mijenja stanje.
    client.get("/admin/students/%d/grade" % student_id)
    assert db.fetch_student_profile(student_id)["grade_confirmed_at"] is None


def test_16_grade_source_is_server_owned(admin, db):
    """Klijent ne smije izabrati ko je „potvrdio" razred."""
    student_id = legacy(db, "Amar 7 Septembar")
    token = csrf_from(admin.get("/admin/students/%d" % student_id))
    admin.post("/admin/students/%d/grade" % student_id,
               data={"csrf_token": token, "grade": "7",
                     "grade_source": "thinkific_progress",
                     "grade_confirmed_at": "1999-01-01 00:00:00"})

    saved = db.fetch_student_profile(student_id)
    assert saved["grade_source"] == student_grades.GRADE_SOURCE_ADMIN
    assert not saved["grade_confirmed_at"].startswith("1999")


def test_16b_the_return_target_cannot_be_an_arbitrary_url(admin, db):
    student_id = legacy(db, "Amar 7 Septembar")
    token = csrf_from(admin.get("/admin/students"))
    answer = admin.post("/admin/students/%d/grade" % student_id,
                        data={"csrf_token": token, "grade": "7",
                              "next": "https://zlonamjeran.example.com"})
    assert answer.status_code == 302
    assert "zlonamjeran" not in answer.headers["Location"]
    assert answer.headers["Location"].startswith("/admin/students/")


# ===========================================================================
# 17-19) BLOKADE PRIJE POTVRDE, OTKLJUČAVANJE POSLIJE
# ===========================================================================
def test_17_session_entry_stays_blocked_before_confirmation(admin, db):
    student_id = legacy(db, "Amar 7 Septembar")
    page = admin.get("/admin/students/%d" % student_id)

    assert ("Potrebno je potvrditi trenutni razred učenika prije unosa "
            "časa.").encode() in page.data
    assert b'name="area_name"' not in page.data
    with pytest.raises(admin_students._GradeUnknown):
        admin_students._student_grade(student_id)


def test_18_report_generation_stays_blocked_before_confirmation(db):
    student_id = legacy(db, "Amar 7 Septembar")
    payload = report_input.build_report_input(student_id, "2026-08")
    assert payload["profile"]["grade_confirmed"] is False


def test_19_the_confirmed_grade_unlocks_that_grades_curriculum(admin, db):
    """Potvrđen sedmi otvara gradivo SEDMOG, ne zatečenog šestog."""
    student_id = legacy(db, "Amar 7 Septembar")
    page = admin.get("/admin/students/%d" % student_id)
    admin.post("/admin/students/%d/grade" % student_id,
               data={"csrf_token": csrf_from(page), "grade": "7"})

    assert admin_students._student_grade(student_id) == 7
    assert report_input.build_report_input(
        student_id, "2026-08")["profile"]["grade_confirmed"] is True

    # Gradivo sedmog razreda prolazi...
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
# 20) ISTORIJA OSTAJE NETAKNUTA
# ===========================================================================
def test_20_confirming_leaves_every_historical_row_unchanged(admin, db):
    student_id = legacy(db, "Amar 7 Septembar")
    conn = db._connection()
    conn.execute(
        "INSERT INTO thinkific_progress_imports (report_month, course_key, "
        " course_name, grade, source_sha256, row_count) "
        "VALUES ('2026-08', 'grade_6', 'M6', 6, 'sha', 1)")
    conn.execute(
        "INSERT INTO thinkific_progress_snapshots (import_id, student_id, "
        " report_month, course_key, course_name, grade) "
        "VALUES (1, ?, '2026-08', 'grade_6', 'M6', 6)", (student_id,))
    conn.execute(
        "INSERT INTO assessment_attempts (student_id, source, assessment_type, "
        " external_attempt_id, grade, score_percent, correct_count, total_count, "
        " completed_at) VALUES (?, 'matbot', 'kontrolni', 'e1', 6, 60.0, 3, 5, "
        " '2026-08-02 10:00:00')", (student_id,))
    conn.execute(
        "INSERT INTO learning_activity (student_id, source, event_type, "
        " event_key, grade, occurred_at) "
        "VALUES (?, 'matbot', 'practice_answer_correct', 'k1', 6, "
        " '2026-08-01 10:00:00')", (student_id,))
    conn.commit()
    db.insert_session(student_id, student_sessions.validate_session(
        session_date="2026-08-03", attendance="present", activity_rating=4,
        homework_status="done", area_name="Djeljivost brojeva",
        lesson_name="Djeljivost zbira, razlike i proizvoda", grade=6))
    db.save_monthly_report(student_id=student_id, report_month="2026-08",
                           metrics_json='{"a": 1}', ai_summary="stari nacrt",
                           instructor_comment="komentar")

    before = {
        "snapshots": conn.execute(
            "SELECT grade, report_month FROM thinkific_progress_snapshots"
        ).fetchall(),
        "assessments": conn.execute(
            "SELECT grade FROM assessment_attempts").fetchall(),
        "activity": conn.execute("SELECT grade FROM learning_activity").fetchall(),
    }
    sessions_before = db.fetch_sessions(student_id)
    report_before = db.fetch_monthly_report(student_id, "2026-08")

    page = admin.get("/admin/students/%d" % student_id)
    admin.post("/admin/students/%d/grade" % student_id,
               data={"csrf_token": csrf_from(page), "grade": "7"})

    assert conn.execute("SELECT grade, report_month FROM "
                        "thinkific_progress_snapshots").fetchall() == \
        before["snapshots"]
    assert conn.execute("SELECT grade FROM assessment_attempts").fetchall() == \
        before["assessments"]
    assert conn.execute("SELECT grade FROM learning_activity").fetchall() == \
        before["activity"]
    assert db.fetch_sessions(student_id) == sessions_before
    assert db.fetch_monthly_report(student_id, "2026-08") == report_before
    assert db.fetch_student_profile(student_id)["grade"] == 7
