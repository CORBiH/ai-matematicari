"""Granica ovlaštenja administratorskog dijela — mjereno, ne pretpostavljeno.

POVOD: operater je prijavio da stranica prijave prikazuje punu administratorsku
navigaciju i da se čas navodno može upisati bez prijave. To je provjereno kao
MOGUĆ PROPUST U OVLAŠTENJU, a ne kao kozmetika.

IZMJERENI ISHOD: propusta nema. Sve rute osim same prijave prolaze kroz
`require_admin`; anoniman klijent dobija 302 na prijavu (GET) ili 403 (POST), i
NIJEDAN anoniman zahtjev ne stiže do upisa. Ovaj fajl to drži tako zauvijek:
nabraja STVARNU tabelu ruta i pada ako se ijedna nova ruta pojavi nezaštićena —
umjesto da se oslanja na to da će neko zapamtiti dekorator.

DVA STVARNA KVARA KOJA SU NAĐENA I POPRAVLJENA:

1. Stranica prijave nasljeđuje zajednički okvir, pa je neprijavljenom posjetiocu
   iscrtavala pun meni iznad forme za prijavu. Nijedna veza nije radila, ali je
   prikaz tvrdio suprotno — prikaz koji laže o pristupu je kvar sam po sebi.

2. Nijedan administratorski odgovor nije nosio direktivu o kešu. To je i
   objašnjenje operaterove prijave: pregledač je na „Nazad" mogao ponovo
   iscrtati ZAPAMĆENU stranicu prijave dok je sesija još važila, pa je
   izgledalo kao rad bez prijave. Sesija je bila stvarna; slika je bila stara.

PII: svi učenici su sintetički.
"""
import re

import pytest

from matbot import reporting_db, reporting_schema

from tests.test_thinkific_progress_import import build_v1, migrate

libsql = pytest.importorskip("libsql")

PASSWORD = "administratorska-lozinka-123"

LOGIN = "/admin/reports/login"
GRADE = 6
DATE, TIME = "2026-09-01", "10:00"
TOPIC = "Tajna tema"
COMMENT = "tajno zapažanje"

# Svaki od ovih nizova znači da je privatni sadržaj procurio u odgovor.
PRIVATE_MARKERS = ("Amar Tajni", "Lejla Tajna", COMMENT, TOPIC)

WRITE_RE = re.compile(r"\A\s*(INSERT|UPDATE|DELETE|REPLACE|DROP|ALTER)\b", re.I)


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


class _CountingConn:
    """Konekcija koja BROJI svaki upis. Dokaz „nula upisa" mora biti mjeren."""

    def __init__(self, inner, log):
        self._inner, self._log = inner, log

    def execute(self, sql, *args, **kwargs):
        if WRITE_RE.match(str(sql)):
            self._log.append(" ".join(str(sql).split())[:80])
        return self._inner.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


@pytest.fixture
def writes():
    return []


@pytest.fixture
def db(tmp_path, monkeypatch, writes):
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
        connect_factory=lambda: _CountingConn(
            libsql.connect(path, timeout=10.0, _check_same_thread=False),
            writes))
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
    token = csrf_from(client.get(LOGIN))
    assert client.post(LOGIN, data={"csrf_token": token,
                                    "password": PASSWORD}).status_code == 302
    return client


@pytest.fixture
def anon(flask_app, admin_env):
    """POTPUNO svjež klijent: bez kolačića, bez sesije, bez CSRF-a."""
    return flask_app.test_client()


@pytest.fixture
def seeded(admin, db):
    """Privatni podaci koji NE SMIJU procuriti, upisani kroz prijavljen put."""
    ids = [db.create_student(name, GRADE)
           for name in ("Amar Tajni", "Lejla Tajna")]
    page = admin.get("/admin/sessions/new?grade=%d&session_date=%s"
                     "&session_time=%s&topic_mode=custom&lesson_name=%s"
                     % (GRADE, DATE, TIME.replace(":", "%3A"),
                        TOPIC.replace(" ", "%20")))
    answer = admin.post("/admin/sessions/bulk", data={
        "csrf_token": csrf_from(page), "session_date": DATE,
        "session_time": TIME, "grade": str(GRADE), "topic_mode": "custom",
        "area_name": "", "lesson_name": TOPIC,
        "s%d_participation" % ids[0]: "present",
        "s%d_activity" % ids[0]: "4", "s%d_homework" % ids[0]: "done",
        "s%d_comment" % ids[0]: COMMENT,
        "s%d_participation" % ids[1]: "absent"})
    class_id = int(re.search(r"/admin/sessions/(\d+)",
                             answer.headers["Location"]).group(1))
    return {"students": ids, "class_id": class_id}


def admin_rules(flask_app):
    """STVARNA tabela ruta. Ne prepisuje se rukom — pa nova ruta ne može uteći."""
    found = []
    for rule in flask_app.url_map.iter_rules():
        if str(rule.rule).startswith("/admin"):
            for method in sorted(rule.methods & {"GET", "POST"}):
                found.append((method, str(rule.rule), rule.endpoint))
    return sorted(found)


def concrete(path, seeded):
    return (path.replace("<int:student_id>", str(seeded["students"][0]))
                .replace("<int:class_id>", str(seeded["class_id"]))
                .replace("<int:session_id>", "1"))


MUTATION_PAYLOADS = {
    "admin_sessions.save_class": {
        "session_date": "2026-09-02", "session_time": "11:00", "grade": "6",
        "topic_mode": "custom", "area_name": "", "lesson_name": "Upad"},
    "admin_sessions.adopt_legacy_class": {
        "session_date": DATE, "session_time": TIME,
        "topic_source": "curriculum", "area_name": "", "lesson_name": TOPIC,
        "grade": "6"},
    "admin_students.update_grade": {"grade": "9"},
    "admin_students.create_student": {"display_name": "Upad", "grade": "6"},
    "admin_students.create_session": {
        "session_date": "2026-09-02", "attendance": "present",
        "activity_rating": "5", "homework_status": "done"},
}


# ===========================================================================
# 1-2) JAVNO JE SAMO PRIJAVA
# ===========================================================================
def test_1_anonymous_login_page_is_public(anon, db):
    response = anon.get(LOGIN)
    assert response.status_code == 200
    assert b'name="password"' in response.data


def test_2_every_admin_route_except_login_is_guarded(flask_app, admin_env):
    """Kapija se dokazuje NA TABELI RUTA, ne na spisku koji neko održava."""
    unguarded = []
    for method, rule, endpoint in admin_rules(flask_app):
        view = flask_app.view_functions[endpoint]
        qual = getattr(view, "__qualname__", "")
        if "require_admin" not in qual and not hasattr(view, "__wrapped__"):
            unguarded.append((method, rule, endpoint))
    assert [u for u in unguarded if u[2] != "admin_reports.login"] == []
    # I obrnuto: prijava MORA ostati javna, inače nastaje petlja preusmjerenja.
    login_view = flask_app.view_functions["admin_reports.login"]
    assert "require_admin" not in getattr(login_view, "__qualname__", "")


# ===========================================================================
# 3-7) ANONIMNI GET NA SVAKU PRIVATNU RUTU
# ===========================================================================
def test_3_every_anonymous_get_is_refused(anon, flask_app, db, seeded, writes):
    checked = 0
    for method, rule, endpoint in admin_rules(flask_app):
        if method != "GET" or endpoint == "admin_reports.login":
            continue
        writes.clear()
        response = anon.get(concrete(rule, seeded))
        assert response.status_code in (302, 401, 403, 404), (rule,
                                                              response.status_code)
        if response.status_code == 302:
            assert LOGIN in response.headers.get("Location", ""), rule
        assert writes == [], (rule, writes)
        checked += 1
    assert checked >= 10, "premalo GET ruta provjereno: %d" % checked


@pytest.mark.parametrize("path", ["/admin/reports", "/admin/students",
                                  "/admin/sessions", "/admin/sessions/new",
                                  "/admin/sessions/legacy"])
def test_4_direct_url_entry_is_refused(anon, db, path):
    """Kucanje URL-a rukom, ne klik na vezu."""
    response = anon.get(path)
    assert response.status_code == 302
    assert LOGIN in response.headers["Location"]


def test_5_anonymous_class_detail_is_refused(anon, db, seeded):
    response = anon.get("/admin/sessions/%d" % seeded["class_id"])
    assert response.status_code == 302 and LOGIN in response.headers["Location"]


def test_6_anonymous_student_profile_is_refused(anon, db, seeded):
    response = anon.get("/admin/students/%d" % seeded["students"][0])
    assert response.status_code == 302 and LOGIN in response.headers["Location"]


def test_7_no_private_data_in_any_anonymous_response(anon, flask_app, db,
                                                     seeded):
    for method, rule, endpoint in admin_rules(flask_app):
        url = concrete(rule, seeded)
        response = (anon.post(url, data=dict(MUTATION_PAYLOADS.get(endpoint, {})))
                    if method == "POST" else anon.get(url))
        body = response.data.decode("utf-8", "replace")
        for marker in PRIVATE_MARKERS:
            assert marker not in body, (rule, marker)


# ===========================================================================
# 8-13) ANONIMNE MUTACIJE — NULA UPISA
# ===========================================================================
def test_8_every_anonymous_post_writes_nothing(anon, flask_app, db, seeded,
                                               writes):
    before = _snapshot(db)
    checked = 0
    for method, rule, endpoint in admin_rules(flask_app):
        if method != "POST" or endpoint == "admin_reports.login":
            continue
        writes.clear()
        response = anon.post(concrete(rule, seeded),
                             data=dict(MUTATION_PAYLOADS.get(endpoint, {})))
        assert response.status_code in (302, 400, 401, 403, 404), rule
        assert writes == [], (rule, writes)
        checked += 1
    assert checked >= 8, "premalo POST ruta provjereno: %d" % checked
    assert _snapshot(db) == before


def _snapshot(db):
    conn = db._connection()
    return {table: conn.execute("SELECT COUNT(*) FROM %s" % table
                                ).fetchall()[0][0]
            for table in ("class_sessions", "student_sessions", "students",
                          "monthly_reports")}


def test_9_anonymous_class_create_creates_nothing(anon, db, seeded, writes):
    before = _snapshot(db)
    writes.clear()
    response = anon.post("/admin/sessions/bulk", data=dict(
        MUTATION_PAYLOADS["admin_sessions.save_class"],
        **{"s%d_participation" % seeded["students"][0]: "present",
           "s%d_activity" % seeded["students"][0]: "5",
           "s%d_homework" % seeded["students"][0]: "done"}))
    assert response.status_code == 403
    assert writes == [] and _snapshot(db) == before


def test_10_anonymous_class_edit_changes_nothing(anon, db, seeded, writes):
    before = db.fetch_class(seeded["class_id"])
    writes.clear()
    response = anon.post("/admin/sessions/bulk", data={
        "class_id": str(seeded["class_id"]), "session_date": "2026-12-31",
        "session_time": "23:00", "grade": "9", "topic_mode": "custom",
        "area_name": "", "lesson_name": "Oteto"})
    assert response.status_code == 403
    assert writes == [] and db.fetch_class(seeded["class_id"]) == before


def test_11_anonymous_class_delete_deletes_nothing(anon, db, seeded, writes):
    writes.clear()
    response = anon.post("/admin/sessions/%d/delete" % seeded["class_id"],
                         data={})
    assert response.status_code == 403
    assert writes == []
    assert db.fetch_class(seeded["class_id"]) is not None
    assert len(db.fetch_class_students(seeded["class_id"])) == 2


def test_12_anonymous_adoption_adopts_nothing(anon, db, seeded, writes):
    writes.clear()
    response = anon.post("/admin/sessions/legacy/adopt", data=dict(
        MUTATION_PAYLOADS["admin_sessions.adopt_legacy_class"]))
    assert response.status_code == 403
    assert writes == []


def test_13_anonymous_grade_mutation_changes_nothing(anon, db, seeded, writes):
    student_id = seeded["students"][0]
    before = db.fetch_student_profile(student_id)
    writes.clear()
    response = anon.post("/admin/students/%d/grade" % student_id,
                         data={"grade": "9"})
    assert response.status_code == 403
    assert writes == []
    assert db.fetch_student_profile(student_id) == before


# ===========================================================================
# 14-16) CSRF I PRIJAVLJEN PUT
# ===========================================================================
def test_14_authenticated_without_csrf_writes_nothing(admin, db, seeded,
                                                      writes):
    before = _snapshot(db)
    writes.clear()
    response = admin.post("/admin/sessions/bulk", data=dict(
        MUTATION_PAYLOADS["admin_sessions.save_class"]))
    assert response.status_code == 400
    assert writes == [] and _snapshot(db) == before


def test_15_authenticated_with_a_wrong_csrf_writes_nothing(admin, db, seeded,
                                                           writes):
    writes.clear()
    response = admin.post("/admin/sessions/bulk", data=dict(
        MUTATION_PAYLOADS["admin_sessions.save_class"],
        csrf_token="ovo-nije-token"))
    assert response.status_code == 400
    assert writes == []


def test_15b_the_central_csrf_layer_is_load_bearing(admin, db):
    """Odjava se oslanja SAMO na CSRF provjeru u `require_admin`.

    Ostale mutirajuće rute imaju i vlastitu provjeru, pa bi ova centralna mogla
    tiho otkazati a da se to nigdje ne vidi. Odjava je njen jedini korisnik i
    zato je ovdje mjeri."""
    assert admin.post("/admin/reports/logout", data={}).status_code == 400
    assert admin.post("/admin/reports/logout",
                      data={"csrf_token": "nije-token"}).status_code == 400
    # Sesija je preživjela odbijeni zahtjev — dakle ništa se nije izvršilo.
    assert admin.get("/admin/reports").status_code == 200


def test_15c_a_non_ascii_csrf_token_is_refused_cleanly(admin, anon, db,
                                                       writes):
    """ŽIVI NALAZ: znak van ASCII-ja je rušio provjeru u 500, s ispisom traga.

    Zahtjev se nikad nije izvršio, pa propusta nema — ali odbijanje mora biti
    ČISTO, i na prijavljenom i na anonimnom putu."""
    for token in ("pogrešan", "čćžšđ", "🙂"):
        writes.clear()
        response = admin.post("/admin/sessions/bulk", data=dict(
            MUTATION_PAYLOADS["admin_sessions.save_class"],
            csrf_token=token))
        assert response.status_code == 400, (token, response.status_code)
        assert writes == [], token
        assert admin.post("/admin/reports/logout",
                          data={"csrf_token": token}).status_code == 400, token
        # Anonimna prijava je javna ruta — i ona mora odbiti, ne pući.
        assert anon.post(LOGIN, data={"csrf_token": token,
                                      "password": "bilo"}).status_code == 400
    # Sesija je i dalje živa: nijedan od tih zahtjeva ništa nije uradio.
    assert admin.get("/admin/reports").status_code == 200


def test_16_authenticated_with_valid_csrf_still_works(admin, db, seeded):
    """Popravka ne smije slomiti stvaran rad instruktora."""
    student_id = seeded["students"][0]
    page = admin.get("/admin/sessions/new?grade=%d&session_date=2026-09-05"
                     "&session_time=09%%3A00&topic_mode=custom"
                     "&lesson_name=Radni%%20cas" % GRADE)
    assert page.status_code == 200
    answer = admin.post("/admin/sessions/bulk", data={
        "csrf_token": csrf_from(page), "session_date": "2026-09-05",
        "session_time": "09:00", "grade": str(GRADE), "topic_mode": "custom",
        "area_name": "", "lesson_name": "Radni cas",
        "s%d_participation" % student_id: "present",
        "s%d_activity" % student_id: "4",
        "s%d_homework" % student_id: "done"})
    assert answer.status_code == 302
    assert re.search(r"/admin/sessions/(\d+)", answer.headers["Location"])


# ===========================================================================
# 17-19) ODJAVA I SESIJA
# ===========================================================================
def test_17_logout_removes_access(admin, db, seeded, writes):
    token = csrf_from(admin.get("/admin/sessions/new?grade=%d" % GRADE))
    assert admin.post("/admin/reports/logout",
                      data={"csrf_token": token}).status_code == 302

    after = admin.get("/admin/sessions/new?grade=%d" % GRADE)
    assert after.status_code == 302 and LOGIN in after.headers["Location"]

    writes.clear()
    before = _snapshot(db)
    blocked = admin.post("/admin/sessions/bulk", data=dict(
        MUTATION_PAYLOADS["admin_sessions.save_class"], csrf_token=token))
    assert blocked.status_code == 403, "stari CSRF token je preživio odjavu"
    assert writes == [] and _snapshot(db) == before


def test_18_an_authenticated_visitor_is_redirected_away_from_login(admin, db):
    """Nikad „forma za prijavu iznad žive sesije" — to je bila izvorna zbrka."""
    response = admin.get(LOGIN)
    assert response.status_code == 302
    assert "/admin/reports" in response.headers["Location"]
    assert b'name="password"' not in response.data


def test_19_login_page_is_reachable_again_after_logout(admin, db):
    token = csrf_from(admin.get("/admin/reports"))
    admin.post("/admin/reports/logout", data={"csrf_token": token})
    response = admin.get(LOGIN)
    assert response.status_code == 200 and b'name="password"' in response.data


# ===========================================================================
# 20-22) NAVIGACIJA PRATI SERVERSKO STANJE
# ===========================================================================
def test_20_the_anonymous_login_page_shows_no_admin_navigation(anon, db):
    body = anon.get(LOGIN).data.decode("utf-8")
    assert 'class="adminnav"' not in body
    assert re.findall(r'<a[^>]*href="/admin[^"]*"', body) == [], \
        "stranica prijave nudi veze u administratorski dio"
    for label in ("Svi časovi", "Upiši čas", "Učenici", "Thinkific"):
        assert label not in body, label
    assert b'name="password"' in anon.get(LOGIN).data


def test_21_authenticated_pages_keep_the_navigation(admin, db, seeded):
    for url in ("/admin/reports", "/admin/students",
                "/admin/sessions", "/admin/sessions/new?grade=%d" % GRADE,
                "/admin/sessions/%d" % seeded["class_id"]):
        body = admin.get(url).data.decode("utf-8")
        assert 'class="adminnav"' in body, url
        for label in ("Pregled", "Učenici", "Svi časovi", "Upiši čas"):
            assert label in body, (url, label)


def test_22_navigation_state_cannot_be_forced_by_the_client(anon, db):
    """`admin_authenticated` dolazi iz sesije — nikad iz upita ili polja."""
    for attempt in ("?admin_authenticated=1", "?admin_authenticated=True",
                    "?nav_active=classes&admin_authenticated=1"):
        body = anon.get(LOGIN + attempt).data.decode("utf-8")
        assert 'class="adminnav"' not in body, attempt
    forced = anon.post(LOGIN, data={"admin_authenticated": "1"})
    assert 'class="adminnav"' not in forced.data.decode("utf-8")


# ===========================================================================
# 23-24) KEŠIRANJE
# ===========================================================================
def test_23_admin_responses_are_never_stored_by_a_cache(admin, anon, db,
                                                        seeded):
    """Zapamćena administratorska stranica je i curenje i izvor one zbrke."""
    for url in ("/admin/reports", "/admin/students",
                "/admin/sessions", "/admin/sessions/new?grade=%d" % GRADE,
                "/admin/sessions/%d" % seeded["class_id"]):
        response = admin.get(url)
        assert response.headers.get("Cache-Control") == "no-store", url
    for url in (LOGIN, "/admin/sessions"):
        assert anon.get(url).headers.get("Cache-Control") == "no-store", url


def test_24_the_learner_app_is_not_affected(client, db):
    """`no-store` je SAMO za /admin — učenička stranica se ne dira."""
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers.get("Cache-Control") != "no-store"


# ===========================================================================
# 25) ADMIN ISKLJUČEN KONFIGURACIJOM
# ===========================================================================
def test_25_without_a_password_the_admin_does_not_exist(flask_app, monkeypatch,
                                                        db):
    monkeypatch.setenv("MATBOT_ADMIN_PASSWORD", "")
    fresh = flask_app.test_client()
    assert fresh.get(LOGIN).status_code == 404
    for path in ("/admin/reports", "/admin/sessions", "/admin/students"):
        assert fresh.get(path).status_code == 404, path
    assert fresh.post("/admin/sessions/bulk", data={}).status_code == 404
