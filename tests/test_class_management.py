"""Čas kao OBJEKAT: pregled, otvaranje, izmjena i brisanje (šema v6).

DVA IZMJERENA NEDOSTATKA MODELA v5 KOJA OVAJ FAJL ČUVA:

1. RAZRED ČASA SE NIGDJE NIJE ČUVAO. `student_sessions` nema kolonu razreda, pa
   se razred održanog časa mogao saznati samo iz `students.grade` — TEKUĆEG,
   promjenjivog. Izmjereno: učenik upisan na septembarski čas sedmog razreda,
   poslije promocije u osmi, prikazivao bi taj isti čas kao čas OSMOG razreda.

2. DVA STVARNA ČASA SU SE SPAJALA. Identitet je bio (datum, vrijeme, oblast,
   lekcija) BEZ razreda: sedmi i osmi razred s temom „Uvodni čas" istog dana u
   17:00 davali su JEDAN red — a brisanje „tog časa" obrisalo bi oba razreda.

Zato v6 uvodi `class_sessions` sa stabilnim `id` i NEPROMJENJIVIM razredom, a
`student_sessions.class_session_id` veže djecu za roditelja.

PII: svi učenici su sintetički.
"""
import re

import pytest

from matbot import (report_facts, report_input, reporting_db, reporting_schema,
                    student_sessions)

from tests.test_thinkific_progress_import import (build_v1, migrate,
                                                  migrate_v5_only)

libsql = pytest.importorskip("libsql")

PASSWORD = "administratorska-lozinka-123"

DATE = "2026-09-01"
MORNING = "10:00"
AFTERNOON = "14:00"
GRADE = 7
AREA = "Cijeli brojevi"
LESSON = "Skup cijelih brojeva Z"
OTHER_LESSON = "Prikaz na brojevnoj pravoj"
CUSTOM = "Uvodni čas"


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


def student(db, name, grade=GRADE):
    return db.create_student(name, grade)


def present(activity="4", homework="done", comment=None):
    fields = {"participation": "present", "activity": activity,
              "homework": homework}
    if comment:
        fields["comment"] = comment
    return fields


def save_class(admin, rows, *, date=DATE, time=MORNING, grade=GRADE, area=AREA,
               lesson=LESSON, mode="curriculum", class_id=None):
    """Upiši/uredi čas kroz STVARNE rute. Vraća identitet časa."""
    from urllib.parse import quote

    page = admin.get("/admin/sessions/new?grade=%s&session_date=%s"
                     "&session_time=%s&topic_mode=%s&area_name=%s&lesson_name=%s"
                     % (grade, date, quote(time), mode, quote(area),
                        quote(lesson)))
    data = {"csrf_token": csrf_from(page), "session_date": date,
            "session_time": time, "grade": str(grade), "topic_mode": mode,
            "area_name": area, "lesson_name": lesson}
    if class_id:
        data["class_id"] = str(class_id)
    for sid, fields in rows.items():
        for key, value in fields.items():
            data["s%d_%s" % (sid, key)] = value
    answer = admin.post("/admin/sessions/bulk", data=data)
    match = re.search(r"/admin/sessions/(\d+)", answer.headers.get("Location", ""))
    return int(match.group(1)) if match else None


def results_of(response):
    """Samo dio stranice s REZULTATIMA.

    Padajući meniji nose sve razrede kao opcije, pa pretraga po cijelom HTML-u
    ne bi mjerila filter nego postojanje filtera."""
    body = response.data.decode("utf-8")
    return body.split("Ukupno časova", 1)[-1] if "Ukupno časova" in body else ""


def class_rows(db, class_id):
    conn = db._connection()
    return conn.execute(
        "SELECT COUNT(*) FROM student_sessions WHERE class_session_id = ?",
        (int(class_id),)).fetchall()[0][0]


# ===========================================================================
# 1-2) NAVIGACIJA I PRISTUP
# ===========================================================================
def test_1_the_admin_navigation_reaches_every_destination(admin, db):
    body = admin.get("/admin/reports").data
    for label in ("Pregled", "Učenici", "Svi časovi", "Izvještaji", "Thinkific",
                  "Upiši čas"):
        assert label.encode() in body, label
    # Brze akcije na naslovnoj.
    assert "Brze akcije".encode() in body


def test_2_the_class_list_requires_admin_auth(client, db, admin_env):
    for path in ("/admin/sessions", "/admin/sessions/1",
                 "/admin/sessions/1/delete"):
        answer = client.get(path)
        assert answer.status_code in (302, 401, 403), path


# ===========================================================================
# 3-5) JEDAN RED PO ČASU, PORE­DAK, PODRAZUMIJEVANI MJESEC
# ===========================================================================
def test_3_one_row_per_class_not_per_student(admin, db):
    ids = [student(db, "U%d" % i) for i in range(4)]
    save_class(admin, {sid: present() for sid in ids})

    body = admin.get("/admin/sessions?month=2026-09").data.decode("utf-8")
    assert body.count('href="/admin/sessions/1"') >= 1
    # Četiri učenika, ali JEDAN čas.
    assert body.count("Pregled") == 1 or "Ukupno časova: <b>1</b>" in body
    assert "Ukupno časova: <b>1</b>" in body


def test_4_newest_class_first(admin, db):
    a = student(db, "A")
    save_class(admin, {a: present()}, date="2026-09-01", time=MORNING)
    save_class(admin, {a: present()}, date="2026-09-03", time=MORNING)
    save_class(admin, {a: present()}, date="2026-09-03", time=AFTERNOON)

    body = admin.get("/admin/sessions?month=2026-09").data.decode("utf-8")
    order = [m for m in re.findall(r"2026-09-\d\d|\d\d:\d\d", body)
             if m.startswith("2026") or m in (MORNING, AFTERNOON)]
    # Najsvježiji datum prvi, pa najsvježije vrijeme.
    assert order[0] == "2026-09-03"
    assert order.index("14:00") < order.index("10:00")


def test_5_the_current_month_is_the_default(admin, db):
    import datetime

    month = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")
    body = admin.get("/admin/sessions").data.decode("utf-8")
    assert 'value="%s"' % month in body


# ===========================================================================
# 6-9) FILTERI
# ===========================================================================
def test_6_month_filter(admin, db):
    a = student(db, "A")
    save_class(admin, {a: present()}, date="2026-09-01")
    save_class(admin, {a: present()}, date="2026-08-01")

    september = admin.get("/admin/sessions?month=2026-09").data.decode("utf-8")
    august = admin.get("/admin/sessions?month=2026-08").data.decode("utf-8")
    assert "2026-09-01" in september and "2026-08-01" not in september
    assert "2026-08-01" in august and "2026-09-01" not in august


def test_7_grade_filter(admin, db):
    seventh = student(db, "Sedmak", 7)
    eighth = student(db, "Osmak", 8)
    save_class(admin, {seventh: present()}, grade=7)
    save_class(admin, {eighth: present()}, grade=8, area="Cijeli brojevi",
               lesson="Skup cijelih brojeva Z", time=AFTERNOON)

    results = results_of(admin.get("/admin/sessions?month=2026-09&grade=7"))
    assert "7. razred" in results and "8. razred" not in results


def test_8_topic_source_filter(admin, db):
    a = student(db, "A")
    save_class(admin, {a: present()})
    save_class(admin, {a: present()}, mode="custom", area="", lesson=CUSTOM,
               time=AFTERNOON)

    plan = admin.get("/admin/sessions?month=2026-09"
                     "&topic_source=curriculum").data.decode("utf-8")
    manual = admin.get("/admin/sessions?month=2026-09"
                       "&topic_source=custom").data.decode("utf-8")
    assert LESSON in plan and CUSTOM not in plan
    assert CUSTOM in manual and LESSON not in manual


def test_9_topic_search(admin, db):
    a = student(db, "A")
    save_class(admin, {a: present()})
    save_class(admin, {a: present()}, lesson=OTHER_LESSON, time=AFTERNOON)

    body = admin.get("/admin/sessions?month=2026-09&q=brojevnoj").data.decode("utf-8")
    assert OTHER_LESSON in body and LESSON not in body


# ===========================================================================
# 10-12) SAŽETAK I PRIKAZ
# ===========================================================================
def test_10_attendance_summary_counts_only_persisted_rows(admin, db):
    ids = [student(db, "U%d" % i) for i in range(4)]
    rows = {ids[0]: present(), ids[1]: present(), ids[2]: {"participation": "absent"},
            ids[3]: {"participation": "not_scheduled"}}
    save_class(admin, rows)

    body = admin.get("/admin/sessions?month=2026-09").data.decode("utf-8")
    # „Nije na ovom času" NEMA red, pa nije ni u imeniocu.
    assert "2 prisutnih · 1 odsutnih" in body


def test_11_and_12_curriculum_and_custom_classes_are_labelled(admin, db):
    a = student(db, "A")
    save_class(admin, {a: present()})
    save_class(admin, {a: present()}, mode="custom", area="", lesson=CUSTOM,
               time=AFTERNOON)

    body = admin.get("/admin/sessions?month=2026-09").data.decode("utf-8")
    assert LESSON in body and "Plan" in body
    assert CUSTOM in body and "Ručni unos" in body


# ===========================================================================
# 13-17) PREGLED ČASA
# ===========================================================================
def test_13_to_16_the_detail_page_shows_exactly_this_class(admin, db):
    here = student(db, "Prisutan Ucenik")
    away = student(db, "Odsutan Ucenik")
    elsewhere = student(db, "Druga Grupa")
    class_id = save_class(admin, {here: present(activity="4",
                                                comment="dobar rad"),
                                  away: {"participation": "absent"},
                                  elsewhere: {"participation": "not_scheduled"}})

    body = admin.get("/admin/sessions/%d" % class_id).data.decode("utf-8")
    assert "Prisutan Ucenik" in body and "Odsutan Ucenik" in body
    assert "Druga Grupa" not in body, "učenik koji nije na času je prikazan"
    assert "7. razred" in body and DATE in body and MORNING in body
    # 15) kanonske oznake aktivnosti; aktivnost se NIKAD ne zove ocjenom —
    # jedina dozvoljena pojava te riječi je napomena da to NIJE ocjena.
    assert "4 — aktivan i uglavnom samostalan" in body
    for row in re.findall(r"<td>([^<]*)</td>", body):
        assert "ocjen" not in row.lower(), row
    assert "ne ocjena iz matematike" in body
    # 16) odsutan nema aktivnost
    assert "Odsutan" in body
    assert "dobar rad" in body


def test_17_the_detail_page_offers_edit_and_delete(admin, db):
    a = student(db, "A")
    class_id = save_class(admin, {a: present()})
    body = admin.get("/admin/sessions/%d" % class_id).data.decode("utf-8")
    assert 'href="/admin/sessions/new?class_id=%d"' % class_id in body
    assert "/admin/sessions/%d/delete" % class_id in body
    assert "Nazad na sve časove" in body


# ===========================================================================
# 18-19) IZMJENA
# ===========================================================================
def test_18_edit_loads_the_correct_class(admin, db):
    a = student(db, "A")
    b = student(db, "B")
    first = save_class(admin, {a: present(activity="4", comment="prvi cas")})
    second = save_class(admin, {b: present(activity="2", comment="drugi cas")},
                        time=AFTERNOON)

    body = admin.get("/admin/sessions/new?class_id=%d" % first).data.decode("utf-8")
    assert "prvi cas" in body and "drugi cas" not in body
    assert 'value="%s"' % MORNING in body


def test_19_editing_one_class_does_not_change_another(admin, db):
    a = student(db, "A")
    b = student(db, "B")
    first = save_class(admin, {a: present(activity="4")})
    second = save_class(admin, {b: present(activity="2")}, time=AFTERNOON)

    save_class(admin, {b: present(activity="5")}, time=AFTERNOON,
               class_id=second)

    assert db.fetch_sessions(a)[0]["activity_rating"] == 4
    assert db.fetch_sessions(b)[0]["activity_rating"] == 5
    assert len(db.fetch_sessions(a)) == 1 and len(db.fetch_sessions(b)) == 1


def test_19b_removing_a_student_during_edit_deletes_only_that_row(admin, db):
    a = student(db, "Ostaje")
    b = student(db, "Uklonjen")
    class_id = save_class(admin, {a: present(), b: present()})
    assert class_rows(db, class_id) == 2

    save_class(admin, {a: present(), b: {"participation": "not_scheduled"}},
               class_id=class_id)
    assert class_rows(db, class_id) == 1
    assert db.fetch_sessions(b) == []
    assert len(db.fetch_sessions(a)) == 1


# ===========================================================================
# 20-23) BRISANJE
# ===========================================================================
def test_20_delete_never_happens_on_get(admin, db):
    a = student(db, "A")
    class_id = save_class(admin, {a: present()})
    # GET je SAMO potvrda; ništa se ne mijenja.
    page = admin.get("/admin/sessions/%d/delete" % class_id)
    assert page.status_code == 200
    assert "Obrisati ovaj čas?".encode() in page.data
    assert db.fetch_class(class_id) is not None
    assert class_rows(db, class_id) == 1


def test_21_delete_requires_csrf(admin, db):
    a = student(db, "A")
    class_id = save_class(admin, {a: present()})
    answer = admin.post("/admin/sessions/%d/delete" % class_id)
    assert answer.status_code == 400
    assert db.fetch_class(class_id) is not None


def test_21b_delete_requires_admin_auth(client, db, admin_env, admin):
    a = student(db, "A")
    class_id = save_class(admin, {a: present()})
    anon = client.application.test_client()
    answer = anon.post("/admin/sessions/%d/delete" % class_id)
    assert answer.status_code in (302, 401, 403)
    assert db.fetch_class(class_id) is not None


def test_22_delete_removes_exactly_one_class(admin, db):
    """Dva časa iste teme istog dana, različito vrijeme — briše se samo jedan."""
    a = student(db, "A")
    b = student(db, "B")
    morning = save_class(admin, {a: present()}, time=MORNING)
    afternoon = save_class(admin, {b: present()}, time=AFTERNOON)

    page = admin.get("/admin/sessions/%d/delete" % morning)
    admin.post("/admin/sessions/%d/delete" % morning,
               data={"csrf_token": csrf_from(page)})

    assert db.fetch_class(morning) is None
    assert db.fetch_class(afternoon) is not None
    assert db.fetch_sessions(a) == []
    assert len(db.fetch_sessions(b)) == 1, "obrisan je tuđi čas"


def test_22b_delete_is_safe_across_grades_with_the_same_custom_topic(admin, db):
    """Sedmi i osmi razred, isti dan, isto vrijeme, ista ručna tema."""
    seventh = student(db, "Sedmak", 7)
    eighth = student(db, "Osmak", 8)
    first = save_class(admin, {seventh: present()}, grade=7, mode="custom",
                       area="", lesson=CUSTOM, time="17:00")
    second = save_class(admin, {eighth: present()}, grade=8, mode="custom",
                        area="", lesson=CUSTOM, time="17:00")
    assert first != second, "dva razreda su spojena u jedan čas"

    page = admin.get("/admin/sessions/%d/delete" % first)
    admin.post("/admin/sessions/%d/delete" % first,
               data={"csrf_token": csrf_from(page)})

    assert db.fetch_class(first) is None
    assert db.fetch_class(second) is not None
    assert db.fetch_sessions(seventh) == []
    assert len(db.fetch_sessions(eighth)) == 1


def test_23_delete_is_atomic_over_all_children(admin, db):
    ids = [student(db, "U%d" % i) for i in range(8)]
    class_id = save_class(admin, {sid: present() for sid in ids})
    assert class_rows(db, class_id) == 8

    page = admin.get("/admin/sessions/%d/delete" % class_id)
    admin.post("/admin/sessions/%d/delete" % class_id,
               data={"csrf_token": csrf_from(page)})

    assert class_rows(db, class_id) == 0
    assert db.fetch_class(class_id) is None
    for sid in ids:
        assert db.fetch_sessions(sid) == []


class _FailingOnParentDelete:
    """Omotač konekcije koji pukne TAČNO na brisanju roditelja.

    Konekcija je C objekat i njeni atributi su samo za čitanje, pa se ne može
    zakrpiti — omotava se."""

    def __init__(self, inner):
        self._inner = inner

    def execute(self, sql, *args, **kwargs):
        if sql.strip().upper().startswith("DELETE FROM CLASS_SESSIONS"):
            raise RuntimeError("simulirani pad")
        return self._inner.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_23b_a_failing_delete_rolls_back_completely(admin, db, monkeypatch):
    """Pad u sredini ne smije ostaviti pola časa."""
    ids = [student(db, "U%d" % i) for i in range(8)]
    class_id = save_class(admin, {sid: present() for sid in ids})
    assert class_rows(db, class_id) == 8

    wrapped = _FailingOnParentDelete(db._connection())
    monkeypatch.setattr(db, "_connection", lambda: wrapped)
    with pytest.raises(reporting_db.ReportingUnavailable):
        db.delete_class(class_id)
    monkeypatch.undo()

    assert db.fetch_class(class_id) is not None, "roditelj je nestao"
    assert class_rows(db, class_id) == 8, "djeca su djelimično obrisana"


# ===========================================================================
# 24-25) IZVJEŠTAJ
# ===========================================================================
def test_24_deleting_a_class_removes_exactly_its_evidence(admin, db):
    a = student(db, "A")
    first = save_class(admin, {a: present(activity="4", homework="done")},
                       date="2026-09-01", time=MORNING)
    save_class(admin, {a: present(activity="2", homework="not_done")},
               date="2026-09-02", time=MORNING)

    before = report_input.build_instruction_section(a, "2026-09", database=db)
    assert before["sessions_total"] == 2
    assert before["activity"]["average"] == 3.0

    page = admin.get("/admin/sessions/%d/delete" % first)
    admin.post("/admin/sessions/%d/delete" % first,
               data={"csrf_token": csrf_from(page)})

    after = report_input.build_instruction_section(a, "2026-09", database=db)
    assert after["sessions_total"] == 1
    assert after["present_count"] == 1
    assert after["activity"]["average"] == 2.0
    assert after["homework"]["done_count"] == 0
    assert after["homework"]["not_done_count"] == 1


def test_24b_editing_a_class_changes_future_report_facts(admin, db):
    a = student(db, "A")
    class_id = save_class(admin, {a: present(activity="2", homework="not_done")})
    before = report_input.build_instruction_section(a, "2026-09", database=db)
    assert before["activity"]["average"] == 2.0

    save_class(admin, {a: present(activity="5", homework="done")},
               class_id=class_id)

    after = report_input.build_instruction_section(a, "2026-09", database=db)
    assert after["activity"]["average"] == 5.0
    assert after["homework"]["done_count"] == 1
    assert after["sessions_total"] == 1, "izmjena je napravila drugi čas"


def test_25_already_saved_reports_stay_immutable(admin, db):
    a = student(db, "A")
    class_id = save_class(admin, {a: present(activity="4")})
    db.save_monthly_report(student_id=a, report_month="2026-09",
                           metrics_json='{"sessions": 1}',
                           ai_summary="stari tekst",
                           instructor_comment="komentar")
    before = db.fetch_monthly_report(a, "2026-09")

    page = admin.get("/admin/sessions/%d/delete" % class_id)
    admin.post("/admin/sessions/%d/delete" % class_id,
               data={"csrf_token": csrf_from(page)})

    after = db.fetch_monthly_report(a, "2026-09")
    assert after["ai_summary"] == before["ai_summary"] == "stari tekst"
    assert after["metrics_json"] == before["metrics_json"]
    assert after["instructor_comment"] == "komentar"


# ===========================================================================
# 26-28) STABILAN ISTORIJSKI RAZRED
# ===========================================================================
def test_26_and_27_the_class_grade_survives_a_student_promotion(admin, db):
    """SRŽ v6: septembarski čas sedmog razreda ostaje sedmi i poslije promocije."""
    a = student(db, "Napredni", 7)
    class_id = save_class(admin, {a: present()}, grade=7)
    assert db.fetch_class(class_id)["grade"] == 7

    db.set_student_grade(a, 8)          # promovisan u osmi

    assert db.fetch_class(class_id)["grade"] == 7, "istorija je falsifikovana"
    body = admin.get("/admin/sessions/%d" % class_id).data.decode("utf-8")
    assert "7. razred" in body and "8. razred" not in body
    listing = admin.get("/admin/sessions?month=2026-09").data.decode("utf-8")
    assert "7. razred" in listing


def test_28_the_class_grade_is_never_read_from_the_current_profile():
    """Razred časa se čita iz `class_sessions`, ne iz `students`."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "matbot"
              / "admin_sessions.py").read_text(encoding="utf-8")
    body = source.split("def class_detail(")[1].split("\n@admin_sessions_bp")[0]
    assert "fetch_student_profile" not in body
    assert "fetch_class(" in body


def test_28b_two_grades_with_the_same_topic_are_two_classes(admin, db):
    """Nalaz 2: bez razreda u identitetu ova dva časa bi bila jedan red."""
    seventh = student(db, "Sedmak", 7)
    eighth = student(db, "Osmak", 8)
    first = save_class(admin, {seventh: present()}, grade=7, mode="custom",
                       area="", lesson=CUSTOM, time="17:00")
    second = save_class(admin, {eighth: present()}, grade=8, mode="custom",
                        area="", lesson=CUSTOM, time="17:00")

    assert first != second
    body = admin.get("/admin/sessions?month=2026-09").data.decode("utf-8")
    assert "Ukupno časova: <b>2</b>" in body


# ===========================================================================
# 29-31) MOBILNI PRIKAZ, PRAZNA STANJA, ZATEČENI REDOVI
# ===========================================================================
def test_29_the_list_renders_mobile_cards_as_well_as_a_table(admin, db):
    a = student(db, "A")
    save_class(admin, {a: present()})
    body = admin.get("/admin/sessions?month=2026-09").data.decode("utf-8")
    assert 'class="cls-card"' in body      # kartica za mobitel
    assert "<table" in body                # tabela za desktop
    assert "@media (max-width:720px)" in body


def test_30_empty_states_offer_the_next_action(admin, db):
    empty = admin.get("/admin/sessions?month=2026-09").data.decode("utf-8")
    assert "Još nema evidentiranih časova" in empty
    assert "Upiši prvi čas" in empty

    a = student(db, "A")
    save_class(admin, {a: present()})
    filtered = admin.get("/admin/sessions?month=2026-09&q=nepostojeca").data.decode("utf-8")
    assert "Nema časova za odabrane filtere" in filtered
    assert "Očisti filtere" in filtered


def test_31_legacy_rows_without_a_class_are_left_alone(admin, db):
    """Zatečen red nema objekat časa — i ne izmišlja mu se."""
    a = student(db, "Sa Istorijom")
    conn = db._connection()
    conn.execute(
        "INSERT INTO student_sessions (student_id, session_date, attendance, "
        " activity_rating, homework_status, area_name, lesson_name, comment) "
        "VALUES (?, '2026-09-20', 'present', 3, 'done', ?, ?, 'lanjski')",
        (a, AREA, LESSON))
    conn.commit()

    stored = db.fetch_sessions(a)
    assert len(stored) == 1
    assert stored[0]["class_session_id"] is None
    assert stored[0]["session_time"] is None
    # I dalje se broji u mjesečnom sažetku.
    summary = report_input.build_instruction_section(a, "2026-09", database=db)
    assert summary["sessions_total"] == 1
    # Ali NE pravi lažan red u pregledu časova.
    body = admin.get("/admin/sessions?month=2026-09").data.decode("utf-8")
    assert "Još nema evidentiranih časova" in body


# ===========================================================================
# 32-37) NEPROMIJENJENO OKRUŽENJE
# ===========================================================================
def test_32_current_grade_authority_is_unchanged(admin, db):
    seventh = student(db, "Sedmak", 7)
    eighth = student(db, "Osmak", 8)
    before = db.fetch_student_profile(seventh)
    class_id = save_class(admin, {seventh: present(), eighth: present()})
    assert class_id is None, "podmetnut osmoškolac nije odbio čas"
    assert db.fetch_student_profile(seventh) == before


def test_33_custom_topics_remain_non_curriculum_in_reporting(admin, db):
    a = student(db, "A")
    save_class(admin, {a: present()}, mode="custom", area="", lesson=CUSTOM)
    summary = report_input.build_instruction_section(a, "2026-09", database=db)
    assert summary["lessons_worked"] == []
    assert summary["custom_topics"] == [CUSTOM]

    facts = report_facts.build_ai_facts(
        report_input.build_report_input(a, "2026-09", database=db))
    import json

    assert CUSTOM not in json.dumps(facts, ensure_ascii=False)


def test_34_the_exact_session_time_stays_out_of_the_ai_facts(admin, db):
    a = student(db, "A")
    save_class(admin, {a: present()}, time=AFTERNOON)
    facts = report_facts.build_ai_facts(
        report_input.build_report_input(a, "2026-09", database=db))
    import json

    blob = json.dumps(facts, ensure_ascii=False)
    assert AFTERNOON not in blob and "session_time" not in blob


def test_35_the_learner_ui_is_untouched():
    from pathlib import Path

    html = (Path(__file__).resolve().parent.parent / "templates"
            / "index.html").read_text(encoding="utf-8")
    assert "Mala pomoć" in html and "Daj mi hint" not in html
    assert "MAT-BOT može pogriješiti. Provjeri važne informacije." in html


def test_36_thinkific_import_is_untouched(admin, db):
    body = admin.get("/admin/reports").data.decode("utf-8")
    assert "Thinkific" in body
    from matbot import report_input as ri

    assert hasattr(ri, "import_progress_files")


def test_37_no_model_call_is_involved(admin, db, flask_app):
    fake = flask_app.config["MATBOT_LLM"]
    before = len(getattr(fake, "calls", []) or [])
    a = student(db, "A")
    class_id = save_class(admin, {a: present()})
    admin.get("/admin/sessions?month=2026-09")
    admin.get("/admin/sessions/%d" % class_id)
    page = admin.get("/admin/sessions/%d/delete" % class_id)
    admin.post("/admin/sessions/%d/delete" % class_id,
               data={"csrf_token": csrf_from(page)})
    assert len(getattr(fake, "calls", []) or []) == before


def test_37b_the_report_prompt_version_is_unchanged():
    from matbot import report_prompt

    assert report_prompt.REPORT_PROMPT_VERSION == "3d-2"


# ===========================================================================
# 38) MIGRACIJA v5 → v6
# ===========================================================================
def test_38_v5_to_v6_is_additive_idempotent_and_verified(tmp_path):
    path = str(tmp_path / "v5.db")
    build_v1(path)
    migrate_v5_only(path)
    conn = libsql.connect(path)
    conn.execute("INSERT INTO students (display_name, grade) VALUES ('A', 7)")
    conn.execute(
        "INSERT INTO student_sessions (student_id, session_date, session_time, "
        " attendance, activity_rating, homework_status, area_name, lesson_name) "
        "VALUES (1, '2026-05-01', '10:00', 'present', 4, 'done', ?, ?)",
        (AREA, LESSON))
    conn.commit()
    columns = ("id, student_id, session_date, session_time, attendance, "
               "activity_rating, homework_status, area_name, lesson_name")
    before = [tuple(r) for r in conn.execute(
        "SELECT %s FROM student_sessions ORDER BY id" % columns).fetchall()]

    assert reporting_schema.migrate_to_v6(conn) is True
    assert reporting_schema.verify_v6_schema(conn) == []
    assert reporting_schema.migrate_to_v6(conn) is False

    after = [tuple(r) for r in conn.execute(
        "SELECT %s FROM student_sessions ORDER BY id" % columns).fetchall()]
    assert after == before, "migracija je prepisala zatečene redove"
    # NIŠTA SE NE POPUNJAVA UNAZAD: razred tog časa se ne može pouzdano znati.
    assert [tuple(r) for r in conn.execute(
        "SELECT class_session_id FROM student_sessions").fetchall()] == [(None,)]
    assert reporting_schema.applied_versions(conn) >= {1, 2, 3, 4, 5, 6}
    conn.close()


def test_38b_a_malformed_partial_v6_fails_closed(tmp_path):
    path = str(tmp_path / "broken.db")
    build_v1(path)
    migrate_v5_only(path)
    conn = libsql.connect(path)
    conn.execute("ALTER TABLE student_sessions ADD COLUMN class_session_id "
                 "INTEGER NOT NULL DEFAULT 0")
    conn.commit()
    with pytest.raises(reporting_schema.MigrationError) as caught:
        reporting_schema.migrate_to_v6(conn)
    assert caught.value.code.startswith("v6_child_not_nullable")
    assert 6 not in reporting_schema.applied_versions(conn)
    conn.close()


def test_38c_a_recorded_v6_without_the_table_fails_closed(tmp_path):
    path = str(tmp_path / "lying.db")
    build_v1(path)
    migrate_v5_only(path)
    conn = libsql.connect(path)
    conn.execute("INSERT INTO schema_migrations (version, description) "
                 "VALUES (6, 'lazni zapis')")
    conn.commit()
    with pytest.raises(reporting_schema.MigrationError) as caught:
        reporting_schema.migrate_to_v6(conn)
    assert caught.value.code.startswith("v6_")
    conn.close()


def test_38d_v6_refuses_to_run_before_v5(tmp_path):
    from tests.test_thinkific_progress_import import migrate_v4_only

    path = str(tmp_path / "v4.db")
    build_v1(path)
    migrate_v4_only(path)
    conn = libsql.connect(path)
    with pytest.raises(reporting_schema.MigrationError) as caught:
        reporting_schema.migrate_to_v6(conn)
    assert caught.value.code == "v5_migration_record_missing"
    conn.close()


def test_38e_version_row_written_only_after_verification():
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "matbot"
              / "reporting_schema.py").read_text(encoding="utf-8")
    body = source.split("def migrate_to_v6(")[1].split("\ndef ")[0]
    assert body.index("_apply_v6_ddl(conn)") < body.rindex("verify_v6_schema(conn)")
    assert body.rindex("verify_v6_schema(conn)") < body.index("_record_migration(")
