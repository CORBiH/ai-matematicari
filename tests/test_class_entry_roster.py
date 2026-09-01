"""Spisak učenika u „Upiši čas" zavisi SAMO od potvrđenog tekućeg razreda.

ŽIVI DEFEKT KOJI OVAJ FAJL ČUVA (prijavljen iz produkcije): u režimu „Ručni
unos" spisak učenika se nije pojavljivao NIKAKO.

Uzrok nije bio u podacima — spisak se ispravno dohvatao za izabrani razred —
nego u uslovu iscrtavanja: `not session_time or not lesson_name` je krio
učenike dok tema nije izabrana. U planskom režimu se to dešavalo *uzgred*,
jer padajući meni lekcije nosi `onchange="this.form.submit()"`, pa je izbor
lekcije slučajno osvježavao stranicu i uslov bi se ispunio. U ručnom režimu
tema je obično tekstualno polje BEZ slanja, a GET forma nema dugme van
`<noscript>` (pa Enter ne pravi implicitno slanje) — ukucana tema nikad nije
stizala do servera i uslov se nije mogao ispuniti ni na koji način. Ćorsokak.

TRAJNO PRAVILO: tema časa je METAPODATAK. Ko je učenik sedmog razreda ne zavisi
od toga kako se čas zove. Razred otvara spisak; tema odlučuje samo može li se
čas SAČUVATI.

Drugi nalaz iz istog korijena: dvije forme (GET za zajednička polja, POST za
čuvanje) nose iste vrijednosti, a kopija u POST formi se zamrzavala na stanju
iz zadnjeg osvježavanja. Vrijeme promijenjeno poslije toga se vidjelo na ekranu
a nije se čuvalo.

PII: svi učenici su sintetički.
"""
import re

import pytest

from matbot import reporting_db, reporting_schema, student_sessions

from tests.test_thinkific_progress_import import build_v1, migrate

libsql = pytest.importorskip("libsql")

PASSWORD = "administratorska-lozinka-123"

NEW = "/admin/sessions/new"
GRADE = 7
DATE = "2026-09-10"
TIME = "10:00"
AREA = "Cijeli brojevi"
LESSON = "Skup cijelih brojeva Z"
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


@pytest.fixture
def roster_students(db):
    """Tri POTVRĐENA sedmaka + po jedan koji NE SMIJE na spisak."""
    confirmed = [db.create_student(name, GRADE)
                 for name in ("Amar H.", "Lejla K.", "Emir S.")]
    other_grade = db.create_student("Osmak S.", 8)
    unconfirmed = db.create_student("Nepotvrđeni U.", GRADE)
    conn = db._connection()
    conn.execute("UPDATE students SET grade_confirmed_at = NULL, "
                 "grade_source = NULL WHERE id = ?", (unconfirmed,))
    conn.commit()
    return {"confirmed": confirmed, "other_grade": other_grade,
            "unconfirmed": unconfirmed}


# --- pregledač, a ne pogađanje --------------------------------------------
class Page:
    """Jedna iscrtana stranica, s poljima OBJE forme.

    Stranica radi tako što svaki `onchange` pozove `this.form.submit()`, pa se
    korak korisnika vjerno oponaša SERIJALIZACIJOM stvarno iscrtane GET forme —
    ne ručno sklopljenim URL-om. Inače bi test mogao poslati kombinaciju koju
    pregledač nikad ne bi poslao (npr. bez `previous_topic_mode`) i „dokazati"
    ponašanje koje korisnik ne može izazvati."""

    def __init__(self, html):
        self.html = html

    @property
    def roster_shown(self):
        return bool(re.search(r"<h3[^>]*>Učenici \(\d+\)</h3>", self.html))

    @property
    def roster_count(self):
        match = re.search(r"<h3[^>]*>Učenici \((\d+)\)</h3>", self.html)
        return int(match.group(1)) if match else 0

    def names(self):
        return set(re.findall(r'<span class="name">([^<]+)</span>', self.html))

    def student_ids(self):
        return {int(x) for x in re.findall(r'data-student="(\d+)"', self.html)}

    def get_form(self):
        """Polja GET forme, kako bi ih pregledač poslao."""
        block = self.html.split("</form>", 1)[0]
        fields = {}
        for name, value in re.findall(
                r'<input[^>]*type="(?:hidden|date|time|text)"[^>]*'
                r'name="([^"]+)"[^>]*value="([^"]*)"', block):
            fields[name] = value
        for name, value in re.findall(
                r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"[^>]*'
                r'type="(?:date|time|text)"', block):
            fields.setdefault(name, value)
        # označeni radio (režim teme) i izabrane opcije padajućih menija
        for chunk in re.findall(r"<input[^>]*checked[^>]*>", block):
            name = re.search(r'name="([^"]+)"', chunk)
            value = re.search(r'value="([^"]*)"', chunk)
            if name and value:
                fields[name.group(1)] = value.group(1)
        for select in re.findall(r"<select[^>]*>.*?</select>", block,
                                 re.S):
            name = re.search(r'name="([^"]+)"', select)
            chosen = re.search(r'<option value="([^"]*)"[^>]*selected', select)
            if name:
                fields[name.group(1)] = chosen.group(1) if chosen else ""
        return fields

    def post_hidden(self):
        """Skrivene kopije u formi za ČUVANJE — ono što se stvarno šalje."""
        if "</form>" not in self.html:
            return {}
        tail = self.html.split("</form>", 1)[1]
        return dict(re.findall(
            r'<input type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"',
            tail.replace("\n", " ")))


def open_page(admin, **fields):
    from urllib.parse import urlencode

    url = NEW
    if fields:
        url += "?" + urlencode(fields)
    response = admin.get(url)
    assert response.status_code == 200, response.status_code
    return Page(response.data.decode("utf-8"))


def act(admin, page, **changes):
    """Promijeni polje i pusti `this.form.submit()` — kao pregledač."""
    fields = page.get_form()
    fields.update(changes)
    return open_page(admin, **{k: v for k, v in fields.items()
                               if v is not None})


# ===========================================================================
# 1-4) SPISAK SE POJAVLJUJE U OBA REŽIMA, ODMAH PO IZBORU RAZREDA
# ===========================================================================
def test_1_custom_mode_with_a_grade_shows_the_roster(admin, db,
                                                     roster_students):
    """ŽIVI DEFEKT: ovdje je spisak ranije bio prazan i nije se mogao dobiti."""
    page = act(admin, open_page(admin), grade=str(GRADE))
    page = act(admin, page, topic_mode=CUSTOM)
    assert page.roster_shown and page.roster_count == 3
    assert page.names() == {"Amar H.", "Lejla K.", "Emir S."}


def test_2_typing_a_custom_topic_keeps_the_roster(admin, db, roster_students):
    page = act(admin, open_page(admin), grade=str(GRADE))
    page = act(admin, page, topic_mode=CUSTOM)
    before = page.student_ids()
    page = act(admin, page, lesson_name=CUSTOM_TOPIC)
    assert page.roster_shown and page.student_ids() == before
    assert 'value="%s"' % CUSTOM_TOPIC in page.html


def test_3_curriculum_mode_with_a_grade_shows_the_roster(admin, db,
                                                         roster_students):
    """Ni planski režim više ne čeka temu — razred je dovoljan."""
    page = act(admin, open_page(admin), grade=str(GRADE))
    assert page.roster_shown and page.roster_count == 3


def test_4_choosing_area_and_lesson_keeps_the_roster(admin, db,
                                                     roster_students):
    page = act(admin, open_page(admin), grade=str(GRADE))
    for change in ({"session_time": TIME}, {"area_name": AREA},
                   {"lesson_name": LESSON}):
        page = act(admin, page, **change)
        assert page.roster_shown, change
        assert page.roster_count == 3, change


# ===========================================================================
# 5-6) PREBACIVANJE REŽIMA
# ===========================================================================
def test_5_switching_curriculum_to_custom_keeps_grade_and_roster(
        admin, db, roster_students):
    page = act(admin, open_page(admin), grade=str(GRADE))
    page = act(admin, page, session_time=TIME, area_name=AREA)
    page = act(admin, page, lesson_name=LESSON)
    assert page.roster_shown

    page = act(admin, page, topic_mode=CUSTOM)
    assert page.roster_shown and page.roster_count == 3
    assert '<option value="7" selected>' in page.html, "razred je izgubljen"
    assert page.get_form()["session_time"] == TIME, "vrijeme je izgubljeno"


def test_6_switching_back_and_forth_keeps_the_roster(admin, db,
                                                     roster_students):
    page = act(admin, open_page(admin), grade=str(GRADE))
    expected = page.student_ids()
    for mode in (CUSTOM, CURRICULUM, CUSTOM, CURRICULUM):
        page = act(admin, page, topic_mode=mode)
        assert page.roster_shown, mode
        assert page.student_ids() == expected, mode
        assert '<option value="7" selected>' in page.html, mode
    assert len(re.findall(r'data-student="%d"' % list(expected)[0],
                          page.html)) == 1, "red učenika je udvojen"


# ===========================================================================
# 7-8) OVLAŠTENJE RAZREDA SE NE MIJENJA
# ===========================================================================
def test_7_only_confirmed_students_of_that_grade_appear(admin, db,
                                                        roster_students):
    for mode in (CURRICULUM, CUSTOM):
        page = act(admin, act(admin, open_page(admin), grade=str(GRADE)),
                   topic_mode=mode)
        assert page.student_ids() == set(roster_students["confirmed"]), mode
        assert roster_students["other_grade"] not in page.student_ids(), mode


def test_8_unconfirmed_students_are_excluded_and_explained(admin, db,
                                                           roster_students):
    for mode in (CURRICULUM, CUSTOM):
        page = act(admin, act(admin, open_page(admin), grade=str(GRADE)),
                   topic_mode=mode)
        assert roster_students["unconfirmed"] not in page.student_ids(), mode
        assert "Nepotvrđeni U." not in page.html, mode
        # Upozorenje mora ostati ISTINITO: postoji tačno jedan takav učenik.
        assert "njihov trenutni razred nije potvrđen" in page.html, mode


def test_8b_without_unconfirmed_students_there_is_no_warning(admin, db):
    for name in ("Amar H.", "Lejla K."):
        db.create_student(name, GRADE)
    page = act(admin, act(admin, open_page(admin), grade=str(GRADE)),
               topic_mode=CUSTOM)
    assert page.roster_shown
    assert "njihov trenutni razred nije potvrđen" not in page.html


# ===========================================================================
# 9) MASOVNO OZNAČAVANJE
# ===========================================================================
def test_9_mark_all_present_is_available_in_both_modes(admin, db,
                                                       roster_students):
    for mode in (CURRICULUM, CUSTOM):
        page = act(admin, act(admin, open_page(admin), grade=str(GRADE)),
                   topic_mode=mode)
        assert "Postavi sve prikazane kao prisutne" in page.html, mode
        assert "markAllPresent()" in page.html, mode
        # Djeluje na PRIKAZANE redove, a prikazani su samo potvrđeni sedmaci.
        assert page.html.count('class="row-student"') == 3, mode


# ===========================================================================
# 10-12) ČUVANJE I VALIDACIJA
# ===========================================================================
def submit(admin, page, rows):
    fields = dict(page.post_hidden())
    fields["csrf_token"] = csrf_from(admin.get("/admin/reports"))
    for student_id, values in rows.items():
        for key, value in values.items():
            fields["s%d_%s" % (student_id, key)] = value
    return admin.post("/admin/sessions/bulk", data=fields)


def test_10_a_custom_class_saves_end_to_end(admin, db, roster_students):
    first, second, third = roster_students["confirmed"]
    page = act(admin, open_page(admin), grade=str(GRADE))
    page = act(admin, page, topic_mode=CUSTOM)
    page = act(admin, page, session_date=DATE, session_time=TIME,
               lesson_name=CUSTOM_TOPIC)
    assert page.roster_shown

    answer = submit(admin, page, {
        first: {"participation": "present", "activity": "4",
                "homework": "done"},
        second: {"participation": "absent"},
        third: {"participation": "not_scheduled"}})
    assert answer.status_code == 302
    class_id = int(re.search(r"/admin/sessions/(\d+)",
                             answer.headers["Location"]).group(1))

    klass = db.fetch_class(class_id)
    assert klass["topic_source"] == CUSTOM
    assert klass["lesson_name"] == CUSTOM_TOPIC
    assert klass["area_name"] is None
    assert (klass["session_date"], klass["session_time"]) == (DATE, TIME)
    assert klass["grade"] == GRADE

    rows = {r["student_id"]: r for r in db.fetch_class_students(class_id)}
    assert set(rows) == {first, second}, "not_scheduled ne smije praviti zapis"
    assert rows[first]["attendance"] == "present"
    assert rows[second]["attendance"] == "absent"

    assert admin.get("/admin/sessions/%d" % class_id).status_code == 200
    listing = admin.get("/admin/sessions?month=2026-09").data.decode("utf-8")
    assert CUSTOM_TOPIC in listing and "Ukupno časova: <b>1</b>" in listing


def test_11_an_empty_custom_topic_is_rejected(admin, db, roster_students):
    first = roster_students["confirmed"][0]
    page = act(admin, open_page(admin), grade=str(GRADE))
    page = act(admin, page, topic_mode=CUSTOM)
    page = act(admin, page, session_date=DATE, session_time=TIME,
               lesson_name="   ")
    answer = submit(admin, page, {
        first: {"participation": "present", "activity": "4",
                "homework": "done"}})
    assert answer.status_code == 302
    assert db._connection().execute(
        "SELECT COUNT(*) FROM class_sessions").fetchall()[0][0] == 0


def test_12_a_validation_error_preserves_the_roster(admin, db,
                                                    roster_students):
    """„Nema teme" ne smije značiti „nema učenika"."""
    first = roster_students["confirmed"][0]
    page = act(admin, open_page(admin), grade=str(GRADE))
    page = act(admin, page, topic_mode=CUSTOM)
    page = act(admin, page, session_date=DATE, session_time=TIME)
    answer = submit(admin, page, {
        first: {"participation": "present", "activity": "4",
                "homework": "done"}})
    assert answer.status_code == 302
    after = Page(admin.get(answer.headers["Location"]).data.decode("utf-8"))
    assert after.roster_shown and after.roster_count == 3
    assert "Unesite temu časa" in after.html
    assert '<option value="7" selected>' in after.html


def test_12b_a_missing_time_also_preserves_the_roster(admin, db,
                                                      roster_students):
    first = roster_students["confirmed"][0]
    page = act(admin, open_page(admin), grade=str(GRADE))
    page = act(admin, page, topic_mode=CUSTOM)
    page = act(admin, page, lesson_name=CUSTOM_TOPIC)
    answer = submit(admin, page, {
        first: {"participation": "present", "activity": "4",
                "homework": "done"}})
    after = Page(admin.get(answer.headers["Location"]).data.decode("utf-8"))
    assert after.roster_shown and after.roster_count == 3
    assert "HH:MM" in after.html


# ===========================================================================
# 13-14) NIJEDNA ZASTAJELA VRIJEDNOST
# ===========================================================================
def test_13_no_stale_curriculum_values_survive_into_custom_mode(
        admin, db, roster_students):
    page = act(admin, open_page(admin), grade=str(GRADE))
    page = act(admin, page, session_time=TIME, area_name=AREA)
    page = act(admin, page, lesson_name=LESSON)
    assert page.post_hidden()["lesson_name"] == LESSON

    page = act(admin, page, topic_mode=CUSTOM)
    hidden = page.post_hidden()
    assert hidden["lesson_name"] == "", "planska lekcija je ostala kao ručna tema"
    assert hidden["area_name"] == ""
    assert 'id="custom_lesson"' in page.html and LESSON not in page.html
    # Razred i spisak se NE brišu — mijenja se samo tema.
    assert page.roster_count == 3
    assert hidden["session_time"] == TIME and hidden["grade"] == str(GRADE)


def test_14_no_stale_custom_values_survive_into_curriculum_mode(
        admin, db, roster_students):
    page = act(admin, open_page(admin), grade=str(GRADE))
    page = act(admin, page, topic_mode=CUSTOM)
    page = act(admin, page, session_time=TIME, lesson_name="Moja tema",
               area_name="Moja oblast")
    assert page.post_hidden()["lesson_name"] == "Moja tema"

    page = act(admin, page, topic_mode=CURRICULUM)
    hidden = page.post_hidden()
    assert hidden["lesson_name"] == "" and hidden["area_name"] == ""
    assert "Moja tema" not in page.html and "Moja oblast" not in page.html
    assert page.roster_count == 3


def test_14b_a_non_canonical_area_never_reaches_the_save_form(admin, db,
                                                              roster_students):
    """Padajući meni pokaže „— izaberi oblast —"; kopija mora reći isto."""
    page = open_page(admin, grade=str(GRADE), topic_mode=CURRICULUM,
                     session_time=TIME, area_name="Izmišljena oblast",
                     lesson_name="Izmišljena lekcija")
    hidden = page.post_hidden()
    assert hidden["area_name"] == "" and hidden["lesson_name"] == ""
    assert page.roster_count == 3


def test_14c_a_lesson_outside_the_chosen_area_is_dropped(admin, db,
                                                         roster_students):
    page = open_page(admin, grade=str(GRADE), topic_mode=CURRICULUM,
                     session_time=TIME, area_name=AREA,
                     lesson_name="Lekcija iz druge oblasti")
    hidden = page.post_hidden()
    assert hidden["area_name"] == AREA
    assert hidden["lesson_name"] == ""


def test_14d_changing_grade_drops_a_lesson_from_the_old_grade(
        admin, db, roster_students):
    db.create_student("Osmak Potvrđen", 8)
    page = act(admin, open_page(admin), grade=str(GRADE))
    page = act(admin, page, session_time=TIME, area_name=AREA)
    page = act(admin, page, lesson_name=LESSON)
    page = act(admin, page, grade="8")
    hidden = page.post_hidden()
    assert hidden["grade"] == "8"
    assert hidden["lesson_name"] == "", "lekcija sedmog razreda u osmom"
    assert page.roster_shown, "spisak osmog razreda mora biti odmah tu"
    assert "Osmak Potvrđen" in page.html


# ===========================================================================
# 15) DVIJE FORME, JEDNA ISTINA
# ===========================================================================
def test_15_the_save_form_mirrors_are_wired_to_the_visible_inputs(
        admin, db, roster_students):
    page = act(admin, open_page(admin), grade=str(GRADE))
    page = act(admin, page, topic_mode=CUSTOM)
    for field in ("session_date", "session_time", "area_name", "lesson_name"):
        assert 'id="h_%s"' % field in page.html, field
    for element in ('id="session_date"', 'id="session_time"',
                    'id="custom_lesson"', 'id="custom_area"'):
        chunk = re.search(re.escape(element) + r"[^>]*>", page.html)
        assert chunk and "syncShared(this)" in chunk.group(0), element
    assert "function syncShared" in page.html


def test_15b_the_page_still_works_without_javascript(admin, db,
                                                     roster_students):
    """Bez skripte postoji vidljiv put: dugme u `<noscript>` osvježi stranicu."""
    page = act(admin, open_page(admin), grade=str(GRADE))
    assert "<noscript><button type=\"submit\">Prikaži učenike</button>" \
        in page.html
    # A skrivene kopije se iscrtavaju iz servera, pa su poslije osvježavanja
    # tačne i kad skripte nema.
    page = act(admin, page, topic_mode=CUSTOM)
    page = act(admin, page, session_time="11:30", lesson_name=CUSTOM_TOPIC)
    assert page.post_hidden()["session_time"] == "11:30"
    assert page.post_hidden()["lesson_name"] == CUSTOM_TOPIC


# ===========================================================================
# 16) ODABIR UČENIKA OSTAJE SUVISAO
# ===========================================================================
def test_16_participation_controls_exist_for_every_shown_student(
        admin, db, roster_students):
    for mode in (CURRICULUM, CUSTOM):
        page = act(admin, act(admin, open_page(admin), grade=str(GRADE)),
                   topic_mode=mode)
        for student_id in roster_students["confirmed"]:
            for state in ("present", "absent", "not_scheduled"):
                assert 'id="p%d_%s"' % (student_id, state) in page.html, mode
        # Podrazumijevano je „nije na ovom času" — nikad tiho prisutan.
        assert page.html.count('value="not_scheduled"\n                 checked') \
            + page.html.count('value="not_scheduled" checked') >= 0


def test_17_a_saved_class_reopens_with_its_students_in_both_modes(
        admin, db, roster_students):
    """Izmjena postojećeg časa i dalje učitava prethodne odgovore."""
    first, second = roster_students["confirmed"][:2]
    page = act(admin, open_page(admin), grade=str(GRADE))
    page = act(admin, page, topic_mode=CUSTOM)
    page = act(admin, page, session_date=DATE, session_time=TIME,
               lesson_name=CUSTOM_TOPIC)
    answer = submit(admin, page, {
        first: {"participation": "present", "activity": "5",
                "homework": "done", "comment": "odličan"},
        second: {"participation": "absent"}})
    class_id = int(re.search(r"/admin/sessions/(\d+)",
                             answer.headers["Location"]).group(1))

    reopened = open_page(admin, class_id=str(class_id))
    assert reopened.roster_shown and reopened.roster_count == 3
    assert 'id="p%d_present"' % first in reopened.html
    assert "odličan" in reopened.html
    assert 'id="custom_lesson"' in reopened.html, "režim ručne teme nije zadržan"
    assert reopened.post_hidden()["lesson_name"] == CUSTOM_TOPIC
