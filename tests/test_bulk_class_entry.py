"""Faza 3D+ — „Upiši čas": jedan čas, više učenika, ista kanonska evidencija.

TVRDNJA KOJU OVAJ FAJL ČUVA: brzi unos NIJE drugi izvještajni sistem. Red koji
nastane ovdje mora biti NERAZLUČIV od reda unesenog kroz profil učenika — isti
`student_sessions`, ista validacija, isti mjesečni sažetak. Zato ovdje postoji
test koji dva učenika s identičnim časovima puni RAZLIČITIM putevima i traži
BAJT ZA BAJT iste metrike.

DRUGA TVRDNJA: spisak časa je serverska odluka. Ni ime, ni Thinkific kurs, ni
kontrolni, ni MAT-BOT aktivnost ne mogu nikoga staviti na spisak — samo
administratorom POTVRĐEN tekući razred.

TREĆA: „nije na ovom času" nije izostanak. Unutar razreda postoje grupe i
termini, pa učenik koga nije bilo na OVOM času ne smije dobiti lažan izostanak
koji bi mjesecima obarao prisustvo u izvještaju roditelju.

PII: svi učenici su sintetički.
"""
import re

import pytest

from matbot import (class_entry, report_input, reporting_db, reporting_schema,
                    student_sessions)
from matbot.student_identity import PROVIDER_THINKIFIC_EMAIL

from tests.test_thinkific_progress_import import build_v1, migrate

libsql = pytest.importorskip("libsql")

PASSWORD = "administratorska-lozinka-123"

DATE = "2026-09-07"
GRADE = 7
AREA = "Cijeli brojevi"
LESSON = "Skup cijelih brojeva Z"
OTHER_LESSON = "Prikaz na brojevnoj pravoj"
GRADE6_AREA = "Djeljivost brojeva"
GRADE6_LESSON = "Djeljivost zbira, razlike i proizvoda"


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


def confirmed(db, name, grade):
    """Ručni upis = potvrđen tekući razred (verzija 4)."""
    return db.create_student(name, grade)


def legacy(db, name, grade=6):
    """Zatečen red: razred postoji, potvrde nema."""
    student_id = db.create_student(name, grade)
    conn = db._connection()
    conn.execute("UPDATE students SET grade = ?, grade_confirmed_at = NULL, "
                 " grade_source = NULL WHERE id = ?", (grade, student_id))
    conn.commit()
    return student_id


def form(rows, *, date=DATE, grade=GRADE, area=AREA, lesson=LESSON, token=None):
    data = {"session_date": date, "grade": str(grade),
            "area_name": area, "lesson_name": lesson}
    if token:
        data["csrf_token"] = token
    for student_id, fields in rows.items():
        for key, value in fields.items():
            data["s%d_%s" % (student_id, key)] = value
    return data


def entry_page(admin, grade=GRADE, **extra):
    query = "/admin/sessions/new?grade=%s&session_date=%s&area_name=%s&lesson_name=%s" % (
        grade, extra.get("date", DATE), extra.get("area", AREA),
        extra.get("lesson", LESSON))
    return admin.get(query)


def sessions_of(db, student_id):
    return db.fetch_sessions(student_id)


# ===========================================================================
# 1-3) PRISTUP
# ===========================================================================
def test_1_page_requires_admin_auth(client, db, admin_env):
    answer = client.get("/admin/sessions/new")
    assert answer.status_code in (302, 401, 403)
    if answer.status_code == 302:
        assert "/admin/reports/login" in answer.headers["Location"]


def test_1b_bulk_save_requires_admin_auth(client, db, admin_env):
    student_id = confirmed(db, "Sedmak", GRADE)
    answer = client.post("/admin/sessions/bulk",
                         data=form({student_id: {"participation": "present",
                                                 "activity": "4"}}))
    assert answer.status_code in (302, 401, 403)
    assert sessions_of(db, student_id) == []


def test_2_mutation_requires_post(admin, db):
    """GET nikad ne mijenja stanje — ruta ga i ne prihvata."""
    assert admin.get("/admin/sessions/bulk").status_code == 405


def test_2b_opening_the_page_writes_nothing(admin, db):
    student_id = confirmed(db, "Sedmak", GRADE)
    for _ in range(3):
        entry_page(admin, GRADE)
    assert sessions_of(db, student_id) == []


def test_3_csrf_is_enforced(admin, db):
    student_id = confirmed(db, "Sedmak", GRADE)
    answer = admin.post("/admin/sessions/bulk",
                        data=form({student_id: {"participation": "present",
                                                "activity": "4"}}))
    assert answer.status_code == 400
    assert sessions_of(db, student_id) == []


# ===========================================================================
# 4-5) ZAJEDNIČKA POLJA ČASA
# ===========================================================================
def test_4_date_defaults_to_today(admin, db):
    import datetime

    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    page = admin.get("/admin/sessions/new")
    assert ('value="%s"' % today).encode() in page.data
    # I dalje se smije promijeniti.
    assert b'type="date"' in page.data


@pytest.mark.parametrize("grade", ["5", "10", "0", "abc", ""])
def test_5_only_grades_six_to_nine_are_accepted(admin, db, grade):
    student_id = confirmed(db, "Sedmak", GRADE)
    token = csrf_from(entry_page(admin, GRADE))
    admin.post("/admin/sessions/bulk",
               data=form({student_id: {"participation": "present",
                                       "activity": "4"}},
                         grade=grade, token=token))
    assert sessions_of(db, student_id) == []


# ===========================================================================
# 6-9) SPISAK JE SERVERSKA ODLUKA
# ===========================================================================
def test_6_only_confirmed_active_students_of_that_grade_appear(admin, db):
    mine = confirmed(db, "Sedmi Potvrdjen", 7)
    other = confirmed(db, "Osmi Potvrdjen", 8)
    page = entry_page(admin, 7)

    assert "Sedmi Potvrdjen".encode() in page.data
    assert "Osmi Potvrdjen".encode() not in page.data
    assert ('name="s%d_participation"' % mine).encode() in page.data
    assert ('name="s%d_participation"' % other).encode() not in page.data


def test_6b_inactive_students_are_not_offered(admin, db):
    active = confirmed(db, "Aktivan Sedmak", 7)
    gone = confirmed(db, "Arhiviran Sedmak", 7)
    conn = db._connection()
    conn.execute("UPDATE students SET status = 'archived' WHERE id = ?", (gone,))
    conn.commit()

    page = entry_page(admin, 7)
    assert "Aktivan Sedmak".encode() in page.data
    assert "Arhiviran Sedmak".encode() not in page.data
    assert active in [s["student_id"] for s in
                      __import__("matbot.admin_sessions", fromlist=["x"])
                      .class_roster(db, 7)[0]]


def test_7_unconfirmed_students_are_excluded_and_explained(admin, db):
    confirmed(db, "Potvrdjen Sedmak", 7)
    hidden = legacy(db, "Zatecen Ucenik", 7)
    page = entry_page(admin, 7)

    assert "Zatecen Ucenik".encode() not in page.data
    assert ('name="s%d_participation"' % hidden).encode() not in page.data
    assert "nije potvrđen".encode() in page.data
    assert b"confirmed=nepotvrdjen" in page.data


def test_7b_an_unconfirmed_student_cannot_be_forced_in(admin, db):
    hidden = legacy(db, "Zatecen Ucenik", 7)
    confirmed(db, "Potvrdjen Sedmak", 7)
    token = csrf_from(entry_page(admin, 7))
    admin.post("/admin/sessions/bulk",
               data=form({hidden: {"participation": "present", "activity": "5"}},
                         token=token))
    assert sessions_of(db, hidden) == []


def test_8_the_name_hint_cannot_put_a_student_on_the_roster(admin, db):
    """„Amar 7 Septembar" bez POTVRĐENOG sedmog razreda nije sedmak."""
    hinted = legacy(db, "Amar 7 Septembar", 6)
    page = entry_page(admin, 7)
    assert "Amar 7 Septembar".encode() not in page.data
    assert ('name="s%d_participation"' % hinted).encode() not in page.data


def test_9_thinkific_content_grade_cannot_put_a_student_on_the_roster(admin, db):
    """Kurs 7. razreda je SADRŽAJ; tekući razred ostaje potvrđeni šesti."""
    student_id = confirmed(db, "Sestak Koji Vjezba Sedmi", 6)
    conn = db._connection()
    conn.execute(
        "INSERT INTO thinkific_progress_imports (report_month, course_key, "
        " course_name, grade, source_sha256, row_count) "
        "VALUES ('2026-09', 'grade_7', 'M7', 7, 'sha', 1)")
    conn.execute(
        "INSERT INTO thinkific_progress_snapshots (import_id, student_id, "
        " report_month, course_key, course_name, grade) "
        "VALUES (1, ?, '2026-09', 'grade_7', 'M7', 7)", (student_id,))
    conn.execute(
        "INSERT INTO assessment_attempts (student_id, source, assessment_type, "
        " external_attempt_id, grade, score_percent, correct_count, total_count, "
        " completed_at) VALUES (?, 'matbot', 'kontrolni', 'e1', 7, 60.0, 3, 5, "
        " '2026-09-01 10:00:00')", (student_id,))
    conn.execute(
        "INSERT INTO learning_activity (student_id, source, event_type, "
        " event_key, grade, occurred_at) "
        "VALUES (?, 'matbot', 'practice_answer_correct', 'k1', 7, "
        " '2026-09-01 10:00:00')", (student_id,))
    conn.commit()

    page = entry_page(admin, 7)
    assert "Sestak Koji Vjezba Sedmi".encode() not in page.data
    # A u svom POTVRĐENOM šestom razredu se i dalje vidi.
    assert "Sestak Koji Vjezba Sedmi".encode() in entry_page(
        admin, 6, area=GRADE6_AREA, lesson=GRADE6_LESSON).data


# ===========================================================================
# 10) KURIKULUM
# ===========================================================================
def test_10_area_and_lesson_are_validated_against_the_canonical_curriculum(
        admin, db):
    student_id = confirmed(db, "Sedmak", 7)
    token = csrf_from(entry_page(admin, 7))
    row = {student_id: {"participation": "present", "activity": "4"}}

    # Lekcija drugog razreda.
    admin.post("/admin/sessions/bulk",
               data=form(row, area=GRADE6_AREA, lesson=GRADE6_LESSON, token=token))
    assert sessions_of(db, student_id) == []
    # Izmišljena lekcija.
    admin.post("/admin/sessions/bulk",
               data=form(row, lesson="Izmisljena lekcija", token=token))
    assert sessions_of(db, student_id) == []
    # Lekcija iz DRUGE oblasti istog razreda.
    admin.post("/admin/sessions/bulk",
               data=form(row, area="Ugao i trougao", lesson=LESSON, token=token))
    assert sessions_of(db, student_id) == []
    # Bez oblasti.
    admin.post("/admin/sessions/bulk",
               data=form(row, area="", token=token))
    assert sessions_of(db, student_id) == []

    # Kanonski par prolazi.
    admin.post("/admin/sessions/bulk", data=form(row, token=token))
    assert len(sessions_of(db, student_id)) == 1


# ===========================================================================
# 11-14) STANJA UČEŠĆA
# ===========================================================================
def test_11_present_requires_an_activity_rating(admin, db):
    student_id = confirmed(db, "Sedmak", 7)
    token = csrf_from(entry_page(admin, 7))
    admin.post("/admin/sessions/bulk",
               data=form({student_id: {"participation": "present"}}, token=token))
    assert sessions_of(db, student_id) == []


@pytest.mark.parametrize("homework", ["done", "not_done", "not_assigned"])
def test_12_present_accepts_every_canonical_homework_status(admin, db, homework):
    student_id = confirmed(db, "Sedmak", 7)
    token = csrf_from(entry_page(admin, 7))
    admin.post("/admin/sessions/bulk",
               data=form({student_id: {"participation": "present",
                                       "activity": "4",
                                       "homework": homework}}, token=token))
    stored = sessions_of(db, student_id)
    assert len(stored) == 1 and stored[0]["homework_status"] == homework


def test_13_absent_stores_no_activity_rating(admin, db):
    student_id = confirmed(db, "Sedmak", 7)
    token = csrf_from(entry_page(admin, 7))
    # Klijent ŠALJE angažman i zadaću; server ih za odsutnog ne uzima.
    admin.post("/admin/sessions/bulk",
               data=form({student_id: {"participation": "absent",
                                       "activity": "5",
                                       "homework": "done"}}, token=token))
    stored = sessions_of(db, student_id)
    assert len(stored) == 1
    assert stored[0]["attendance"] == "absent"
    assert stored[0]["activity_rating"] is None
    # Zadaća odsutnog je SERVERSKA i izostavljena iz imenioca.
    assert stored[0]["homework_status"] == class_entry.ABSENT_HOMEWORK
    assert class_entry.ABSENT_HOMEWORK == student_sessions.HOMEWORK_NOT_ASSIGNED


def test_14_not_scheduled_creates_no_row(admin, db):
    present = confirmed(db, "Bio Na Casu", 7)
    skipped = confirmed(db, "Druga Grupa", 7)
    token = csrf_from(entry_page(admin, 7))
    admin.post("/admin/sessions/bulk",
               data=form({present: {"participation": "present", "activity": "4"},
                          skipped: {"participation": "not_scheduled"}},
                         token=token))

    assert len(sessions_of(db, present)) == 1
    assert sessions_of(db, skipped) == [], "lažan izostanak za drugu grupu"


def test_14b_the_default_state_is_not_scheduled(admin, db):
    """Bez ijednog dodira formulara ne nastaje NIJEDAN red."""
    a = confirmed(db, "Prvi", 7)
    b = confirmed(db, "Drugi", 7)
    token = csrf_from(entry_page(admin, 7))
    admin.post("/admin/sessions/bulk", data=form({}, token=token))
    assert sessions_of(db, a) == [] and sessions_of(db, b) == []
    assert class_entry.PARTICIPATION_DEFAULT == "not_scheduled"


# ===========================================================================
# 15-18) JEDNO ČUVANJE, ATOMSKI
# ===========================================================================
def test_15_one_submit_saves_many_students(admin, db):
    ids = [confirmed(db, "Ucenik %d" % i, 7) for i in range(6)]
    token = csrf_from(entry_page(admin, 7))
    rows = {}
    for index, student_id in enumerate(ids):
        rows[student_id] = ({"participation": "absent"} if index == 5 else
                            {"participation": "present", "activity": "4",
                             "homework": "done"})
    answer = admin.post("/admin/sessions/bulk", data=form(rows, token=token))

    assert answer.status_code == 302
    for student_id in ids:
        assert len(sessions_of(db, student_id)) == 1
    assert sessions_of(db, ids[5])[0]["attendance"] == "absent"


def test_16_and_17_one_invalid_row_prevents_any_partial_save(admin, db):
    good = confirmed(db, "Dobar Red", 7)
    broken = confirmed(db, "Neispravan Red", 7)
    token = csrf_from(entry_page(admin, 7))

    admin.post("/admin/sessions/bulk",
               data=form({good: {"participation": "present", "activity": "4"},
                          # prisutan BEZ angažmana → cijeli čas pada
                          broken: {"participation": "present"}}, token=token))

    assert sessions_of(db, good) == [], "djelimično sačuvan čas"
    assert sessions_of(db, broken) == []


def test_17b_an_out_of_range_activity_kills_the_whole_class(admin, db):
    good = confirmed(db, "Dobar Red", 7)
    broken = confirmed(db, "Neispravan Red", 7)
    token = csrf_from(entry_page(admin, 7))
    admin.post("/admin/sessions/bulk",
               data=form({good: {"participation": "present", "activity": "4"},
                          broken: {"participation": "present", "activity": "9"}},
                         token=token))
    assert sessions_of(db, good) == [] and sessions_of(db, broken) == []


def test_18_cross_grade_student_injection_is_rejected(admin, db):
    """Podmetnut osmoškolac u sedmi razred obara cijeli zahtjev."""
    seventh = confirmed(db, "Sedmak", 7)
    eighth = confirmed(db, "Osmak", 8)
    token = csrf_from(entry_page(admin, 7))

    admin.post("/admin/sessions/bulk",
               data=form({seventh: {"participation": "present", "activity": "4"},
                          eighth: {"participation": "present", "activity": "5"}},
                         token=token))

    # Osmoškolac NIJE upisan, i sedmak nije tiho sačuvan pored njega.
    assert sessions_of(db, eighth) == []
    assert sessions_of(db, seventh) == []


# ===========================================================================
# 19-20) DUPLIKATI I ISPRAVKA
# ===========================================================================
def test_19_double_submit_creates_no_duplicate_logical_session(admin, db):
    student_id = confirmed(db, "Sedmak", 7)
    token = csrf_from(entry_page(admin, 7))
    data = form({student_id: {"participation": "present", "activity": "4",
                              "homework": "done"}}, token=token)

    for _ in range(3):                      # osvježavanje / dvoklik
        admin.post("/admin/sessions/bulk", data=data)

    stored = sessions_of(db, student_id)
    assert len(stored) == 1, "dvostruko slanje je napravilo duplikat"
    assert stored[0]["activity_rating"] == 4


def test_19b_a_different_lesson_on_the_same_day_is_a_different_class(admin, db):
    student_id = confirmed(db, "Sedmak", 7)
    token = csrf_from(entry_page(admin, 7))
    row = {student_id: {"participation": "present", "activity": "4"}}
    admin.post("/admin/sessions/bulk", data=form(row, token=token))
    admin.post("/admin/sessions/bulk",
               data=form(row, lesson=OTHER_LESSON, token=token))

    stored = sessions_of(db, student_id)
    assert len(stored) == 2
    assert {r["lesson_name"] for r in stored} == {LESSON, OTHER_LESSON}


def test_20_an_existing_class_can_be_corrected_without_opening_profiles(admin, db):
    student_id = confirmed(db, "Sedmak", 7)
    token = csrf_from(entry_page(admin, 7))
    admin.post("/admin/sessions/bulk",
               data=form({student_id: {"participation": "present",
                                       "activity": "2", "homework": "not_done",
                                       "comment": "prva verzija"}}, token=token))

    # Ista stranica s istom četvorkom UČITAVA ono što je već upisano.
    page = entry_page(admin, 7)
    assert b"prva verzija" in page.data
    assert ('id="a%d_2"' % student_id).encode() in page.data

    admin.post("/admin/sessions/bulk",
               data=form({student_id: {"participation": "present",
                                       "activity": "5", "homework": "done",
                                       "comment": "ispravljeno"}},
                         token=csrf_from(page)))

    stored = sessions_of(db, student_id)
    assert len(stored) == 1, "ispravka je napravila drugi red"
    assert stored[0]["activity_rating"] == 5
    assert stored[0]["homework_status"] == "done"
    assert stored[0]["comment"] == "ispravljeno"


def test_20b_a_present_student_can_be_corrected_to_absent(admin, db):
    student_id = confirmed(db, "Sedmak", 7)
    token = csrf_from(entry_page(admin, 7))
    admin.post("/admin/sessions/bulk",
               data=form({student_id: {"participation": "present",
                                       "activity": "4"}}, token=token))
    admin.post("/admin/sessions/bulk",
               data=form({student_id: {"participation": "absent"}}, token=token))

    stored = sessions_of(db, student_id)
    assert len(stored) == 1
    assert stored[0]["attendance"] == "absent"
    assert stored[0]["activity_rating"] is None


# ===========================================================================
# 21-25) IZVJEŠTAJNA KOMPATIBILNOST
# ===========================================================================
def _month_summary(db, student_id):
    return report_input.build_instruction_section(
        student_id, "2026-09", database=db)


def test_21_to_24_bulk_metrics_equal_individual_entry_metrics(admin, db):
    """SRŽ FAZE: dva puta, isti podaci → BAJT ZA BAJT isti mjesečni sažetak."""
    bulk = confirmed(db, "Preko Casa", 7)
    single = confirmed(db, "Preko Profila", 7)

    plan = [
        ("2026-09-01", "present", "4", "done", "dobar tempo"),
        ("2026-09-03", "present", "5", "not_done", None),
        ("2026-09-05", "absent", None, None, None),
        ("2026-09-08", "present", "3", "not_assigned", "ponoviti razlomke"),
    ]
    lessons = [LESSON, OTHER_LESSON, LESSON, OTHER_LESSON]

    token = csrf_from(entry_page(admin, 7))
    for (date, state, activity, homework, comment), lesson in zip(plan, lessons):
        fields = {"participation": state}
        if state == "present":
            fields["activity"] = activity
            fields["homework"] = homework
        if comment:
            fields["comment"] = comment
        admin.post("/admin/sessions/bulk",
                   data=form({bulk: fields}, date=date, lesson=lesson,
                             token=token))

        # ISTI podaci, STARIM putem (pojedinačni unos kroz sloj profila).
        record = student_sessions.validate_session(
            session_date=date,
            attendance=state,
            activity_rating=activity if state == "present" else None,
            homework_status=(homework if state == "present"
                             else class_entry.ABSENT_HOMEWORK),
            area_name=AREA, lesson_name=lesson, comment=comment, grade=7)
        db.insert_session(single, record)

    from_bulk = _month_summary(db, bulk)
    from_single = _month_summary(db, single)
    assert from_bulk == from_single, "brzi unos daje drugačije metrike"

    # I sami brojevi moraju biti tačni, ne samo jednaki.
    assert from_bulk["sessions_total"] == 4          # 22) prisustvo
    assert from_bulk["present_count"] == 3
    assert from_bulk["absent_count"] == 1
    assert from_bulk["activity"]["average"] == 4.0   # 24) prosjek (4+5+3)/3
    assert from_bulk["activity"]["rated_sessions"] == 3
    # 23) imenilac: done + not_done, bez `not_assigned` i bez izostanka.
    assert from_bulk["homework"]["assigned_count"] == 2
    assert from_bulk["homework"]["done_count"] == 1
    assert from_bulk["homework"]["not_assigned_count"] == 2


def test_23b_absent_rows_never_enter_the_homework_denominator(admin, db):
    student_id = confirmed(db, "Sedmak", 7)
    token = csrf_from(entry_page(admin, 7))
    for day in ("2026-09-01", "2026-09-02", "2026-09-03"):
        admin.post("/admin/sessions/bulk",
                   data=form({student_id: {"participation": "absent"}},
                             date=day, token=token))

    summary = _month_summary(db, student_id)
    assert summary["absent_count"] == 3
    assert summary["homework"]["assigned_count"] == 0, "izostanak je ušao u imenilac"
    assert summary["homework"]["not_assigned_count"] == 3
    assert summary["activity"]["average"] is None


def test_25_comments_are_optional_and_stay_out_of_the_model_contract(admin, db):
    from matbot import report_facts

    student_id = confirmed(db, "Sedmak", 7)
    token = csrf_from(entry_page(admin, 7))
    # Bez komentara — potpuno legitimno.
    admin.post("/admin/sessions/bulk",
               data=form({student_id: {"participation": "present",
                                       "activity": "4"}}, token=token))
    assert sessions_of(db, student_id)[0]["comment"] is None

    secret = "SINTETICKA BILJESKA KOJA NE SMIJE U MODEL"
    admin.post("/admin/sessions/bulk",
               data=form({student_id: {"participation": "present",
                                       "activity": "4", "comment": secret}},
                         date="2026-09-02", token=token))

    payload = report_input.build_report_input(student_id, "2026-09")
    facts = report_facts.build_ai_facts(payload)
    import json

    assert secret not in json.dumps(facts, ensure_ascii=False)


# ===========================================================================
# 26-30) NEPROMIJENJENO OKRUŽENJE
# ===========================================================================
def test_26_the_report_prompt_is_unchanged():
    from matbot import report_prompt

    assert report_prompt.REPORT_PROMPT_VERSION == "3d-2"


def test_27_the_schema_stays_at_version_four():
    from matbot import config

    assert reporting_schema.CURRENT_SCHEMA_VERSION == 4
    assert config.REPORTING_SCHEMA_VERSION == 4
    assert set(reporting_schema.MIGRATION_DESCRIPTIONS) == {2, 3, 4}
    assert not hasattr(reporting_schema, "SCHEMA_VERSION_V5")


def test_28_the_individual_per_student_workflow_still_works(admin, db):
    """Stari put NIJE uklonjen — ostaje za ispravke i izuzetke."""
    student_id = confirmed(db, "Sedmak", 7)
    profile = admin.get("/admin/students/%d" % student_id)
    assert b'name="area_name"' in profile.data

    admin.post("/admin/students/%d/sessions" % student_id,
               data={"csrf_token": csrf_from(profile),
                     "session_date": "2026-09-10", "attendance": "present",
                     "activity_rating": "5", "homework_status": "done",
                     "area_name": AREA, "lesson_name": LESSON,
                     "comment": ""})
    stored = sessions_of(db, student_id)
    assert len(stored) == 1 and stored[0]["activity_rating"] == 5


def test_29_historical_sessions_are_untouched_by_a_new_class(admin, db):
    student_id = confirmed(db, "Sedmak", 7)
    old = student_sessions.validate_session(
        session_date="2026-05-04", attendance="present", activity_rating=2,
        homework_status="not_done", area_name=AREA, lesson_name=LESSON,
        comment="lanjski cas", grade=7)
    db.insert_session(student_id, old)
    before = sessions_of(db, student_id)

    token = csrf_from(entry_page(admin, 7))
    admin.post("/admin/sessions/bulk",
               data=form({student_id: {"participation": "present",
                                       "activity": "5"}}, token=token))

    after = sessions_of(db, student_id)
    assert len(after) == 2
    assert after[0] == before[0], "raniji čas je promijenjen"


def test_30_no_model_call_is_involved(admin, db, flask_app):
    """Cijeli tok je determinističan: nula poziva modela."""
    fake = flask_app.config["MATBOT_LLM"]
    before = len(getattr(fake, "calls", []) or [])

    student_id = confirmed(db, "Sedmak", 7)
    token = csrf_from(entry_page(admin, 7))
    admin.post("/admin/sessions/bulk",
               data=form({student_id: {"participation": "present",
                                       "activity": "4"}}, token=token))
    admin.get("/admin/sessions/saved?session_date=%s&grade=7&area_name=%s"
              "&lesson_name=%s&present=1&absent=0" % (DATE, AREA, LESSON))

    assert len(getattr(fake, "calls", []) or []) == before
    source = (__import__("pathlib").Path("matbot") / "admin_sessions.py").read_text(
        encoding="utf-8")
    assert "llm" not in source.lower().replace("small", "")


# ===========================================================================
# POTVRDA POSLIJE ČUVANJA
# ===========================================================================
def test_saved_page_shows_the_class_summary_and_an_edit_link(admin, db):
    a = confirmed(db, "Prvi", 7)
    b = confirmed(db, "Drugi", 7)
    token = csrf_from(entry_page(admin, 7))
    answer = admin.post("/admin/sessions/bulk",
                        data=form({a: {"participation": "present", "activity": "4"},
                                   b: {"participation": "absent"}}, token=token))
    assert answer.status_code == 302

    page = admin.get(answer.headers["Location"])
    assert "Čas sačuvan".encode() in page.data
    assert DATE.encode() in page.data
    assert AREA.encode() in page.data and LESSON.encode() in page.data
    assert b">1<" in page.data          # 1 prisutan, 1 odsutan
    assert "Uredi čas".encode() in page.data
    # Nikad ime ni komentar na stranici potvrde.
    assert b"Prvi" not in page.data and b"Drugi" not in page.data


def test_logging_carries_no_names_or_comments(admin, db, caplog):
    import logging

    student_id = confirmed(db, "Tajno Ime", 7)
    token = csrf_from(entry_page(admin, 7))
    with caplog.at_level(logging.INFO):
        admin.post("/admin/sessions/bulk",
                   data=form({student_id: {"participation": "present",
                                           "activity": "4",
                                           "comment": "TAJNO ZAPAZANJE"}},
                             token=token))
    assert "admin_class_saved" in caplog.text
    assert "Tajno Ime" not in caplog.text
    assert "TAJNO ZAPAZANJE" not in caplog.text
    assert "@" not in caplog.text
