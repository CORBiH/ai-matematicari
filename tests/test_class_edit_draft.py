"""Izmjena postojećeg časa: NACRT instruktora preživljava GET obrte.

DVA ŽIVA DEFEKTA KOJA OVAJ FAJL ČUVA, oba izmjerena kroz stvarne forme:

1. NACRT SE GUBIO NA SVAKOM OBRTU. Kad je `class_id` prisutan, stranica je
   zajednička polja punila IZ BAZE na SVAKI GET — a svaki `onchange` na toj
   stranici je jedan GET. Izmjereno: instruktor traži 15:00 i „Ručni unos", a
   stranica vrati 10:00 i „Iz nastavnog plana". Pet od šest izmjena je nestajalo
   (šesta je „preživjela" samo zato što je slučajno bila jednaka bazi). Time je
   izmjena postojećeg časa bila nemoguća iako je `save_class` premještanje
   odavno podržavao.

2. DIJETE PROMOVISANOG UČENIKA SE TIHO BRISALO. Spisak za izmjenu se gradio
   isključivo iz POTVRĐENOG TEKUĆEG razreda, pa učenik koji je u međuvremenu
   prešao u naredni razred nije bio prikazan; kako `save_class` briše djecu koja
   nisu poslana, prvo sljedeće čuvanje mu je uklonilo red. Izmjereno: 2 djeteta
   prije, promovisani nestao poslije. To je gubitak ISTORIJSKOG DOKAZA o času na
   kojem je učenik stvarno bio.

MODEL STANJA: „sačuvano" i „ono što instruktor upravo mijenja" su dvije različite
stvari. Čas se uvijek čita iz baze (postoji li, koja su mu djeca), ali njegove
vrijednosti pune formu SAMO pri prvom otvaranju. Marker `edit_draft` razlikuje
prvo otvaranje od obrta — eksplicitno, jer je „polje je prazno" legitimno
međustanje nacrta, a ne znak da treba čitati bazu.

NIJEDAN GET NE PIŠE. Sve mijenja tek `POST /admin/sessions/bulk`.

PII: svi učenici su sintetički.
"""
import json
import re

import pytest

from matbot import (report_facts, report_input, reporting_db, reporting_schema,
                    student_sessions)

from tests.test_thinkific_progress_import import build_v1, migrate

libsql = pytest.importorskip("libsql")

PASSWORD = "administratorska-lozinka-123"

NEW = "/admin/sessions/new"
GRADE = 6
DATE = "2026-09-01"
TIME = "10:00"
AREA = "Skupovi i skupovne operacije"
LESSON = "Pojam skupa, elementi skupa i označavanje"
CUSTOM_TOPIC = "Uvodni čas"

CURRICULUM = student_sessions.TOPIC_CURRICULUM
CUSTOM = student_sessions.TOPIC_CUSTOM


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


class Page:
    """Iscrtana stranica; serijalizuje se kao što bi je pregledač poslao."""

    def __init__(self, html):
        self.html = html

    def get_form(self):
        block = self.html.split("</form>", 1)[0]
        fields = {}
        for chunk in re.findall(r"<input[^>]*>", block):
            name = re.search(r'name="([^"]+)"', chunk)
            value = re.search(r'value="([^"]*)"', chunk)
            kind = re.search(r'type="([^"]+)"', chunk)
            if not name:
                continue
            if kind and kind.group(1) == "radio":
                if "checked" in chunk and value:
                    fields[name.group(1)] = value.group(1)
                continue
            fields[name.group(1)] = value.group(1) if value else ""
        for select in re.findall(r"<select[^>]*>.*?</select>", block, re.S):
            name = re.search(r'name="([^"]+)"', select)
            picked = re.search(r'<option value="([^"]*)"[^>]*selected', select)
            if name:
                fields[name.group(1)] = picked.group(1) if picked else ""
        return fields

    def post_hidden(self):
        if "</form>" not in self.html:
            return {}
        tail = self.html.split("</form>", 1)[1].replace("\r", " ")
        tail = tail.replace("\n", " ")
        return dict(re.findall(
            r'<input type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"',
            tail))

    @property
    def mode(self):
        return self.get_form().get("topic_mode")

    @property
    def roster_count(self):
        found = re.search(r"<h3[^>]*>Učenici \((\d+)\)</h3>", self.html)
        return int(found.group(1)) if found else 0

    def student_ids(self):
        return {int(x) for x in re.findall(r'data-student="(\d+)"', self.html)}

    def checked(self, student_id, group):
        """Označeno stanje jednog reda učenika: p/a/h prefiks."""
        for chunk in re.findall(
                r'<input[^>]*id="%s%d_[^"]*"[^>]*>' % (group, student_id),
                self.html):
            if "checked" in chunk:
                return re.search(r'value="([^"]*)"', chunk).group(1)
        return None

    def comment_of(self, student_id):
        found = re.search(
            r'name="s%d_comment"[^>]*value="([^"]*)"' % student_id, self.html)
        return found.group(1) if found else None


def open_page(admin, **fields):
    from urllib.parse import urlencode

    url = NEW + ("?" + urlencode(fields) if fields else "")
    response = admin.get(url)
    assert response.status_code == 200, response.status_code
    return Page(response.data.decode("utf-8"))


def act(admin, page, **changes):
    """Jedan `this.form.submit()` — kao pregledač, s markerom nacrta."""
    fields = page.get_form()
    fields.update(changes)
    return open_page(admin, **{k: v for k, v in fields.items()
                               if v is not None})


def submit(admin, page, rows):
    fields = dict(page.post_hidden())
    fields["csrf_token"] = csrf_from(admin.get("/admin/reports"))
    for student_id, values in rows.items():
        for key, value in values.items():
            fields["s%d_%s" % (student_id, key)] = value
    return admin.post("/admin/sessions/bulk", data=fields)


def class_id_of(response):
    found = re.search(r"/admin/sessions/(\d+)",
                      response.headers.get("Location", ""))
    return int(found.group(1)) if found else None


@pytest.fixture
def students(db):
    return [db.create_student(name, GRADE)
            for name in ("Amar H.", "Lejla K.", "Emir S.")]


@pytest.fixture
def existing(admin, db, students):
    """Sačuvan planski čas s mješovitim stanjima učenika."""
    page = act(admin, act(admin, open_page(admin), grade=str(GRADE)),
               session_date=DATE, session_time=TIME, area_name=AREA)
    page = act(admin, page, lesson_name=LESSON)
    answer = submit(admin, page, {
        students[0]: {"participation": "present", "activity": "4",
                      "homework": "done", "comment": "dobar rad"},
        students[1]: {"participation": "absent"}})
    class_id = class_id_of(answer)
    assert class_id is not None
    return class_id


def class_state(db, class_id):
    row = db.fetch_class(class_id)
    if row is None:
        return None
    return (row["session_date"], row["session_time"], row["grade"],
            row["topic_source"], row["area_name"], row["lesson_name"])


def rows_snapshot(db):
    return [tuple(r) for r in db._connection().execute(
        "SELECT id, student_id, session_date, session_time, attendance, "
        " activity_rating, homework_status, area_name, lesson_name, "
        " topic_source, comment, class_session_id, created_at, updated_at "
        "FROM student_sessions ORDER BY id").fetchall()]


def classes_snapshot(db):
    return [tuple(r) for r in db._connection().execute(
        "SELECT id, session_date, session_time, grade, topic_source, "
        " area_name, lesson_name FROM class_sessions ORDER BY id").fetchall()]


# ===========================================================================
# 1) PRVO OTVARANJE UČITAVA BAZU
# ===========================================================================
def test_1_initial_edit_get_loads_persisted_state(admin, db, existing,
                                                  students):
    page = open_page(admin, class_id=str(existing))
    fields = page.get_form()
    assert fields["session_date"] == DATE
    assert fields["session_time"] == TIME
    assert fields["grade"] == str(GRADE)
    assert fields["topic_mode"] == CURRICULUM
    assert fields["area_name"] == AREA
    assert fields["lesson_name"] == LESSON
    assert "Uredi čas" in page.html
    assert fields.get("edit_draft") == "1", "marker nacrta nije iscrtan"


# ===========================================================================
# 2-9) NACRT PREŽIVLJAVA OBRTE
# ===========================================================================
def test_2_a_time_edit_survives_round_trips(admin, db, existing):
    page = act(admin, open_page(admin, class_id=str(existing)),
               session_time="15:00")
    assert page.get_form()["session_time"] == "15:00"
    # i preživi JOŠ jedan obrt izazvan nečim drugim
    page = act(admin, page, area_name=AREA)
    assert page.get_form()["session_time"] == "15:00"
    assert class_state(db, existing)[1] == TIME, "baza je promijenjena"


def test_3_a_date_edit_survives_round_trips(admin, db, existing):
    page = act(admin, open_page(admin, class_id=str(existing)),
               session_date="2026-09-20")
    page = act(admin, page, session_time="15:00")
    assert page.get_form()["session_date"] == "2026-09-20"
    assert class_state(db, existing)[0] == DATE


def test_4_a_grade_edit_survives_round_trips(admin, db, existing):
    db.create_student("Sedmak Potvrđen", 7)
    page = act(admin, open_page(admin, class_id=str(existing)), grade="7")
    assert page.get_form()["grade"] == "7"
    page = act(admin, page, session_time="15:00")
    assert page.get_form()["grade"] == "7"
    assert class_state(db, existing)[2] == GRADE, "istorijski razred promijenjen"


def test_5_curriculum_to_custom_survives(admin, db, existing):
    page = act(admin, open_page(admin, class_id=str(existing)),
               topic_mode=CUSTOM)
    assert page.mode == CUSTOM
    assert 'id="custom_lesson"' in page.html
    # Planska lekcija NE curi u ručnu temu.
    assert page.get_form()["lesson_name"] == ""
    assert LESSON not in page.html
    page = act(admin, page, session_time="15:00")
    assert page.mode == CUSTOM
    assert class_state(db, existing)[3] == CURRICULUM


def test_6_custom_text_survives(admin, db, existing):
    page = act(admin, open_page(admin, class_id=str(existing)),
               topic_mode=CUSTOM)
    page = act(admin, page, lesson_name=CUSTOM_TOPIC)
    assert page.get_form()["lesson_name"] == CUSTOM_TOPIC
    page = act(admin, page, session_date="2026-09-20")
    assert page.get_form()["lesson_name"] == CUSTOM_TOPIC
    assert page.mode == CUSTOM
    assert class_state(db, existing)[5] == LESSON


def test_7_custom_to_curriculum_starts_clean(admin, db, existing):
    page = act(admin, open_page(admin, class_id=str(existing)),
               topic_mode=CUSTOM)
    page = act(admin, page, lesson_name="Moja tema", area_name="Moja oblast")
    page = act(admin, page, topic_mode=CURRICULUM)
    fields = page.get_form()
    assert fields["lesson_name"] == "" and fields["area_name"] == ""
    assert "Moja tema" not in page.html
    assert page.post_hidden()["lesson_name"] == ""


def test_8_area_selection_survives(admin, db, existing):
    page = act(admin, open_page(admin, class_id=str(existing)),
               topic_mode=CUSTOM)
    page = act(admin, page, topic_mode=CURRICULUM)
    page = act(admin, page, area_name=AREA)
    assert page.get_form()["area_name"] == AREA
    page = act(admin, page, session_time="15:00")
    assert page.get_form()["area_name"] == AREA


def test_9_lesson_selection_survives(admin, db, existing):
    page = act(admin, open_page(admin, class_id=str(existing)),
               session_time="15:00")
    page = act(admin, page, lesson_name=LESSON)
    assert page.get_form()["lesson_name"] == LESSON
    page = act(admin, page, session_date="2026-09-20")
    assert page.get_form()["lesson_name"] == LESSON


def test_9b_the_full_measured_edit_sequence_survives(admin, db, existing):
    """Tačna redoslijed koraka iz prijave — nijedan se ne smije izgubiti."""
    page = open_page(admin, class_id=str(existing))
    page = act(admin, page, session_time="15:00")
    page = act(admin, page, topic_mode=CUSTOM)
    page = act(admin, page, lesson_name=CUSTOM_TOPIC)
    page = act(admin, page, session_date="2026-09-20")
    fields = page.get_form()
    assert (fields["session_time"], fields["session_date"],
            fields["topic_mode"], fields["lesson_name"]) == (
                "15:00", "2026-09-20", CUSTOM, CUSTOM_TOPIC)
    assert class_state(db, existing) == (DATE, TIME, GRADE, CURRICULUM,
                                         AREA, LESSON)


# ===========================================================================
# 10-12) SPISAK I ZATEČENA DJECA
# ===========================================================================
def test_10_the_roster_stays_visible_through_the_whole_edit(admin, db,
                                                            existing):
    page = open_page(admin, class_id=str(existing))
    assert page.roster_count == 3
    for change in ({"session_time": "15:00"}, {"topic_mode": CUSTOM},
                   {"lesson_name": CUSTOM_TOPIC},
                   {"session_date": "2026-09-20"},
                   {"topic_mode": CURRICULUM}):
        page = act(admin, page, **change)
        assert page.roster_count >= 3, change
        assert "Unesite vrijeme i temu časa" not in page.html, change


def test_11_existing_linked_children_stay_visible(admin, db, existing,
                                                  students):
    page = open_page(admin, class_id=str(existing))
    assert {students[0], students[1]} <= page.student_ids()
    assert page.checked(students[0], "p") == "present"
    assert page.checked(students[1], "p") == "absent"


def test_12_a_promoted_students_child_is_never_lost(admin, db, existing,
                                                    students):
    """ŽIVI DEFEKT: prvo čuvanje izmjene brisalo je red promovisanog učenika."""
    db._connection().execute("UPDATE students SET grade = 7 WHERE id = ?",
                             (students[1],))
    db._connection().commit()

    page = open_page(admin, class_id=str(existing))
    assert students[1] in page.student_ids(), "istorijski učesnik je nestao"
    assert "upisan ranije" in page.html, "prisustvo nije objašnjeno"
    assert page.checked(students[1], "p") == "absent"

    before = {int(r[0]) for r in db._connection().execute(
        "SELECT student_id FROM student_sessions WHERE class_session_id = ?",
        (existing,)).fetchall()}
    answer = submit(admin, page, {
        students[0]: {"participation": "present", "activity": "4",
                      "homework": "done"},
        students[1]: {"participation": "absent"}})
    assert class_id_of(answer) == existing
    after = {int(r[0]) for r in db._connection().execute(
        "SELECT student_id FROM student_sessions WHERE class_session_id = ?",
        (existing,)).fetchall()}
    assert students[1] in after, "istorijski dokaz je obrisan"
    assert after == before


def test_12b_a_promoted_participant_can_be_removed_explicitly(admin, db,
                                                              existing,
                                                              students):
    """Ostaje mogućnost IZRIČITOG uklanjanja — brani se samo TIHI gubitak."""
    db._connection().execute("UPDATE students SET grade = 7 WHERE id = ?",
                             (students[1],))
    db._connection().commit()
    page = open_page(admin, class_id=str(existing))
    submit(admin, page, {
        students[0]: {"participation": "present", "activity": "4",
                      "homework": "done"},
        students[1]: {"participation": "not_scheduled"}})
    remaining = {int(r[0]) for r in db._connection().execute(
        "SELECT student_id FROM student_sessions WHERE class_session_id = ?",
        (existing,)).fetchall()}
    assert students[1] not in remaining


# ===========================================================================
# 13-15) STANJA UČENIKA
# ===========================================================================
def test_13_saved_activity_survives_a_metadata_round_trip(admin, db,
                                                          existing, students):
    page = act(admin, open_page(admin, class_id=str(existing)),
               topic_mode=CUSTOM)
    assert page.checked(students[0], "a") == "4"


def test_14_saved_homework_survives_a_metadata_round_trip(admin, db,
                                                          existing, students):
    page = act(admin, open_page(admin, class_id=str(existing)),
               topic_mode=CUSTOM)
    assert page.checked(students[0], "h") == "done"


def test_15_saved_comment_survives_a_metadata_round_trip(admin, db,
                                                         existing, students):
    page = act(admin, open_page(admin, class_id=str(existing)),
               topic_mode=CUSTOM)
    page = act(admin, page, lesson_name=CUSTOM_TOPIC)
    assert page.comment_of(students[0]) == "dobar rad"


def test_15b_fields_that_do_not_reload_keep_unsaved_student_edits(admin, db,
                                                                  existing):
    """Datum, vrijeme i ručna tema NE osvježavaju stranicu.

    Zato nesačuvane oznake učenika preživljavaju njihovu izmjenu — one žive samo
    u pregledaču. Polja koja MORAJU do servera (razred, oblast, lekcija) i dalje
    osvježavaju stranicu, i to je namjerno."""
    page = act(admin, open_page(admin, class_id=str(existing)),
               topic_mode=CUSTOM)
    for element in ('id="session_date"', 'id="session_time"',
                    'id="custom_lesson"', 'id="custom_area"'):
        chunk = re.search(re.escape(element) + r"[^>]*>", page.html).group(0)
        assert "this.form.submit()" not in chunk, element
        assert "syncShared(this)" in chunk, element
    for element in ('id="grade"',):
        chunk = re.search(re.escape(element) + r"[^>]*>", page.html).group(0)
        assert "this.form.submit()" in chunk, element


# ===========================================================================
# 16) DVIJE FORME, JEDNA ISTINA
# ===========================================================================
def test_16_hidden_post_mirrors_equal_the_visible_draft(admin, db, existing):
    page = open_page(admin, class_id=str(existing))
    steps = ({"session_time": "15:00"}, {"session_date": "2026-09-20"},
             {"topic_mode": CUSTOM}, {"lesson_name": CUSTOM_TOPIC},
             {"area_name": "Moja oblast"}, {"topic_mode": CURRICULUM},
             {"area_name": AREA}, {"lesson_name": LESSON})
    for change in steps:
        page = act(admin, page, **change)
        visible = page.get_form()
        hidden = page.post_hidden()
        for field in ("session_date", "session_time", "grade", "topic_mode",
                      "area_name", "lesson_name"):
            assert hidden.get(field, "") == visible.get(field, ""), (
                change, field, hidden.get(field), visible.get(field))
        assert hidden.get("class_id") == str(existing)


# ===========================================================================
# 17) NIJEDAN GET NE PIŠE
# ===========================================================================
def test_17_no_get_round_trip_writes_anything(admin, db, existing, students):
    before_rows, before_classes = rows_snapshot(db), classes_snapshot(db)
    page = open_page(admin, class_id=str(existing))
    for change in ({"session_date": "2026-09-20"}, {"session_time": "15:00"},
                   {"grade": "7"}, {"grade": str(GRADE)},
                   {"topic_mode": CUSTOM}, {"lesson_name": CUSTOM_TOPIC},
                   {"area_name": "Moja oblast"}, {"topic_mode": CURRICULUM},
                   {"area_name": AREA}, {"lesson_name": LESSON}):
        page = act(admin, page, **change)
        assert rows_snapshot(db) == before_rows, change
        assert classes_snapshot(db) == before_classes, change
    assert rows_snapshot(db) == before_rows
    assert classes_snapshot(db) == before_classes


# ===========================================================================
# 18-20) ČUVANJE IZMJENE
# ===========================================================================
def test_18_saving_an_edit_mutates_the_same_class(admin, db, existing,
                                                  students):
    page = open_page(admin, class_id=str(existing))
    page = act(admin, page, session_time="15:00")
    page = act(admin, page, topic_mode=CUSTOM)
    page = act(admin, page, lesson_name=CUSTOM_TOPIC)
    answer = submit(admin, page, {
        students[0]: {"participation": "present", "activity": "2",
                      "homework": "not_done", "comment": "slabije"},
        students[1]: {"participation": "present", "activity": "5",
                      "homework": "done"},
        students[2]: {"participation": "absent"}})
    assert class_id_of(answer) == existing, "napravljen je novi čas"

    assert class_state(db, existing) == (DATE, "15:00", GRADE, CUSTOM,
                                         None, CUSTOM_TOPIC)
    assert len(classes_snapshot(db)) == 1, "dvojnik časa"
    rows = {r["student_id"]: r for r in db.fetch_class_students(existing)}
    assert set(rows) == set(students)
    assert rows[students[0]]["activity_rating"] == 2
    assert rows[students[0]]["comment"] == "slabije"
    assert rows[students[2]]["attendance"] == "absent"
    # Kopije u redovima učenika prate roditelja (v6 ugovor).
    assert all(r["session_time"] == "15:00" and r["topic_source"] == CUSTOM
               for r in rows.values())

    detail = admin.get("/admin/sessions/%d" % existing).data.decode("utf-8")
    assert CUSTOM_TOPIC in detail and "15:00" in detail
    listing = admin.get("/admin/sessions?month=2026-09").data.decode("utf-8")
    assert CUSTOM_TOPIC in listing and "Ukupno časova: <b>1</b>" in listing


def test_19_a_date_edit_moves_the_same_class(admin, db, existing, students):
    page = act(admin, open_page(admin, class_id=str(existing)),
               session_date="2026-10-05")
    answer = submit(admin, page, {
        students[0]: {"participation": "present", "activity": "4",
                      "homework": "done"}})
    assert class_id_of(answer) == existing
    assert class_state(db, existing)[0] == "2026-10-05"
    assert len(classes_snapshot(db)) == 1


GRADE7_AREA = "Vektori i izometrijska preslikavanja"
GRADE7_LESSON = "Usmjerena duž i pojam vektora"


def test_20a_a_grade_edit_drops_the_old_grades_topic(admin, db, existing):
    """Lekcija šestog razreda ne smije preživjeti prelazak u sedmi."""
    page = act(admin, open_page(admin, class_id=str(existing)), grade="7")
    fields = page.get_form()
    assert fields["grade"] == "7"
    assert fields["area_name"] == "" and fields["lesson_name"] == ""
    assert page.post_hidden()["lesson_name"] == ""
    # Bez nove teme čuvanje pada zatvoreno — ne tiho.
    assert class_state(db, existing)[2] == GRADE


def test_20_a_grade_edit_keeps_one_class_and_its_children(admin, db,
                                                          existing, students):
    db.create_student("Sedmak Potvrđen", 7)
    page = act(admin, open_page(admin, class_id=str(existing)), grade="7")
    assert page.roster_count >= 1
    page = act(admin, page, area_name=GRADE7_AREA)
    page = act(admin, page, lesson_name=GRADE7_LESSON)
    assert page.get_form()["grade"] == "7"
    answer = submit(admin, page, {
        students[0]: {"participation": "present", "activity": "4",
                      "homework": "done"},
        students[1]: {"participation": "absent"}})
    assert class_id_of(answer) == existing
    assert class_state(db, existing)[2] == 7
    assert class_state(db, existing)[5] == GRADE7_LESSON
    assert len(classes_snapshot(db)) == 1
    assert {r["student_id"] for r in db.fetch_class_students(existing)} == {
        students[0], students[1]}
    # Nijedan profil učenika nije pomjeren.
    assert {int(r[0]): int(r[1]) for r in db._connection().execute(
        "SELECT id, grade FROM students WHERE id IN (?, ?)",
        (students[0], students[1])).fetchall()} == {students[0]: GRADE,
                                                    students[1]: GRADE}


# ===========================================================================
# 21) SUDAR TERMINA
# ===========================================================================
def test_21_moving_onto_an_occupied_termin_fails_closed(admin, db, existing,
                                                        students):
    page = act(admin, act(admin, open_page(admin), grade=str(GRADE)),
               session_date=DATE, session_time="15:00", area_name=AREA)
    page = act(admin, page, lesson_name=LESSON)
    other = class_id_of(submit(admin, page, {
        students[2]: {"participation": "present", "activity": "3",
                      "homework": "done"}}))
    assert other is not None and other != existing

    before_rows, before_classes = rows_snapshot(db), classes_snapshot(db)
    draft = act(admin, open_page(admin, class_id=str(existing)),
                session_time="15:00")
    assert draft.get_form()["session_time"] == "15:00", "nacrt mora biti vidljiv"

    answer = submit(admin, draft, {
        students[0]: {"participation": "present", "activity": "4",
                      "homework": "done"}})
    assert answer.status_code == 302
    body = admin.get(answer.headers["Location"]).data.decode("utf-8")
    assert "već postoji čas" in body
    assert rows_snapshot(db) == before_rows, "djeca su dirana"
    assert classes_snapshot(db) == before_classes, "čas je pomjeren"
    assert len(classes_snapshot(db)) == 2


def test_21b_a_rejected_edit_keeps_the_class_identity(admin, db, existing,
                                                      students):
    """Poslije odbijene izmjene forma i dalje uređuje ISTI čas.

    Bez `class_id` u povratku, sljedeće čuvanje bi napravilo dvojnika."""
    page = act(admin, open_page(admin, class_id=str(existing)),
               topic_mode=CUSTOM)
    answer = submit(admin, page, {
        students[0]: {"participation": "present", "activity": "4",
                      "homework": "done"}})
    after = Page(admin.get(answer.headers["Location"]).data.decode("utf-8"))
    assert after.get_form().get("class_id") == str(existing)
    assert after.get_form().get("edit_draft") == "1"
    assert "Uredi čas" in after.html


# ===========================================================================
# 22-23) NAPUŠTEN NACRT I VALIDACIJA
# ===========================================================================
def test_22_abandoning_a_draft_leaves_the_database_untouched(admin, db,
                                                             existing):
    before_rows, before_classes = rows_snapshot(db), classes_snapshot(db)
    page = open_page(admin, class_id=str(existing))
    page = act(admin, page, session_time="15:00")
    page = act(admin, page, topic_mode=CUSTOM)
    act(admin, page, lesson_name="Nešto sasvim drugo")

    assert rows_snapshot(db) == before_rows
    assert classes_snapshot(db) == before_classes
    # Ponovno otvaranje čita SAČUVANO stanje, ne napušteni nacrt.
    fresh = open_page(admin, class_id=str(existing))
    fields = fresh.get_form()
    assert fields["session_time"] == TIME
    assert fields["topic_mode"] == CURRICULUM
    assert fields["lesson_name"] == LESSON


def test_23_a_validation_error_keeps_the_draft_not_the_database(admin, db,
                                                                existing,
                                                                students):
    page = act(admin, open_page(admin, class_id=str(existing)),
               session_time="15:00")
    page = act(admin, page, topic_mode=CUSTOM)
    answer = submit(admin, page, {
        students[0]: {"participation": "present", "activity": "4",
                      "homework": "done"}})
    after = Page(admin.get(answer.headers["Location"]).data.decode("utf-8"))
    assert "Unesite temu časa" in after.html
    fields = after.get_form()
    assert fields["session_time"] == "15:00", "nacrt vraćen na bazu"
    assert fields["topic_mode"] == CUSTOM
    assert fields["grade"] == str(GRADE)
    assert after.roster_count >= 3, "spisak je nestao"
    assert class_state(db, existing) == (DATE, TIME, GRADE, CURRICULUM,
                                         AREA, LESSON)


# ===========================================================================
# 24-25) USVOJEN ZATEČENI ČAS I BRISANJE
# ===========================================================================
def test_24_an_adopted_legacy_class_edits_like_any_other(admin, db, students):
    conn = db._connection()
    for student_id in students[:2]:
        conn.execute(
            "INSERT INTO student_sessions (student_id, session_date, "
            " session_time, attendance, activity_rating, homework_status, "
            " area_name, lesson_name, topic_source, comment, "
            " class_session_id, created_at, updated_at) "
            "VALUES (?, ?, ?, 'present', 4, 'done', ?, ?, 'curriculum', NULL, "
            " NULL, '2026-09-01 09:00:00', '2026-09-01 09:00:00')",
            (student_id, DATE, TIME, AREA, LESSON))
    conn.commit()
    adopted = db.adopt_legacy_class(session_date=DATE, session_time=TIME,
                                    topic_source=CURRICULUM, area_name=AREA,
                                    lesson_name=LESSON, grade=GRADE)
    class_id = adopted["class_id"]

    page = open_page(admin, class_id=str(class_id))
    assert page.get_form()["session_time"] == TIME
    page = act(admin, page, session_time="16:00")
    page = act(admin, page, topic_mode=CUSTOM)
    page = act(admin, page, lesson_name=CUSTOM_TOPIC)
    assert page.get_form()["session_time"] == "16:00"
    assert page.roster_count >= 3

    answer = submit(admin, page, {
        students[0]: {"participation": "present", "activity": "5",
                      "homework": "done"},
        students[1]: {"participation": "present", "activity": "3",
                      "homework": "done"}})
    assert class_id_of(answer) == class_id
    assert class_state(db, class_id) == (DATE, "16:00", GRADE, CUSTOM, None,
                                         CUSTOM_TOPIC)
    assert len(classes_snapshot(db)) == 1


def test_25_delete_after_an_edit_is_still_exact(admin, db, existing, students):
    page = act(admin, act(admin, open_page(admin), grade=str(GRADE)),
               session_date=DATE, session_time="18:00", area_name=AREA)
    page = act(admin, page, lesson_name=LESSON)
    other = class_id_of(submit(admin, page, {
        students[2]: {"participation": "present", "activity": "3",
                      "homework": "done"}}))

    edited = act(admin, open_page(admin, class_id=str(existing)),
                 session_time="15:00")
    submit(admin, edited, {
        students[0]: {"participation": "present", "activity": "4",
                      "homework": "done"},
        students[1]: {"participation": "absent"}})

    other_children = sorted(int(r[0]) for r in db._connection().execute(
        "SELECT id FROM student_sessions WHERE class_session_id = ?",
        (other,)).fetchall())
    admin.post("/admin/sessions/%d/delete" % existing, data={
        "csrf_token": csrf_from(
            admin.get("/admin/sessions/%d/delete" % existing))})
    assert db.fetch_class(existing) is None
    assert not db._connection().execute(
        "SELECT COUNT(*) FROM student_sessions WHERE class_session_id = ?",
        (existing,)).fetchall()[0][0]
    assert db.fetch_class(other) is not None
    assert sorted(int(r[0]) for r in db._connection().execute(
        "SELECT id FROM student_sessions WHERE class_session_id = ?",
        (other,)).fetchall()) == other_children


# ===========================================================================
# 26-27) IZVJEŠTAJI
# ===========================================================================
def test_26_draft_round_trips_change_zero_report_metrics(admin, db, existing,
                                                         students):
    before = {s: report_input.build_instruction_section(s, "2026-09",
                                                        database=db)
              for s in students}
    facts = {s: report_facts.build_ai_facts(
        report_input.build_report_input(s, "2026-09", database=db))
        for s in students}

    page = open_page(admin, class_id=str(existing))
    for change in ({"session_time": "15:00"}, {"topic_mode": CUSTOM},
                   {"lesson_name": CUSTOM_TOPIC},
                   {"session_date": "2026-10-05"}):
        page = act(admin, page, **change)

    assert {s: report_input.build_instruction_section(s, "2026-09",
                                                      database=db)
            for s in students} == before
    assert {s: report_facts.build_ai_facts(
        report_input.build_report_input(s, "2026-09", database=db))
        for s in students} == facts
    from matbot import report_prompt
    assert report_prompt.REPORT_PROMPT_VERSION == "3d-2"


def test_26b_a_metadata_only_edit_keeps_the_classroom_evidence(admin, db,
                                                               existing,
                                                               students):
    """Promjena vremena/teme ne smije pomjeriti prisustvo, angažman ni zadaću."""
    before = report_input.build_instruction_section(students[0], "2026-09",
                                                    database=db)
    page = act(admin, open_page(admin, class_id=str(existing)),
               session_time="15:00")
    page = act(admin, page, topic_mode=CUSTOM)
    page = act(admin, page, lesson_name=CUSTOM_TOPIC)
    submit(admin, page, {
        students[0]: {"participation": "present", "activity": "4",
                      "homework": "done", "comment": "dobar rad"},
        students[1]: {"participation": "absent"}})
    after = report_input.build_instruction_section(students[0], "2026-09",
                                                   database=db)
    for key in ("present_count", "absent_count", "sessions_total"):
        assert after[key] == before[key], key
    assert after["activity"] == before["activity"]
    assert after["homework"] == before["homework"]
    # Ručna tema NIJE kurikularno gradivo — seli se u `custom_topics`.
    assert CUSTOM_TOPIC not in (after["lessons_worked"] or [])
    assert CUSTOM_TOPIC in (after["custom_topics"] or [])


def test_27_saved_reports_are_untouched_by_an_edit(admin, db, existing,
                                                   students):
    conn = db._connection()
    conn.execute(
        "INSERT INTO monthly_reports (student_id, report_month, metrics_json, "
        " ai_summary, instructor_comment, pdf_path, status, created_at, "
        " updated_at) VALUES (?, '2026-09', ?, ?, ?, ?, 'draft', "
        " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        (students[0], json.dumps({"present_count": 99}), "sačuvan tekst",
         "komentar", "/reports/x.pdf"))
    conn.commit()
    saved = [tuple(r) for r in conn.execute(
        "SELECT student_id, report_month, metrics_json, ai_summary, "
        " instructor_comment, pdf_path FROM monthly_reports").fetchall()]

    page = act(admin, open_page(admin, class_id=str(existing)),
               session_time="15:00")
    submit(admin, page, {
        students[0]: {"participation": "present", "activity": "1",
                      "homework": "not_done"}})
    assert [tuple(r) for r in db._connection().execute(
        "SELECT student_id, report_month, metrics_json, ai_summary, "
        " instructor_comment, pdf_path FROM monthly_reports").fetchall()] \
        == saved


# ===========================================================================
# 28) NOV ČAS NIJE POSTAO IZMJENA
# ===========================================================================
def test_28_a_new_class_is_unaffected_by_the_draft_marker(admin, db,
                                                          students):
    page = act(admin, open_page(admin), grade=str(GRADE))
    assert page.get_form().get("edit_draft") is None
    assert page.get_form().get("class_id") is None
    assert "Upiši čas" in page.html and "Uredi čas" not in page.html
    page = act(admin, page, session_date=DATE, session_time="19:00",
               area_name=AREA)
    page = act(admin, page, lesson_name=LESSON)
    answer = submit(admin, page, {
        students[0]: {"participation": "present", "activity": "4",
                      "homework": "done"}})
    assert class_id_of(answer) is not None
    assert len(classes_snapshot(db)) == 1
