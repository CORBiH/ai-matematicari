"""Zatečeni časovi iz vremena između šeme v5 i v6 — most, ne migracija.

IZMJERENO STANJE PRODUKCIJE (samo čitanje, prije izdanja):

    student_sessions ukupno: 3
      · bez vremena (prije v5):        1
      · s vremenom, bez roditelja:     2   ← ova dva su JEDAN stvaran čas

    jedina pojava oblika časa:
      2026-09-01 · 10:00 · Skupovi i skupovne operacije
      · Pojam skupa, elementi skupa i označavanje · curriculum · 2 reda

Poslije v6 pregled „Svi časovi" čita ISKLJUČIVO `class_sessions`, pa bi ta dva
ispravna reda naprosto NESTALA s ekrana. Ovaj fajl brani most koji ih vraća:
administrator ih vidi, otvori, provjeri i IZRIČITO im dodijeli razred.

TRI TVRDNJE KOJE OVDJE MORAJU OSTATI TAČNE:

1. RAZRED UNOSI ČOVJEK. Zapis časa ne nosi razred grupe; `students.grade` je
   TEKUĆI razred i mijenja se promocijom. Nijedan drugi izvor (ime, Thinkific,
   MAT-BOT, kontrolni, sadržaj lekcije) nije dokaz o razredu na dan časa.
2. POVEZIVANJE DODAJE, NE PREPISUJE. Nastaje samo roditelj i veza ka njemu;
   nijedna činjenica o učeniku se ne dira — ni `updated_at`.
3. IZVJEŠTAJ SE NE MIJENJA. Mjesečne brojke prije i poslije povezivanja su
   identične, jer izvještajni sloj i dalje čita `student_sessions`.

PII: svi učenici su sintetički.
"""
import re

import pytest

from matbot import (report_facts, report_input, reporting_db, reporting_schema,
                    student_sessions)

from tests.test_thinkific_progress_import import build_v1, migrate

libsql = pytest.importorskip("libsql")

PASSWORD = "administratorska-lozinka-123"

# Tačno izmjerena pojava iz produkcije.
DATE = "2026-09-01"
TIME = "10:00"
AREA = "Skupovi i skupovne operacije"
LESSON = "Pojam skupa, elementi skupa i označavanje"
SOURCE = "curriculum"
GRADE = 6            # razred koji administrator IZRIČITO bira; nigdje zapisan

LIST_URL = "/admin/sessions"
ADOPT_URL = "/admin/sessions/legacy/adopt"

# Sve kolone `student_sessions` OSIM veze na čas. Nijedna se ne smije pomjeriti.
UNTOUCHED = ("id", "student_id", "session_date", "session_time", "attendance",
             "activity_rating", "homework_status", "area_name", "lesson_name",
             "topic_source", "comment", "created_at", "updated_at")


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


# --- gradnja IZMJERENOG zatečenog stanja -----------------------------------
def raw_session(db, student_id, *, date=DATE, time=TIME, area=AREA,
                lesson=LESSON, source=SOURCE, attendance="present",
                activity=4, homework="done", comment=None):
    """Red kakav je nastao PRIJE v6: ima vrijeme, nema roditelja.

    Piše se direktno, jer ga današnje rute više ne mogu proizvesti — `save_class`
    uvijek pravi roditelja. Ovo je jedini vjeran način da se reprodukuje ono što
    produkcija stvarno sadrži."""
    conn = db._connection()
    conn.execute(
        "INSERT INTO student_sessions (student_id, session_date, session_time, "
        " attendance, activity_rating, homework_status, area_name, "
        " lesson_name, topic_source, comment, class_session_id, "
        " created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, "
        " '2026-09-01 09:00:00', '2026-09-01 09:00:00')",
        (int(student_id), date, time, attendance, activity, homework, area,
         lesson, source, comment))
    conn.commit()
    return int(conn.execute("SELECT MAX(id) FROM student_sessions"
                            ).fetchall()[0][0])


def raw_timeless(db, student_id):
    """Red od prije v5: bez vremena i bez izvora teme. Most ga NE SMIJE dirati."""
    conn = db._connection()
    conn.execute(
        "INSERT INTO student_sessions (student_id, session_date, attendance, "
        " activity_rating, homework_status, area_name, lesson_name, comment, "
        " created_at, updated_at) "
        "VALUES (?, '2026-08-20', 'present', 3, 'done', ?, ?, NULL, "
        " '2026-08-20 09:00:00', '2026-08-20 09:00:00')",
        (int(student_id), AREA, LESSON))
    conn.commit()
    return int(conn.execute("SELECT MAX(id) FROM student_sessions"
                            ).fetchall()[0][0])


@pytest.fixture
def production_shape(db):
    """Tačno 3 reda: 1 bez vremena + 2 koja čine jednu zatečenu pojavu."""
    first = db.create_student("Amar H.", GRADE)
    second = db.create_student("Lejla K.", GRADE)
    ids = {
        "timeless": raw_timeless(db, first),
        "a": raw_session(db, first, activity=4, comment="dobar rad"),
        "b": raw_session(db, second, activity=5, homework="not_done"),
        "students": (first, second),
    }
    assert dump(db, "SELECT COUNT(*) FROM student_sessions")[0][0] == 3
    return ids


def dump(db, sql, params=()):
    return db._connection().execute(sql, params).fetchall()


def snapshot(db):
    """Sve kolone SVIH redova osim veze na čas — poređenje bajt po bajt."""
    return [tuple(row) for row in dump(
        db, "SELECT %s FROM student_sessions ORDER BY id" % ", ".join(UNTOUCHED))]


def links(db):
    return {int(r[0]): r[1] for r in dump(
        db, "SELECT id, class_session_id FROM student_sessions ORDER BY id")}


def occurrence_form(**overrides):
    fields = {"session_date": DATE, "session_time": TIME,
              "topic_source": SOURCE, "area_name": AREA, "lesson_name": LESSON}
    fields.update(overrides)
    return fields


def inspect_url(**overrides):
    from urllib.parse import urlencode

    return "/admin/sessions/legacy?" + urlencode(occurrence_form(**overrides))


def adopt(admin, *, grade=GRADE, csrf=True, **overrides):
    data = occurrence_form(**overrides)
    if grade is not None:
        data["grade"] = str(grade)
    if csrf:
        data["csrf_token"] = csrf_from(admin.get(inspect_url(**overrides)))
    return admin.post(ADOPT_URL, data=data)


def adopted_class_id(response):
    match = re.search(r"/admin/sessions/(\d+)",
                      response.headers.get("Location", ""))
    return int(match.group(1)) if match else None


# ===========================================================================
# 1-7) OTKRIVANJE: šta jeste, a šta NIJE zatečen čas
# ===========================================================================
def test_1_the_two_timed_rows_form_exactly_one_occurrence(db, production_shape):
    groups, total = db.fetch_unlinked_timed_groups()
    assert total == 1 and len(groups) == 1
    assert groups[0] == {"session_date": DATE, "session_time": TIME,
                         "topic_source": SOURCE, "area_name": AREA,
                         "lesson_name": LESSON, "row_count": 2}


def test_2_the_timeless_row_is_never_offered(db, production_shape):
    """Red bez vremena nema termin, pa nije čas koji se može identifikovati."""
    groups, _ = db.fetch_unlinked_timed_groups()
    assert all(g["session_date"] != "2026-08-20" for g in groups)
    rows = db.fetch_unlinked_timed_rows(**occurrence_form())
    assert production_shape["timeless"] not in {r["id"] for r in rows}


def test_3_rows_that_already_have_a_parent_are_never_offered(db,
                                                             production_shape):
    db.adopt_legacy_class(grade=GRADE, **occurrence_form())
    groups, total = db.fetch_unlinked_timed_groups()
    assert (groups, total) == ([], 0)


def test_4_two_lessons_at_the_same_time_are_two_occurrences(db,
                                                            production_shape):
    third = db.create_student("Emir S.", 7)
    raw_session(db, third, lesson="Prikaz na brojevnoj pravoj")
    groups, total = db.fetch_unlinked_timed_groups()
    assert total == 2
    assert {g["lesson_name"] for g in groups} == {
        LESSON, "Prikaz na brojevnoj pravoj"}


def test_5_a_null_topic_source_reads_as_curriculum(db, production_shape):
    """Zatečen red bez izvora teme je kurikularni — isto pravilo kao u v5."""
    third = db.create_student("Nejra B.", GRADE)
    raw_session(db, third, source=None, time="12:00")
    groups, _ = db.fetch_unlinked_timed_groups()
    noon = [g for g in groups if g["session_time"] == "12:00"][0]
    assert noon["topic_source"] == "curriculum"


def test_6_a_null_area_groups_with_an_empty_area(db, production_shape):
    third = db.create_student("Tarik M.", GRADE)
    fourth = db.create_student("Ajla D.", GRADE)
    raw_session(db, third, area=None, lesson="Uvodni čas", source="custom",
                time="17:30")
    raw_session(db, fourth, area="", lesson="Uvodni čas", source="custom",
                time="17:30")
    groups, _ = db.fetch_unlinked_timed_groups()
    custom = [g for g in groups if g["lesson_name"] == "Uvodni čas"]
    assert len(custom) == 1 and custom[0]["row_count"] == 2
    assert custom[0]["area_name"] is None


def test_7_the_occurrence_rows_carry_the_original_facts(db, production_shape):
    rows = db.fetch_unlinked_timed_rows(**occurrence_form())
    assert len(rows) == 2
    by_name = {r["display_name"]: r for r in rows}
    assert by_name["Amar H."]["activity_rating"] == 4
    assert by_name["Amar H."]["comment"] == "dobar rad"
    assert by_name["Lejla K."]["homework_status"] == "not_done"
    assert all(r["class_session_id"] is None for r in rows)


# ===========================================================================
# 8-12) SPISAK ČASOVA: zatečeni čas mora biti VIDLJIV
# ===========================================================================
def test_8_the_class_list_shows_the_legacy_section(admin, db, production_shape):
    body = admin.get(LIST_URL + "?month=2026-09").data.decode("utf-8")
    assert "Raniji časovi za povezivanje (1)" in body
    assert LESSON in body and "Poveži čas" in body


def test_9_the_section_disappears_when_there_is_nothing_to_adopt(admin, db,
                                                                 production_shape):
    db.adopt_legacy_class(grade=GRADE, **occurrence_form())
    body = admin.get(LIST_URL + "?month=2026-09").data.decode("utf-8")
    assert "Raniji časovi za povezivanje" not in body


def test_10_a_month_filter_cannot_hide_a_legacy_class(admin, db,
                                                      production_shape):
    """Zaturen čas se ne smije kriti još jednom — sekcija je izvan filtera."""
    body = admin.get(LIST_URL + "?month=2026-12").data.decode("utf-8")
    assert "Raniji časovi za povezivanje (1)" in body
    body = admin.get(LIST_URL + "?month=2026-09&grade=9").data.decode("utf-8")
    assert "Raniji časovi za povezivanje (1)" in body


def test_11_the_action_links_to_the_inspection_page(admin, db,
                                                    production_shape):
    body = admin.get(LIST_URL).data.decode("utf-8")
    assert "/admin/sessions/legacy?" in body
    assert ADOPT_URL not in body, "spisak ne smije nuditi direktan upis"


def test_12_an_empty_database_renders_the_list_without_the_section(admin, db):
    body = admin.get(LIST_URL).data.decode("utf-8")
    assert "Raniji časovi za povezivanje" not in body


# ===========================================================================
# 13-18) PREGLED PRIJE POVEZIVANJA
# ===========================================================================
def test_13_inspection_shows_the_students_and_their_facts(admin, db,
                                                          production_shape):
    body = admin.get(inspect_url()).data.decode("utf-8")
    for expected in ("Amar H.", "Lejla K.", DATE, TIME, LESSON, AREA,
                     "dobar rad"):
        assert expected in body, expected


def test_14_no_grade_is_preselected(admin, db, production_shape):
    """Preduzabran razred bi pogađanje pretvorio u „samo potvrdi"."""
    body = admin.get(inspect_url()).data.decode("utf-8")
    assert '<option value="" selected>Odaberi razred</option>' in body
    assert not re.search(r'<option value="[6789]"[^>]*selected', body)


def test_15_inspection_never_mutates_anything(admin, db, production_shape):
    before, before_links = snapshot(db), links(db)
    for _ in range(3):
        assert admin.get(inspect_url()).status_code == 200
    assert dump(db, "SELECT COUNT(*) FROM class_sessions")[0][0] == 0
    assert snapshot(db) == before and links(db) == before_links


def test_16_a_vanished_occurrence_redirects_with_a_plain_message(admin, db,
                                                                 production_shape):
    answer = admin.get(inspect_url(lesson_name="Nepostojeća lekcija"))
    assert answer.status_code == 302
    assert "/admin/sessions" in answer.headers["Location"]
    body = admin.get(answer.headers["Location"]).data.decode("utf-8")
    assert "više ne postoji" in body


def test_17_no_internal_code_reaches_the_screen(admin, db, production_shape):
    pages = [admin.get(inspect_url()).data.decode("utf-8"),
             admin.get(LIST_URL).data.decode("utf-8")]
    answer = admin.get(inspect_url(session_time="23:59"))
    pages.append(admin.get(answer.headers["Location"]).data.decode("utf-8"))
    for body in pages:
        for code in ("legacy_group_empty", "legacy_adoption_raced",
                     "class_occurrence_conflict", "legacy_list_failed",
                     "class_entity_unavailable", "class_session_id"):
            assert code not in body, code


def test_18_inspection_requires_an_administrator(client, db, production_shape):
    assert client.get(inspect_url()).status_code in (302, 401, 403, 404)


# ===========================================================================
# 19-25) POVEZIVANJE: dodaje roditelja, ne dira ništa drugo
# ===========================================================================
def test_19_adoption_creates_exactly_one_parent_with_the_chosen_grade(
        admin, db, production_shape):
    answer = adopt(admin, grade=8)
    assert answer.status_code == 302
    class_id = adopted_class_id(answer)
    rows = dump(db, "SELECT id, session_date, session_time, grade, "
                    "topic_source, area_name, lesson_name FROM class_sessions")
    assert len(rows) == 1
    assert tuple(rows[0]) == (class_id, DATE, TIME, 8, SOURCE, AREA, LESSON)


def test_20_exactly_the_occurrence_rows_are_linked(admin, db,
                                                   production_shape):
    class_id = adopted_class_id(adopt(admin))
    assert links(db) == {production_shape["timeless"]: None,
                         production_shape["a"]: class_id,
                         production_shape["b"]: class_id}


def test_21_every_other_column_is_byte_identical(admin, db, production_shape):
    """DODAJE, NE PREPISUJE — ni `updated_at` se ne pomjera."""
    before = snapshot(db)
    adopt(admin)
    assert snapshot(db) == before


def test_22_an_unrelated_occurrence_is_left_alone(admin, db,
                                                  production_shape):
    third = db.create_student("Haris P.", 7)
    other = raw_session(db, third, time="14:00")
    adopt(admin)
    assert links(db)[other] is None
    groups, total = db.fetch_unlinked_timed_groups()
    assert total == 1 and groups[0]["session_time"] == "14:00"


def test_23_the_adopted_class_becomes_a_normal_class(admin, db,
                                                     production_shape):
    class_id = adopted_class_id(adopt(admin))
    listing = admin.get(LIST_URL + "?month=2026-09").data.decode("utf-8")
    assert "Ukupno časova: <b>1</b>" in listing
    assert "Raniji časovi za povezivanje" not in listing

    detail = admin.get("/admin/sessions/%d" % class_id)
    assert detail.status_code == 200
    body = detail.data.decode("utf-8")
    assert "Amar H." in body and "Lejla K." in body
    assert "%d. razred" % GRADE in body

    editable = admin.get("/admin/sessions/new?class_id=%d" % class_id)
    assert editable.status_code == 200
    assert admin.get("/admin/sessions/%d/delete" % class_id).status_code == 200


def test_24_the_historical_grade_survives_a_promotion(admin, db,
                                                      production_shape):
    """Razred časa je ISTORIJSKA činjenica, a `students.grade` je tvrdnja o danas."""
    class_id = adopted_class_id(adopt(admin, grade=7))
    for student_id in production_shape["students"]:
        db._connection().execute("UPDATE students SET grade = 9 WHERE id = ?",
                                 (student_id,))
    db._connection().commit()
    assert db.fetch_class(class_id)["grade"] == 7
    assert "7. razred" in admin.get(
        "/admin/sessions/%d" % class_id).data.decode("utf-8")


def test_25_the_confirmation_says_nothing_was_changed(admin, db,
                                                      production_shape):
    class_id = adopted_class_id(adopt(admin))
    body = admin.get("/admin/sessions/%d?adopted=1" % class_id).data.decode("utf-8")
    assert "povezan" in body and "nisu mijenjani" in body


# ===========================================================================
# 26-33) SIGURNOST I PADANJE ZATVORENO
# ===========================================================================
def test_26_adoption_requires_csrf(admin, db, production_shape):
    before = links(db)
    assert adopt(admin, csrf=False).status_code == 400
    assert links(db) == before
    assert dump(db, "SELECT COUNT(*) FROM class_sessions")[0][0] == 0


def test_27_adoption_is_post_only(admin, db, production_shape):
    assert admin.get(ADOPT_URL).status_code == 405


def test_28_adoption_requires_an_administrator(client, db, production_shape):
    answer = client.post(ADOPT_URL, data=occurrence_form(grade=str(GRADE)))
    assert answer.status_code in (302, 400, 401, 403, 404)
    assert dump(db, "SELECT COUNT(*) FROM class_sessions")[0][0] == 0


def test_29_a_missing_grade_changes_nothing(admin, db, production_shape):
    before = snapshot(db), links(db)
    answer = adopt(admin, grade=None)
    assert answer.status_code == 302
    body = admin.get(answer.headers["Location"]).data.decode("utf-8")
    assert "Izaberite razred" in body
    assert (snapshot(db), links(db)) == before
    assert dump(db, "SELECT COUNT(*) FROM class_sessions")[0][0] == 0


def test_30_an_invalid_grade_is_refused(admin, db, production_shape):
    for bad in ("5", "10", "sedmi", "-7", "7.5"):
        answer = adopt(admin, grade=bad)
        assert answer.status_code == 302
        assert dump(db, "SELECT COUNT(*) FROM class_sessions")[0][0] == 0, bad


def test_31_a_double_submit_creates_only_one_class(admin, db,
                                                   production_shape):
    first = adopt(admin)
    class_id = adopted_class_id(first)
    second = admin.post(ADOPT_URL, data=dict(
        occurrence_form(), grade=str(GRADE),
        csrf_token=csrf_from(admin.get("/admin/reports"))))
    assert dump(db, "SELECT COUNT(*) FROM class_sessions")[0][0] == 1
    assert second.status_code == 302
    assert second.headers["Location"].endswith("/admin/sessions/%d" % class_id)


def test_32_an_occupied_termin_fails_closed(admin, db, production_shape):
    """Povezivanje ne smije spojiti zatečene redove s TUĐIM stvarnim časom."""
    db.save_class(class_id=None, session_date=DATE, session_time=TIME,
                  grade=GRADE, topic_source=SOURCE, area_name=AREA,
                  lesson_name=LESSON,
                  records=[(db.create_student("Osmak S.", GRADE), {
                      "session_date": DATE, "session_time": TIME,
                      "attendance": "present", "activity_rating": 3,
                      "homework_status": "done", "area_name": AREA,
                      "lesson_name": LESSON, "topic_source": SOURCE,
                      "comment": None})])
    before = links(db)
    answer = adopt(admin, grade=GRADE)
    assert answer.status_code == 302
    body = admin.get(answer.headers["Location"]).data.decode("utf-8")
    assert "već postoji čas" in body
    assert links(db) == before, "zatečeni redovi su ipak pomjereni"
    assert dump(db, "SELECT COUNT(*) FROM class_sessions")[0][0] == 1


def test_33_the_client_cannot_choose_which_rows_are_adopted(admin, db,
                                                            production_shape):
    """`id` reda se ne prima iz formulara — redovi se nalaze serverski."""
    third = db.create_student("Podmetnuti U.", 7)
    foreign = raw_session(db, third, time="14:00", lesson="Tuđa tema")
    data = dict(occurrence_form(), grade=str(GRADE),
                csrf_token=csrf_from(admin.get(inspect_url())))
    data["session_id"] = str(foreign)
    data["s%d_participation" % third] = "present"
    data["class_session_id"] = "999"
    admin.post(ADOPT_URL, data=data)
    assert links(db)[foreign] is None
    assert dump(db, "SELECT COUNT(*) FROM class_sessions")[0][0] == 1


class _FailingOnLink:
    """Konekcija koja pukne TAČNO na povezivanju djece.

    Konekcija je C objekat i atributi su joj samo za čitanje, pa se omotava."""

    def __init__(self, inner):
        self._inner = inner

    def execute(self, sql, *args, **kwargs):
        if "SET class_session_id" in sql:
            raise RuntimeError("simulirani pad")
        return self._inner.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_34_a_failing_adoption_leaves_no_orphan_parent(db, production_shape,
                                                       monkeypatch):
    before = snapshot(db), links(db)
    wrapped = _FailingOnLink(db._connection())
    monkeypatch.setattr(db, "_connection", lambda: wrapped)
    with pytest.raises(reporting_db.ReportingUnavailable):
        db.adopt_legacy_class(grade=GRADE, **occurrence_form())
    monkeypatch.undo()

    assert dump(db, "SELECT COUNT(*) FROM class_sessions")[0][0] == 0, \
        "roditelj bez djece je ostao"
    assert (snapshot(db), links(db)) == before


def test_35_a_partially_linked_group_aborts_the_whole_adoption(db,
                                                               production_shape):
    """Ako neko usput usvoji jedan red, ne pravi se pola časa."""
    class Racer:
        def __init__(self, inner, row_id):
            self._inner, self._row_id, self._armed = inner, row_id, True

        def execute(self, sql, *args, **kwargs):
            if self._armed and "SET class_session_id" in sql:
                self._armed = False
                self._inner.execute(
                    "UPDATE student_sessions SET class_session_id = 4242 "
                    "WHERE id = ?", (self._row_id,))
            return self._inner.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    racer = Racer(db._connection(), production_shape["b"])
    original = db._connection
    db._connection = lambda: racer
    try:
        with pytest.raises(reporting_db.ReportingUnavailable) as caught:
            db.adopt_legacy_class(grade=GRADE, **occurrence_form())
    finally:
        db._connection = original
    assert caught.value.code == "legacy_adoption_raced"
    assert dump(db, "SELECT COUNT(*) FROM class_sessions")[0][0] == 0


# ===========================================================================
# 36-38) IZVJEŠTAJ SE NE MIJENJA
# ===========================================================================
def month_facts(db, student_id):
    payload = report_input.build_report_input(student_id, "2026-09",
                                              database=db)
    return report_facts.build_ai_facts(payload)


def test_36_monthly_instruction_facts_are_identical_after_adoption(
        admin, db, production_shape):
    first = production_shape["students"][0]
    before = report_input.build_instruction_section(first, "2026-09",
                                                    database=db)
    adopt(admin)
    after = report_input.build_instruction_section(first, "2026-09",
                                                   database=db)
    assert after == before


def test_37_ai_facts_are_identical_after_adoption(admin, db,
                                                  production_shape):
    first = production_shape["students"][0]
    before = month_facts(db, first)
    adopt(admin)
    assert month_facts(db, first) == before


def test_38_the_class_summary_matches_the_pre_adoption_rows(admin, db,
                                                            production_shape):
    rows = db.fetch_unlinked_timed_rows(**occurrence_form())
    before = student_sessions.build_monthly_summary(rows)
    class_id = adopted_class_id(adopt(admin))
    after = student_sessions.build_monthly_summary(
        db.fetch_class_students(class_id))
    assert after == before
    assert before["present_count"] == 2
