"""Vrijeme DOGAĐAJA, ne vrijeme UPISA.

DEFEKT KOJI OVAJ FAJL ZAKLJUČAVA (nađen pri pregledu, prije commita): i
`learning_activity.occurred_at` i `assessment_attempts.started_at/completed_at`
su se punili bazinim `CURRENT_TIMESTAMP` — dakle u trenutku kad radna nit stigne
da piše. Kako su izvještajni upisi asinhroni i smiju stići obrnutim redom, to je
moglo proizvesti logički nemoguć zapis:

    generisanje  16:20  ->  upis stigao DRUGI  ->  started_at   = 16:24
    predaja      16:24  ->  upis stigao PRVI   ->  completed_at = 16:20

    => completed_at < started_at za test koji je očito prvo generisan.

Raspored niti nikad ne smije određivati hronologiju učenikovih događaja.

Testovi ispod NAMJERNO obrću redoslijed perzistencije i dokazuju da hronologija
preživi. Vremena se zadaju izričito (a ne mjere), jer se dokazuje SEMANTIKA, ne
brzina sata.
"""
import pytest

from matbot import activity, auth, reporting_db, student_identity
from matbot.api import _kontrolni_attempt
from matbot.student_identity import PROVIDER_THINKIFIC_EMAIL

from tests.conftest import queue_two_call, make_explain_output
from tests.test_assessment_capture import (attempts, build_schema, enter, items,
                                           rows, start_exam, submit_exam, wait_for)

libsql = pytest.importorskip("libsql")

T1 = "2026-08-24 16:20:00"    # generisanje
T2 = "2026-08-24 16:24:00"    # predaja
TEST_EMAIL = "kontrolni.ucenik@example.com"


@pytest.fixture
def reporting(tmp_path, monkeypatch):
    path = str(tmp_path / "reporting.db")
    build_schema(path)
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://test.invalid")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token-not-real")
    database = reporting_db.ReportingDatabase(
        connect_factory=lambda: libsql.connect(path, timeout=10.0,
                                               _check_same_thread=False))
    reporting_db.set_database(database)
    yield path
    reporting_db.wait_for_pending_writes()
    reporting_db.set_database(None)


def student(database):
    return database.get_or_create_student(PROVIDER_THINKIFIC_EMAIL, TEST_EMAIL)


def generated(exam_id, started_at):
    return _kontrolni_attempt(exam_id, grade=6, area_name="Razlomci",
                              total_count=5, started_at=started_at)


def completed(exam_id, completed_at):
    return _kontrolni_attempt(exam_id, grade=6, area_name="Razlomci",
                              total_count=5, correct_count=4, score_percent=80,
                              completed_at=completed_at)


ITEMS = [{"item_key": "q1", "ordinal": 1, "is_correct": True},
         {"item_key": "q2", "ordinal": 2, "is_correct": False}]


# ---------------------------------------------------------------------------
# 1-2) Oba redoslijeda perzistencije daju ISTU hronologiju
# ---------------------------------------------------------------------------
def test_natural_order_persists_event_times(reporting):
    database = reporting_db.get_database()
    student_id = student(database)

    database.record_assessment_generated(student_id, generated("exam-a", T1))
    database.record_assessment_completed(student_id, completed("exam-a", T2), ITEMS)

    row = attempts(reporting)[0]
    assert row[13] == T1 and row[14] == T2
    assert row[13] <= row[14]


def test_reversed_persistence_still_yields_correct_chronology(reporting):
    """DIO 3B: upis predaje stigne PRVI, upis generisanja tek poslije."""
    database = reporting_db.get_database()
    student_id = student(database)

    # Predaja se perzistira prva...
    database.record_assessment_completed(student_id, completed("exam-b", T2), ITEMS)
    assert attempts(reporting)[0][13] is None, "predaja ne zna vrijeme početka"

    # ...a generisanje tek onda, noseći svoje ISTORIJSKO vrijeme.
    database.record_assessment_generated(student_id, generated("exam-b", T1))

    row = attempts(reporting)[0]
    assert len(attempts(reporting)) == 1
    assert row[13] == T1, "started_at nije istorijsko vrijeme generisanja"
    assert row[14] == T2, "completed_at je pomjeren zakašnjelim upisom"
    assert row[13] <= row[14], "completed_at < started_at je nemoguća hronologija"
    # DIO 3: zakašnjeli upis i dalje čuva rezultat.
    assert (row[10], row[11], row[12]) == (80.0, 4, 5)
    assert len(items(reporting)) == 2


def test_write_order_does_not_change_the_stored_chronology(reporting):
    """Oba redoslijeda moraju dati BAJT ZA BAJT iste vremenske vrijednosti."""
    database = reporting_db.get_database()
    student_id = student(database)

    database.record_assessment_generated(student_id, generated("exam-fwd", T1))
    database.record_assessment_completed(student_id, completed("exam-fwd", T2), ITEMS)
    database.record_assessment_completed(student_id, completed("exam-rev", T2), ITEMS)
    database.record_assessment_generated(student_id, generated("exam-rev", T1))

    stored = {row[4]: (row[13], row[14]) for row in attempts(reporting)}
    assert stored["exam-fwd"] == stored["exam-rev"] == (T1, T2)


# ---------------------------------------------------------------------------
# 3-4) Ponovljena isporuka ne pomjera vrijeme
# ---------------------------------------------------------------------------
def test_repeated_generation_keeps_the_earliest_start(reporting):
    database = reporting_db.get_database()
    student_id = student(database)

    database.record_assessment_generated(student_id, generated("exam-c", T1))
    # Zakašnjela ponovljena isporuka s KASNIJIM pečatom ne smije pomjeriti početak.
    database.record_assessment_generated(student_id,
                                         generated("exam-c", "2026-08-24 17:00:00"))

    assert attempts(reporting)[0][13] == T1


def test_repeated_completion_keeps_the_first_completion(reporting):
    database = reporting_db.get_database()
    student_id = student(database)

    database.record_assessment_generated(student_id, generated("exam-d", T1))
    database.record_assessment_completed(student_id, completed("exam-d", T2), ITEMS)
    database.record_assessment_completed(student_id,
                                         completed("exam-d", "2026-08-24 18:00:00"),
                                         ITEMS)

    row = attempts(reporting)[0]
    assert row[14] == T2, "ponovljena predaja je pomjerila completed_at"
    assert row[13] == T1
    assert len(attempts(reporting)) == 1 and len(items(reporting)) == 2


# ---------------------------------------------------------------------------
# 5) Rezultat ostaje netaknut
# ---------------------------------------------------------------------------
def test_result_fields_survive_every_ordering(reporting):
    database = reporting_db.get_database()
    student_id = student(database)

    database.record_assessment_completed(student_id, completed("exam-e", T2), ITEMS)
    database.record_assessment_generated(student_id, generated("exam-e", T1))
    database.record_assessment_generated(student_id, generated("exam-e", T1))

    row = attempts(reporting)[0]
    assert (row[10], row[11], row[12]) == (80.0, 4, 5)
    assert row[14] == T2


# ---------------------------------------------------------------------------
# 6) `created_at` i dalje znači VRIJEME UPISA — i to je namjerno
# ---------------------------------------------------------------------------
def test_created_at_remains_row_creation_time(reporting):
    """Semantička razlika koju šema sada nosi čisto:
    `started_at`/`completed_at` = kad se DOGAĐAJ desio,
    `created_at`               = kad je RED nastao."""
    database = reporting_db.get_database()
    student_id = student(database)
    database.record_assessment_generated(student_id, generated("exam-f", T1))

    created = rows(reporting,
                   "SELECT started_at, created_at FROM assessment_attempts")[0]
    assert created[0] == T1
    assert created[1] is not None and created[1] != T1, (
        "created_at je istorijsko vrijeme, a treba da bude vrijeme upisa")


# ---------------------------------------------------------------------------
# 7) learning_activity.occurred_at — isti semantički problem, isto rješenje
# ---------------------------------------------------------------------------
def test_activity_event_carries_its_own_occurrence_time(reporting):
    database = reporting_db.get_database()
    student_id = student(database)
    event = activity.ActivityEvent(
        activity.QUICK_COMPLETED, "quick:s:1", mode="quick",
        occurred_at="2026-07-01 08:15:00")

    database.record_learning_activity(student_id, [event])

    stored = rows(reporting, "SELECT occurred_at FROM learning_activity")[0][0]
    assert stored == "2026-07-01 08:15:00", (
        "occurred_at je uzet iz vremena upisa, a ne iz događaja")


def test_note_stamps_the_moment_the_fact_became_true():
    with activity.capture() as events:
        activity.note(activity.QUICK_COMPLETED, "quick:s:2", mode="quick")

    assert len(events) == 1
    stamp = events[0].occurred_at
    assert stamp and len(stamp) == 19 and stamp[4] == "-" and stamp[13] == ":"


def test_timestamp_format_matches_the_database_default(reporting):
    """Naš format i `CURRENT_TIMESTAMP` moraju biti isti oblik, inače bi se
    poređenje, `date()` i sortiranje ponašali različito nad istom kolonom."""
    ours = activity.event_timestamp()
    theirs = rows(reporting, "SELECT CURRENT_TIMESTAMP")[0][0]

    assert len(ours) == len(theirs) == 19
    assert "." not in ours and "." not in theirs   # bez izmišljenih milisekundi
    assert ours[:11] == theirs[:11]                # isti dan, isti UTC


# ---------------------------------------------------------------------------
# Kroz STVARNU rutu: hronologija i odsustvo sinhronog čekanja
# ---------------------------------------------------------------------------
def test_route_level_kontrolni_chronology_is_never_impossible(client, flask_app,
                                                              fake_llm, reporting):
    token = enter(client)
    started = start_exam(client, flask_app, token)
    wait_for(reporting, "assessment_attempts", 1)
    submit_exam(client, flask_app, token, started["exam_id"])
    wait_for(reporting, "assessment_item_results", 5)

    row = attempts(reporting)[0]
    assert row[13] is not None and row[14] is not None
    assert row[13] <= row[14], "started_at > completed_at kroz stvarnu rutu"


def test_capturing_event_time_adds_no_synchronous_database_wait(client, fake_llm,
                                                                monkeypatch):
    """DIO 5.7: hvatanje vremena je lokalno računanje, ne mrežni put."""
    from tests.test_reporting_db_failure_policy import HangingDatabase
    from matbot import config
    import time

    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://test.invalid")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token-not-real")
    monkeypatch.setattr(config, "REPORTING_DB_MAX_INFLIGHT", 1)
    reporting_db.shutdown()
    hanging = HangingDatabase()
    reporting_db.set_database(hanging)
    try:
        token = enter(client)
        fake_llm.queue(make_explain_output("Prvo."))
        client.post("/api/ai-tutor/chat", json=_explain_payload("warm"),
                    headers={auth.TOKEN_HEADER: token})
        fake_llm.queue(make_explain_output("Drugo."))
        began = time.perf_counter()
        response = client.post("/api/ai-tutor/chat", json=_explain_payload("measured"),
                               headers={auth.TOKEN_HEADER: token})
        elapsed = time.perf_counter() - began
    finally:
        hanging.close()
        reporting_db.set_database(None)
        reporting_db.shutdown()

    assert response.status_code == 200
    assert response.get_json()["status"] == "ready"
    assert elapsed < 0.5


def _explain_payload(turn_id):
    return {"session_id": "ts-sess", "client_turn_id": turn_id, "grade": 6,
            "mode": "explain", "entry_source": "manual_topic_choice",
            "selected_topic": "6-01-005", "selected_oblast": "",
            "student_message": "Objasni mi razlomke.", "conversation_history": []}
