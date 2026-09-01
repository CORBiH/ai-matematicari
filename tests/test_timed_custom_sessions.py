"""Faza 3D+ — VRIJEME ČASA i RUČNA TEMA (šema v5).

DVA STVARNA OPERATIVNA ZAHTJEVA KOJA MODEL PODATAKA NIJE PODRŽAVAO.

1. VIŠE GRUPA ISTOG RAZREDA ISTOG DANA. Instruktor drži sedmi razred u 10:00 i
   opet u 14:00, ponekad NAD ISTOM lekcijom. Logički identitet časa je do sada
   bio (učenik, datum, oblast, lekcija), pa su se ta dva stvarna časa SUDARALA —
   drugo čuvanje je pregazilo prvo. To je bila zapisana granica
   `KNOWN_SAME_DAY_SAME_LESSON_SESSION_LIMITATION`; ovaj fajl je zatvara za sve
   NOVE zapise.

2. NIJE SVAKI ČAS LEKCIJA IZ PLANA. „Uvodni čas", „Ponavljanje", „Priprema za
   kontrolni", „Konsultacije" su stvarni časovi kojih u `topics.json` nema.
   Do sada su morali biti gurnuti u izmišljenu kurikularnu lekciju ili nikako.

TREĆA TVRDNJA, JEDNAKO VAŽNA: ručna tema JESTE čas — broji se u prisustvu,
angažmanu i zadaći — ali NIJE gradivo iz plana i ne smije završiti u izvještaju
roditelju kao lekcija koju treba uvježbati.

PII: svi učenici su sintetički.
"""
import re

import pytest

from matbot import (class_entry, report_facts, report_input, reporting_db,
                    reporting_schema, student_sessions)

from tests.test_thinkific_progress_import import (build_v1, migrate,
                                                  migrate_v4_only)

libsql = pytest.importorskip("libsql")

PASSWORD = "administratorska-lozinka-123"

DATE = "2026-09-01"
MORNING = "10:00"
AFTERNOON = "14:00"
GRADE = 7
AREA = "Cijeli brojevi"
LESSON = "Skup cijelih brojeva Z"
CUSTOM_TOPIC = "Uvodni čas"


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


def confirmed(db, name, grade=GRADE):
    return db.create_student(name, grade)


def form(rows, *, date=DATE, time=MORNING, grade=GRADE, area=AREA,
         lesson=LESSON, mode="curriculum", token=None, previous_time=None):
    data = {"session_date": date, "session_time": time, "grade": str(grade),
            "topic_mode": mode, "area_name": area, "lesson_name": lesson}
    if token:
        data["csrf_token"] = token
    if previous_time:
        data["previous_session_time"] = previous_time
    for student_id, fields in rows.items():
        for key, value in fields.items():
            data["s%d_%s" % (student_id, key)] = value
    return data


def page(admin, *, time=MORNING, area=AREA, lesson=LESSON, mode="curriculum",
         grade=GRADE, date=DATE):
    from urllib.parse import quote

    return admin.get("/admin/sessions/new?grade=%s&session_date=%s"
                     "&session_time=%s&topic_mode=%s&area_name=%s&lesson_name=%s"
                     % (grade, date, quote(time), mode, quote(area),
                        quote(lesson)))


def present(activity="4", homework="done", comment=None):
    fields = {"participation": "present", "activity": activity,
              "homework": homework}
    if comment:
        fields["comment"] = comment
    return fields


def sessions(db, student_id):
    return db.fetch_sessions(student_id)


# ===========================================================================
# 1-4) VRIJEME POSTOJI, VALIDIRA SE I OBAVEZNO JE
# ===========================================================================
def test_1_the_entry_page_has_a_time_field(admin, db):
    confirmed(db, "Sedmak")
    body = admin.get("/admin/sessions/new").data
    assert b'name="session_time"' in body and b'type="time"' in body


@pytest.mark.parametrize("value", ["08:00", "09:30", "14:05", "23:00", "00:00"])
def test_2_valid_times_are_accepted(admin, db, value):
    student_id = confirmed(db, "Sedmak")
    token = csrf_from(page(admin, time=value))
    admin.post("/admin/sessions/bulk",
               data=form({student_id: present()}, time=value, token=token))
    stored = sessions(db, student_id)
    assert len(stored) == 1 and stored[0]["session_time"] == value


@pytest.mark.parametrize("value", ["24:00", "12:60", "9", "9h", "14.30", "abc",
                                   "25:00", "7:00", "07:0", "14:00:00"])
def test_3_invalid_times_are_rejected(admin, db, value):
    student_id = confirmed(db, "Sedmak")
    token = csrf_from(page(admin))
    admin.post("/admin/sessions/bulk",
               data=form({student_id: present()}, time=value, token=token))
    assert sessions(db, student_id) == [], value


def test_4_time_is_required_for_a_new_class(admin, db):
    student_id = confirmed(db, "Sedmak")
    token = csrf_from(page(admin))
    data = form({student_id: present()}, token=token)
    data["session_time"] = ""
    admin.post("/admin/sessions/bulk", data=data)
    assert sessions(db, student_id) == []


def test_4b_the_validator_refuses_a_timeless_new_record():
    with pytest.raises(student_sessions.SessionValidationError) as caught:
        student_sessions.validate_session(
            session_date=DATE, attendance="present", activity_rating=4,
            homework_status="done", area_name=AREA, lesson_name=LESSON,
            grade=GRADE, require_time=True)
    assert caught.value.code == "session_time_required"


# ===========================================================================
# 5-9) VRIJEME JE DIO IDENTITETA ČASA
# ===========================================================================
def test_5_same_date_same_lesson_different_time_are_distinct_classes(admin, db):
    a = confirmed(db, "Grupa A")
    b = confirmed(db, "Grupa B")
    token = csrf_from(page(admin))
    admin.post("/admin/sessions/bulk",
               data=form({a: present()}, time=MORNING, token=token))
    admin.post("/admin/sessions/bulk",
               data=form({b: present()}, time=AFTERNOON, token=token))

    assert sessions(db, a)[0]["session_time"] == MORNING
    assert sessions(db, b)[0]["session_time"] == AFTERNOON


def test_6_the_same_student_may_attend_two_classes_on_one_day(admin, db):
    """Zatvara `KNOWN_SAME_DAY_SAME_LESSON_SESSION_LIMITATION` za nove zapise."""
    student_id = confirmed(db, "Dva Casa")
    token = csrf_from(page(admin))
    admin.post("/admin/sessions/bulk",
               data=form({student_id: present(activity="4")}, time=MORNING,
                         token=token))
    admin.post("/admin/sessions/bulk",
               data=form({student_id: present(activity="5")}, time=AFTERNOON,
                         token=token))

    stored = sessions(db, student_id)
    assert len(stored) == 2, "dva stvarna časa su se sudarila"
    assert {r["session_time"] for r in stored} == {MORNING, AFTERNOON}
    assert {r["activity_rating"] for r in stored} == {4, 5}
    assert {r["lesson_name"] for r in stored} == {LESSON}


def test_7_repeated_save_at_the_same_time_updates_instead_of_duplicating(admin, db):
    student_id = confirmed(db, "Sedmak")
    token = csrf_from(page(admin))
    for _ in range(4):
        admin.post("/admin/sessions/bulk",
                   data=form({student_id: present(activity="3")}, token=token))
    stored = sessions(db, student_id)
    assert len(stored) == 1 and stored[0]["activity_rating"] == 3


def test_8_edit_loads_only_the_selected_time(admin, db):
    morning_student = confirmed(db, "Jutarnja Grupa")
    afternoon_student = confirmed(db, "Popodnevna Grupa")
    token = csrf_from(page(admin))
    admin.post("/admin/sessions/bulk",
               data=form({morning_student: present(comment="jutarnji zapis")},
                         time=MORNING, token=token))
    admin.post("/admin/sessions/bulk",
               data=form({afternoon_student: present(comment="popodnevni zapis")},
                         time=AFTERNOON, token=token))

    morning_page = page(admin, time=MORNING).data
    assert b"jutarnji zapis" in morning_page
    assert b"popodnevni zapis" not in morning_page

    afternoon_page = page(admin, time=AFTERNOON).data
    assert b"popodnevni zapis" in afternoon_page
    assert b"jutarnji zapis" not in afternoon_page


def test_9_editing_one_group_cannot_change_the_other(admin, db):
    a = confirmed(db, "Grupa A")
    b = confirmed(db, "Grupa B")
    token = csrf_from(page(admin))
    admin.post("/admin/sessions/bulk",
               data=form({a: present(activity="4")}, time=MORNING, token=token))
    admin.post("/admin/sessions/bulk",
               data=form({b: present(activity="2")}, time=AFTERNOON, token=token))

    # Popravka popodnevne grupe.
    admin.post("/admin/sessions/bulk",
               data=form({b: present(activity="5")}, time=AFTERNOON,
                         token=token, previous_time=AFTERNOON))

    assert sessions(db, a)[0]["activity_rating"] == 4, "jutarnja grupa je izmijenjena"
    assert sessions(db, b)[0]["activity_rating"] == 5
    assert len(sessions(db, b)) == 1


def test_9b_changing_the_time_moves_the_class_instead_of_duplicating(admin, db):
    student_id = confirmed(db, "Pomjeren Cas")
    token = csrf_from(page(admin))
    admin.post("/admin/sessions/bulk",
               data=form({student_id: present()}, time=MORNING, token=token))
    # Instruktor je pogriješio termin i ispravlja ga.
    admin.post("/admin/sessions/bulk",
               data=form({student_id: present()}, time="11:15", token=token,
                         previous_time=MORNING))

    stored = sessions(db, student_id)
    assert len(stored) == 1, "premještanje je napravilo dvojnik"
    assert stored[0]["session_time"] == "11:15"


def test_9c_moving_onto_an_occupied_slot_fails_closed(admin, db):
    """Spajanje dva stvarna časa bi tiho uništilo jedan — pa se odbija."""
    student_id = confirmed(db, "Dva Casa")
    token = csrf_from(page(admin))
    admin.post("/admin/sessions/bulk",
               data=form({student_id: present(activity="4")}, time=MORNING,
                         token=token))
    admin.post("/admin/sessions/bulk",
               data=form({student_id: present(activity="5")}, time=AFTERNOON,
                         token=token))

    answer = admin.post("/admin/sessions/bulk",
                        data=form({student_id: present(activity="1")},
                                  time=AFTERNOON, token=token,
                                  previous_time=MORNING))
    assert answer.status_code == 302

    stored = sorted(sessions(db, student_id), key=lambda r: r["session_time"])
    assert len(stored) == 2, "časovi su spojeni"
    assert [r["activity_rating"] for r in stored] == [4, 5], "podaci su promijenjeni"


# ===========================================================================
# 10-16) REŽIM TEME
# ===========================================================================
def test_10_curriculum_mode_still_validates_the_canonical_pair(admin, db):
    student_id = confirmed(db, "Sedmak")
    token = csrf_from(page(admin))
    for area, lesson in (("Djeljivost brojeva", "Djeljivost zbira, razlike i proizvoda"),
                         (AREA, "Izmisljena lekcija"),
                         ("Ugao i trougao", LESSON), ("", LESSON), (AREA, "")):
        admin.post("/admin/sessions/bulk",
                   data=form({student_id: present()}, area=area, lesson=lesson,
                             token=token))
    assert sessions(db, student_id) == []


def test_11_custom_mode_accepts_a_real_non_curriculum_topic(admin, db):
    student_id = confirmed(db, "Sedmak")
    token = csrf_from(page(admin, mode="custom", area="", lesson=CUSTOM_TOPIC))
    admin.post("/admin/sessions/bulk",
               data=form({student_id: present()}, mode="custom", area="",
                         lesson=CUSTOM_TOPIC, token=token))

    stored = sessions(db, student_id)
    assert len(stored) == 1
    assert stored[0]["lesson_name"] == CUSTOM_TOPIC
    assert stored[0]["topic_source"] == student_sessions.TOPIC_CUSTOM
    # Nikakva lažna vrijednost umjesto oblasti.
    assert stored[0]["area_name"] is None


@pytest.mark.parametrize("topic", ["Ponavljanje gradiva", "Priprema za kontrolni",
                                   "Analiza kontrolnog rada", "Konsultacije",
                                   "Rad na zadacima"])
def test_11b_every_real_world_custom_topic_is_accepted(admin, db, topic):
    student_id = confirmed(db, "Sedmak")
    token = csrf_from(page(admin, mode="custom", area="", lesson=topic))
    admin.post("/admin/sessions/bulk",
               data=form({student_id: present()}, mode="custom", area="",
                         lesson=topic, token=token))
    assert sessions(db, student_id)[0]["lesson_name"] == topic


def test_12_custom_mode_does_not_require_a_canonical_pair(admin, db):
    """Tema koje u `topics.json` NEMA prolazi — to je cijela poenta."""
    from matbot import topics

    assert not topics.curriculum_pair_valid(GRADE, "", CUSTOM_TOPIC)
    student_id = confirmed(db, "Sedmak")
    token = csrf_from(page(admin, mode="custom", area="", lesson=CUSTOM_TOPIC))
    admin.post("/admin/sessions/bulk",
               data=form({student_id: present()}, mode="custom", area="",
                         lesson=CUSTOM_TOPIC, token=token))
    assert len(sessions(db, student_id)) == 1


@pytest.mark.parametrize("topic", ["", "   ", "\t\n "])
def test_13_an_empty_custom_topic_is_rejected(admin, db, topic):
    student_id = confirmed(db, "Sedmak")
    token = csrf_from(page(admin, mode="custom", area="", lesson="x"))
    admin.post("/admin/sessions/bulk",
               data=form({student_id: present()}, mode="custom", area="",
                         lesson=topic, token=token))
    assert sessions(db, student_id) == []


def test_13b_an_over_long_custom_topic_is_rejected(admin, db):
    student_id = confirmed(db, "Sedmak")
    token = csrf_from(page(admin, mode="custom", area="", lesson="x"))
    too_long = "x" * (student_sessions.MAX_LABEL_CHARS + 1)
    admin.post("/admin/sessions/bulk",
               data=form({student_id: present()}, mode="custom", area="",
                         lesson=too_long, token=token))
    assert sessions(db, student_id) == []


def test_14_the_custom_area_is_optional_but_kept_when_given(admin, db):
    with_area = confirmed(db, "Sa Oblascu")
    token = csrf_from(page(admin, mode="custom", area="", lesson=CUSTOM_TOPIC))
    admin.post("/admin/sessions/bulk",
               data=form({with_area: present()}, mode="custom",
                         area="Priprema", lesson=CUSTOM_TOPIC, token=token))
    stored = sessions(db, with_area)[0]
    assert stored["area_name"] == "Priprema"
    assert stored["topic_source"] == student_sessions.TOPIC_CUSTOM


def test_15_custom_text_is_escaped_not_rendered(admin, db):
    """Predložak bježi sadržaj; oznake se ni ne primaju u naziv."""
    student_id = confirmed(db, "Sedmak")
    token = csrf_from(page(admin, mode="custom", area="", lesson="x"))
    admin.post("/admin/sessions/bulk",
               data=form({student_id: present()}, mode="custom", area="",
                         lesson="<script>alert(1)</script>", token=token))
    # `_clean_label` odbija `<`/`>` prije baze.
    assert sessions(db, student_id) == []

    admin.post("/admin/sessions/bulk",
               data=form({student_id: present()}, mode="custom", area="",
                         lesson="Ponavljanje & vježba", token=token))
    body = page(admin, mode="custom", area="", lesson="Ponavljanje & vježba").data
    # `&` je pobjegao u `&amp;` — predložak ne ispisuje sirov unos.
    assert b"Ponavljanje &amp; vje" in body
    # Podmetnuta skripta se NIGDJE ne odražava (stranica ima svoj `<script>`,
    # pa se traži baš teret, ne oznaka).
    assert b"alert(1)" not in body
    assert b"<script>alert" not in body


def test_16_the_topic_mode_cannot_be_forged_to_bypass_validation(admin, db):
    """Nepoznat režim pada na KURIKULARNI, koji je stroži."""
    student_id = confirmed(db, "Sedmak")
    token = csrf_from(page(admin))
    for forged in ("CURRICULUM", "customx", "none", "", "admin"):
        admin.post("/admin/sessions/bulk",
                   data=form({student_id: present()}, mode=forged, area="",
                             lesson=CUSTOM_TOPIC, token=token))
    assert sessions(db, student_id) == [], "podmetnut režim je zaobišao kurikulum"
    assert class_entry.clean_topic_mode("customx") == \
        student_sessions.TOPIC_CURRICULUM


def test_16b_curriculum_mode_cannot_smuggle_free_text(admin, db):
    student_id = confirmed(db, "Sedmak")
    token = csrf_from(page(admin))
    admin.post("/admin/sessions/bulk",
               data=form({student_id: present()}, mode="curriculum",
                         area="Izmisljena oblast", lesson="Izmisljena lekcija",
                         token=token))
    assert sessions(db, student_id) == []


# ===========================================================================
# 17-20) RUČNA TEMA U IZVJEŠTAJU
# ===========================================================================
def _custom_month(admin, db, student_id, token):
    plan = [("2026-09-01", "10:00", "present", "4", "done"),
            ("2026-09-02", "10:00", "present", "5", "not_done"),
            ("2026-09-03", "10:00", "absent", None, None)]
    for date, time, state, activity, homework in plan:
        fields = {"participation": state}
        if state == "present":
            fields["activity"] = activity
            fields["homework"] = homework
        admin.post("/admin/sessions/bulk",
                   data=form({student_id: fields}, date=date, time=time,
                             mode="custom", area="", lesson=CUSTOM_TOPIC,
                             token=token))


def test_17_to_19_custom_sessions_count_as_real_classroom_evidence(admin, db):
    student_id = confirmed(db, "Sedmak")
    token = csrf_from(page(admin, mode="custom", area="", lesson=CUSTOM_TOPIC))
    _custom_month(admin, db, student_id, token)

    summary = report_input.build_instruction_section(student_id, "2026-09",
                                                     database=db)
    assert summary["sessions_total"] == 3          # 17) prisustvo
    assert summary["present_count"] == 2
    assert summary["absent_count"] == 1
    assert summary["activity"]["average"] == 4.5   # 18) angažman
    assert summary["activity"]["rated_sessions"] == 2
    assert summary["homework"]["assigned_count"] == 2   # 19) zadaća
    assert summary["homework"]["done_count"] == 1


def test_20_a_custom_topic_never_becomes_a_mathematical_lesson(admin, db):
    student_id = confirmed(db, "Sedmak")
    token = csrf_from(page(admin, mode="custom", area="", lesson=CUSTOM_TOPIC))
    _custom_month(admin, db, student_id, token)

    summary = report_input.build_instruction_section(student_id, "2026-09",
                                                     database=db)
    # Ručna tema stoji ODVOJENO od kurikularnog gradiva.
    assert summary["lessons_worked"] == []
    assert summary["areas_worked"] == []
    assert summary["custom_topics"] == [CUSTOM_TOPIC]

    facts = report_facts.build_ai_facts(
        report_input.build_report_input(student_id, "2026-09", database=db))
    import json

    blob = json.dumps(facts, ensure_ascii=False)
    assert CUSTOM_TOPIC not in blob, "ručna tema je otišla modelu kao gradivo"
    assert facts["instruction"]["sessions_total"] == 3, "čas se ipak broji"


def test_20b_a_curriculum_session_still_reaches_the_model_as_a_lesson(admin, db):
    student_id = confirmed(db, "Sedmak")
    token = csrf_from(page(admin))
    admin.post("/admin/sessions/bulk",
               data=form({student_id: present()}, date="2026-09-01",
                         token=token))
    facts = report_facts.build_ai_facts(
        report_input.build_report_input(student_id, "2026-09", database=db))
    assert facts["instruction"]["lessons_worked"] == [LESSON]
    assert facts["instruction"]["areas_worked"] == [AREA]


def test_20c_legacy_rows_without_topic_source_stay_curriculum(db):
    """Zatečen red je bio kurikularno provjeren — ne prekvalifikuje se."""
    rows = [{"session_date": "2026-05-01", "attendance": "present",
             "activity_rating": 4, "homework_status": "done",
             "area_name": AREA, "lesson_name": LESSON, "comment": None,
             "topic_source": None}]
    summary = student_sessions.build_monthly_summary(rows)
    assert summary["lessons_worked"] == [LESSON]
    assert summary["custom_topics"] == []


# ===========================================================================
# 21-22) UGOVOR PREMA MODELU
# ===========================================================================
def test_21_raw_class_comments_stay_out_of_the_ai_facts(admin, db):
    student_id = confirmed(db, "Sedmak")
    token = csrf_from(page(admin))
    admin.post("/admin/sessions/bulk",
               data=form({student_id: present(comment="TAJNO ZAPAZANJE")},
                         date="2026-09-01", token=token))
    facts = report_facts.build_ai_facts(
        report_input.build_report_input(student_id, "2026-09", database=db))
    import json

    assert "TAJNO ZAPAZANJE" not in json.dumps(facts, ensure_ascii=False)


def test_22_the_exact_class_time_is_not_added_to_the_ai_payload(admin, db):
    """Vrijeme je OPERATIVNO. Roditelju ne treba „bio je na času u 14:00"."""
    student_id = confirmed(db, "Sedmak")
    token = csrf_from(page(admin))
    admin.post("/admin/sessions/bulk",
               data=form({student_id: present()}, date="2026-09-01",
                         time=AFTERNOON, token=token))
    facts = report_facts.build_ai_facts(
        report_input.build_report_input(student_id, "2026-09", database=db))
    import json

    blob = json.dumps(facts, ensure_ascii=False)
    assert AFTERNOON not in blob
    assert "session_time" not in blob
    # A u bazi vrijeme naravno postoji.
    assert sessions(db, student_id)[0]["session_time"] == AFTERNOON


# ===========================================================================
# 23-24) DVIJE GRUPE ISTOG RAZREDA ISTOG DANA
# ===========================================================================
def test_23_and_24_two_groups_same_date_same_lesson_stay_separate(admin, db):
    group_a = [confirmed(db, "A%d" % i) for i in range(3)]
    group_b = [confirmed(db, "B%d" % i) for i in range(3)]
    token = csrf_from(page(admin))

    admin.post("/admin/sessions/bulk",
               data=form({sid: present(activity="4") for sid in group_a},
                         time=MORNING, token=token))
    admin.post("/admin/sessions/bulk",
               data=form({sid: present(activity="2") for sid in group_b},
                         time=AFTERNOON, token=token))

    # Šest ispravnih redova, po jedan za svakog učenika.
    assert all(len(sessions(db, sid)) == 1 for sid in group_a + group_b)

    morning = page(admin, time=MORNING).data
    afternoon = page(admin, time=AFTERNOON).data
    for sid in group_a:
        assert ('id="a%d_4"' % sid).encode() in morning
    for sid in group_b:
        assert ('id="a%d_2"' % sid).encode() in afternoon

    # Izmjena grupe B ne dira grupu A.
    admin.post("/admin/sessions/bulk",
               data=form({sid: present(activity="5") for sid in group_b},
                         time=AFTERNOON, token=token, previous_time=AFTERNOON))
    assert all(sessions(db, sid)[0]["activity_rating"] == 4 for sid in group_a)
    assert all(sessions(db, sid)[0]["activity_rating"] == 5 for sid in group_b)

    # Izvještaj broji svakom svoje.
    for sid in group_a + group_b:
        summary = report_input.build_instruction_section(sid, "2026-09",
                                                         database=db)
        assert summary["sessions_total"] == 1 and summary["present_count"] == 1


# ===========================================================================
# 25-28) NEPROMIJENJENE ZAŠTITE
# ===========================================================================
def test_25_the_bulk_save_is_still_atomic(admin, db):
    good = confirmed(db, "Dobar Red")
    broken = confirmed(db, "Neispravan Red")
    token = csrf_from(page(admin))
    admin.post("/admin/sessions/bulk",
               data=form({good: present(),
                          broken: {"participation": "present"}}, token=token))
    assert sessions(db, good) == [] and sessions(db, broken) == []


def test_25b_an_invalid_time_aborts_before_the_first_write(admin, db):
    a = confirmed(db, "Prvi")
    b = confirmed(db, "Drugi")
    token = csrf_from(page(admin))
    admin.post("/admin/sessions/bulk",
               data=form({a: present(), b: present()}, time="25:00",
                         token=token))
    assert sessions(db, a) == [] and sessions(db, b) == []


def test_26_cross_grade_injection_is_still_rejected(admin, db):
    seventh = confirmed(db, "Sedmak", 7)
    eighth = confirmed(db, "Osmak", 8)
    token = csrf_from(page(admin))
    admin.post("/admin/sessions/bulk",
               data=form({seventh: present(), eighth: present()}, token=token))
    assert sessions(db, seventh) == [] and sessions(db, eighth) == []


def test_27_unconfirmed_student_injection_is_still_rejected(admin, db):
    student_id = confirmed(db, "Sedmak")
    hidden = confirmed(db, "Zatecen")
    conn = db._connection()
    conn.execute("UPDATE students SET grade_confirmed_at = NULL, "
                 " grade_source = NULL WHERE id = ?", (hidden,))
    conn.commit()
    token = csrf_from(page(admin))
    admin.post("/admin/sessions/bulk",
               data=form({student_id: present(), hidden: present()},
                         token=token))
    assert sessions(db, student_id) == [] and sessions(db, hidden) == []


def test_28_the_roster_still_uses_only_the_confirmed_current_grade(admin, db):
    from matbot import admin_sessions

    seventh = confirmed(db, "Sedmak", 7)
    eighth = confirmed(db, "Osmak", 8)
    roster, _ = admin_sessions.class_roster(db, 7)
    assert [s["student_id"] for s in roster] == [seventh]
    assert eighth not in [s["student_id"] for s in roster]

    before = db.fetch_student_profile(seventh)
    token = csrf_from(page(admin))
    admin.post("/admin/sessions/bulk",
               data=form({seventh: present()}, token=token))
    assert db.fetch_student_profile(seventh) == before, "razred je promijenjen"


# ===========================================================================
# 29-33) ZATEČENI REDOVI I MIGRACIJA
# ===========================================================================
def test_29_legacy_rows_without_time_remain_readable(db):
    student_id = confirmed(db, "Sa Istorijom")
    conn = db._connection()
    conn.execute(
        "INSERT INTO student_sessions (student_id, session_date, attendance, "
        " activity_rating, homework_status, area_name, lesson_name, comment) "
        "VALUES (?, '2026-05-04', 'present', 3, 'done', ?, ?, 'lanjski')",
        (student_id, AREA, LESSON))
    conn.commit()

    stored = sessions(db, student_id)
    assert len(stored) == 1
    assert stored[0]["session_time"] is None
    assert stored[0]["topic_source"] is None
    assert stored[0]["comment"] == "lanjski"
    summary = report_input.build_instruction_section(student_id, "2026-05",
                                                     database=db)
    assert summary["sessions_total"] == 1
    assert summary["lessons_worked"] == [LESSON]


def test_30_migration_preserves_old_rows_byte_for_byte(tmp_path):
    path = str(tmp_path / "legacy.db")
    build_v1(path)
    migrate_v4_only(path)
    conn = libsql.connect(path)
    conn.execute("INSERT INTO students (display_name, grade) VALUES ('A', 7)")
    for activity in (2, 3):
        conn.execute(
            "INSERT INTO student_sessions (student_id, session_date, attendance,"
            " activity_rating, homework_status, area_name, lesson_name, comment)"
            " VALUES (1, '2026-05-01', 'present', ?, 'done', ?, ?, 'stari')",
            (activity, AREA, LESSON))
    conn.commit()
    columns = ("id, student_id, session_date, attendance, activity_rating, "
               "homework_status, area_name, lesson_name, comment, created_at, "
               "updated_at")
    before = [tuple(r) for r in conn.execute(
        "SELECT %s FROM student_sessions ORDER BY id" % columns).fetchall()]

    assert reporting_schema.migrate_to_v5(conn) is True

    after = [tuple(r) for r in conn.execute(
        "SELECT %s FROM student_sessions ORDER BY id" % columns).fetchall()]
    assert after == before, "migracija je prepisala zatečene redove"
    # Nove kolone su prazne — nijedno vrijeme se ne izmišlja.
    assert [tuple(r) for r in conn.execute(
        "SELECT session_time, topic_source FROM student_sessions").fetchall()] \
        == [(None, None), (None, None)]
    conn.close()


def test_31_v4_to_v5_migration_succeeds_and_is_idempotent(tmp_path):
    path = str(tmp_path / "v4.db")
    build_v1(path)
    migrate_v4_only(path)
    conn = libsql.connect(path)
    assert reporting_schema.migrate_to_v5(conn) is True
    assert reporting_schema.verify_v5_schema(conn) == []
    assert reporting_schema.migrate_to_v5(conn) is False
    assert reporting_schema.applied_versions(conn) >= {1, 2, 3, 4, 5}
    conn.close()


def test_31b_the_unique_index_blocks_a_duplicate_timed_row(tmp_path):
    path = str(tmp_path / "v5.db")
    build_v1(path)
    migrate(path)
    conn = libsql.connect(path)
    conn.execute("INSERT INTO students (display_name, grade) VALUES ('A', 7)")
    conn.execute(
        "INSERT INTO student_sessions (student_id, session_date, session_time, "
        " attendance, activity_rating, homework_status, area_name, lesson_name) "
        "VALUES (1, '2026-09-01', '10:00', 'present', 4, 'done', ?, ?)",
        (AREA, LESSON))
    conn.commit()
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO student_sessions (student_id, session_date, "
            " session_time, attendance, activity_rating, homework_status, "
            " area_name, lesson_name) "
            "VALUES (1, '2026-09-01', '10:00', 'present', 5, 'done', ?, ?)",
            (AREA, LESSON))
        conn.commit()
    conn.close()


def test_32_a_half_applied_v5_resumes_safely(tmp_path):
    path = str(tmp_path / "partial.db")
    build_v1(path)
    migrate_v4_only(path)
    conn = libsql.connect(path)
    # Prekid poslije PRVE kolone.
    conn.execute("ALTER TABLE student_sessions ADD COLUMN session_time TEXT")
    conn.commit()
    assert 5 not in reporting_schema.applied_versions(conn)

    assert reporting_schema.migrate_to_v5(conn) is True
    assert reporting_schema.verify_v5_schema(conn) == []
    conn.close()


def test_33_a_malformed_partial_v5_fails_closed(tmp_path):
    path = str(tmp_path / "broken.db")
    build_v1(path)
    migrate_v4_only(path)
    conn = libsql.connect(path)
    conn.execute("ALTER TABLE student_sessions ADD COLUMN topic_source TEXT "
                 "NOT NULL DEFAULT 'x'")
    conn.commit()
    with pytest.raises(reporting_schema.MigrationError) as caught:
        reporting_schema.migrate_to_v5(conn)
    assert caught.value.code.startswith("v5_not_nullable")
    assert 5 not in reporting_schema.applied_versions(conn)
    conn.close()


def test_33b_a_recorded_v5_without_the_columns_fails_closed(tmp_path):
    path = str(tmp_path / "lying.db")
    build_v1(path)
    migrate_v4_only(path)
    conn = libsql.connect(path)
    conn.execute("INSERT INTO schema_migrations (version, description) "
                 "VALUES (5, 'lazni zapis')")
    conn.commit()
    with pytest.raises(reporting_schema.MigrationError) as caught:
        reporting_schema.migrate_to_v5(conn)
    assert caught.value.code.startswith("v5_columns_missing")
    conn.close()


def test_33c_v5_refuses_to_run_before_v4(tmp_path):
    from tests.test_thinkific_progress_import import migrate_v3_only

    path = str(tmp_path / "v3.db")
    build_v1(path)
    migrate_v3_only(path)
    conn = libsql.connect(path)
    with pytest.raises(reporting_schema.MigrationError) as caught:
        reporting_schema.migrate_to_v5(conn)
    assert caught.value.code == "v4_migration_record_missing"
    conn.close()


def test_33d_version_row_written_only_after_verification():
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "matbot"
              / "reporting_schema.py").read_text(encoding="utf-8")
    body = source.split("def migrate_to_v5(")[1].split("\ndef ")[0]
    assert body.index("_apply_v5_ddl(conn)") < body.rindex("verify_v5_schema(conn)")
    assert body.rindex("verify_v5_schema(conn)") < body.index("_record_migration(")


# ===========================================================================
# 34-38) IZVJEŠTAJ, POJEDINAČNI PUT, BEZ MODELA
# ===========================================================================
def test_34_reporting_metrics_match_the_individual_entry_path(admin, db):
    bulk = confirmed(db, "Preko Casa")
    single = confirmed(db, "Preko Profila")
    plan = [("2026-09-01", "10:00", "present", "4", "done", "prvi"),
            ("2026-09-02", "11:00", "present", "5", "not_done", None),
            ("2026-09-03", "12:00", "absent", None, None, None)]
    token = csrf_from(page(admin))
    for date, time, state, activity, homework, comment in plan:
        fields = {"participation": state}
        if state == "present":
            fields["activity"] = activity
            fields["homework"] = homework
        if comment:
            fields["comment"] = comment
        admin.post("/admin/sessions/bulk",
                   data=form({bulk: fields}, date=date, time=time, token=token))
        db.insert_session(single, student_sessions.validate_session(
            session_date=date, session_time=time, attendance=state,
            activity_rating=activity if state == "present" else None,
            homework_status=(homework if state == "present"
                             else class_entry.ABSENT_HOMEWORK),
            area_name=AREA, lesson_name=LESSON, comment=comment, grade=GRADE,
            topic_source=student_sessions.TOPIC_CURRICULUM, require_time=True))

    from_bulk = report_input.build_instruction_section(bulk, "2026-09",
                                                       database=db)
    from_single = report_input.build_instruction_section(single, "2026-09",
                                                         database=db)
    assert from_bulk == from_single


def test_36_the_individual_workflow_still_works_and_requires_time(admin, db):
    student_id = confirmed(db, "Sedmak")
    profile = admin.get("/admin/students/%d" % student_id)
    assert b'name="session_time"' in profile.data

    # Bez vremena se NOV zapis ne pravi.
    admin.post("/admin/students/%d/sessions" % student_id,
               data={"csrf_token": csrf_from(profile),
                     "session_date": "2026-09-10", "attendance": "present",
                     "activity_rating": "5", "homework_status": "done",
                     "area_name": AREA, "lesson_name": LESSON})
    assert sessions(db, student_id) == []

    admin.post("/admin/students/%d/sessions" % student_id,
               data={"csrf_token": csrf_from(profile),
                     "session_date": "2026-09-10", "session_time": "11:30",
                     "attendance": "present", "activity_rating": "5",
                     "homework_status": "done", "area_name": AREA,
                     "lesson_name": LESSON})
    stored = sessions(db, student_id)
    assert len(stored) == 1 and stored[0]["session_time"] == "11:30"


def test_37_no_model_call_is_involved(admin, db, flask_app):
    fake = flask_app.config["MATBOT_LLM"]
    before = len(getattr(fake, "calls", []) or [])
    student_id = confirmed(db, "Sedmak")
    token = csrf_from(page(admin))
    admin.post("/admin/sessions/bulk",
               data=form({student_id: present()}, token=token))
    admin.post("/admin/sessions/bulk",
               data=form({student_id: present()}, mode="custom", area="",
                         lesson=CUSTOM_TOPIC, time="16:00", token=token))
    assert len(getattr(fake, "calls", []) or []) == before

    from pathlib import Path

    for module in ("class_entry.py", "admin_sessions.py"):
        source = (Path("matbot") / module).read_text(encoding="utf-8")
        assert "llm" not in source.lower().replace("small", "")


def test_37b_the_report_prompt_version_is_unchanged():
    from matbot import report_prompt

    assert report_prompt.REPORT_PROMPT_VERSION == "3d-2"
