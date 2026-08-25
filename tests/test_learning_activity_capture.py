"""Faza 2 — strukturirani zapis aktivnosti učenika kroz STVARNE rute modova.

Namjerno se NE testira `activity.note` izolovano: vrijednost ove faze je da
događaj nastane tačno kad ga server dokaže, a to se vidi samo ako turn prođe
kroz `/api/ai-tutor/*` kao kod učenika. Zato svaki test ide preko Flask klijenta,
s pravom libsql bazom na disku.

ŠTA SE OVDJE DOKAZUJE, A ŠTA NE: dokazuje se da postoji tačno jedan red po
dokazanoj činjenici, da anoniman rad ne piše ništa, da dupla isporuka ne pravi
duplikat i da u zapisu nema ni e-maila ni teksta učenika. NE dokazuje se da je
model dobro odgovorio — to je posao drugih svita.
"""
import json
import logging
import threading

import pytest

from matbot import activity, auth, reporting_db, student_identity
from matbot.student_identity import PROVIDER_THINKIFIC_EMAIL

from tests.conftest import (queue_two_call, make_explain_output, make_quick_output,
                            make_task_payload, make_tutor_draft)
from tests.test_reporting_db_identity import SCHEMA as IDENTITY_SCHEMA

libsql = pytest.importorskip("libsql")

TEST_EMAIL = "ucenik.test@example.com"

# `learning_activity` TAČNO onakav kakav je opisan u produkcijskoj šemi v1.
# Ova faza NE MIJENJA šemu — tabela se ovdje samo ogleda da bi test mjerio isti
# ugovor koji produkcija ima, uključujući UNIQUE(source, event_key).
ACTIVITY_SCHEMA = """
CREATE TABLE learning_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id),
    source TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_key TEXT NOT NULL,
    grade INTEGER,
    area_name TEXT,
    lesson_id TEXT,
    lesson_name TEXT,
    mode TEXT,
    occurred_at TEXT,
    duration_seconds INTEGER,
    progress_percent INTEGER,
    metadata_json TEXT,
    UNIQUE (source, event_key)
);
"""


@pytest.fixture
def reporting(tmp_path, monkeypatch):
    path = str(tmp_path / "reporting.db")
    conn = libsql.connect(path)
    for statement in IDENTITY_SCHEMA.strip().split(";"):
        # `learning_activity` iz identitetske šeme je samo placeholder — ovdje
        # treba prava tabela s UNIQUE ugovorom.
        if statement.strip() and "CREATE TABLE learning_activity" not in statement:
            conn.execute(statement)
    for statement in ACTIVITY_SCHEMA.strip().split(";"):
        if statement.strip():
            conn.execute(statement)
    conn.execute("INSERT INTO schema_migrations (version, description) "
                 "VALUES (1, 'Initial Matematicari reporting schema')")
    conn.commit()
    conn.close()

    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://test.invalid")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token-not-real")
    database = reporting_db.ReportingDatabase(
        connect_factory=lambda: libsql.connect(path, timeout=10.0,
                                               _check_same_thread=False))
    reporting_db.set_database(database)
    yield path
    # Odvojene niti su daemon i prezivjele bi ovaj test; drenaza
    # sprjecava da zaostao upis obori sljedeci test.
    reporting_db.wait_for_pending_writes()
    reporting_db.set_database(None)


# ---------------------------------------------------------------------------
# Pomoćnici
# ---------------------------------------------------------------------------
def rows(path, sql, params=()):
    conn = libsql.connect(path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def wait_for_events(path, expected, timeout=10.0):
    """Upis je NAMJERNO asinhron (Dio 11), pa test čeka na ishod umjesto da
    pretpostavi trenutan red. Nema `sleep` fiksne dužine — čeka se uslov."""
    deadline = threading.Event()
    for _ in range(int(timeout * 40)):
        if rows(path, "SELECT COUNT(*) FROM learning_activity")[0][0] >= expected:
            break
        deadline.wait(0.025)
    return events(path)


def events(path):
    return rows(path, "SELECT event_type, event_key, grade, area_name, lesson_id, "
                      "lesson_name, mode, metadata_json, duration_seconds, "
                      "progress_percent FROM learning_activity ORDER BY id")


def types(path):
    return [row[0] for row in events(path)]


def enter(client, email=TEST_EMAIL):
    query = {student_identity.QUERY_PARAM: email} if email else {}
    response = client.get("/", query_string=query)
    body = response.get_data(as_text=True)
    marker = 'name="matbot-embed-token" content="'
    start = body.index(marker) + len(marker)
    return body[start:body.index('"', start)]


def chat(client, token, *, mode="practice", turn_id="t1", session="sess-a", **over):
    payload = {
        "session_id": session, "client_turn_id": turn_id, "grade": 6, "mode": mode,
        "entry_source": "manual_topic_choice", "selected_topic": "6-01-005",
        "selected_oblast": "", "conversation_history": [],
        "student_message": "Daj mi jedan zadatak za vježbu iz ove teme.",
    }
    payload.update(over)
    return client.post("/api/ai-tutor/chat", json=payload,
                       headers={auth.TOKEN_HEADER: token})


def student_id_of(path):
    found = rows(path, "SELECT id FROM students")
    return found[0][0] if found else None


# ---------------------------------------------------------------------------
# 1) Practice — objavljen zadatak
# ---------------------------------------------------------------------------
def test_identified_practice_task_writes_one_event(client, fake_llm, reporting):
    queue_two_call(fake_llm)
    assert chat(client, enter(client)).status_code == 200

    recorded = wait_for_events(reporting, 1)
    assert len(recorded) == 1
    row = recorded[0]
    assert row[0] == activity.PRACTICE_TASK_PRESENTED
    assert row[2] == 6                       # grade
    assert row[4] == "6-01-005"              # lesson_id
    assert row[6] == "practice"              # mode
    assert row[8] is None and row[9] is None  # duration/progress ostaju NULL
    assert rows(reporting, "SELECT source FROM learning_activity")[0][0] == "matbot"
    assert rows(reporting,
                "SELECT student_id FROM learning_activity")[0][0] == student_id_of(reporting)


# ---------------------------------------------------------------------------
# 2-3) Practice — verdikt klika
# ---------------------------------------------------------------------------
def _present_task(client, fake_llm, token):
    queue_two_call(fake_llm)
    assert chat(client, token).status_code == 200


def _click(client, token, option_id, turn_id, fake_llm=None):
    """Klik ide kroz PRAVU rutu, pa mu treba i njegov model-odgovor: `_two_call`
    za ne-zadatak intent troši tačno jedan poziv (recenzent se ne zove)."""
    if fake_llm is not None:
        queue_two_call(fake_llm, draft=make_tutor_draft(
            intent="clarification", reply="Hajde da pogledamo.", new_task=None))
    return chat(client, token, turn_id=turn_id, interaction_type="choice_answer",
                selected_option_id=option_id, student_message="Izabrana opcija.")


def _help_turn(client, token, fake_llm, *, intent, message, turn_id):
    """Pomoć ide kroz PRAVU rutu. Nacrt mora nositi i sam tekst pomoći —
    `hint_request` bez `hint` polja motor odbija (`hint_request bez hinta`)."""
    helpful = "Pogledaj šta se traži u zadatku."
    queue_two_call(fake_llm, draft=make_tutor_draft(
        intent=intent, reply=helpful, new_task=None,
        hint=helpful if intent == "hint_request" else None,
        worked_solution=helpful if intent == "full_solution_request" else None))
    return chat(client, token, turn_id=turn_id, intent=intent,
                student_message=message)


def _correct_option_id(flask_app):
    session = flask_app.config["MATBOT_SESSION_STORE"].peek("sess-a")
    return session["correct_option_id"], [o["id"] for o in session["current_options"]]


def test_correct_answer_writes_correct_event(client, flask_app, fake_llm, reporting):
    token = enter(client)
    _present_task(client, fake_llm, token)
    correct, _ = _correct_option_id(flask_app)

    assert _click(client, token, correct, "t2", fake_llm).status_code == 200

    recorded = wait_for_events(reporting, 2)
    assert types(reporting) == [activity.PRACTICE_TASK_PRESENTED,
                                activity.PRACTICE_ANSWER_CORRECT]
    assert recorded[1][4] == "6-01-005"


def test_incorrect_answer_writes_incorrect_event(client, flask_app, fake_llm, reporting):
    token = enter(client)
    _present_task(client, fake_llm, token)
    correct, all_ids = _correct_option_id(flask_app)
    wrong = next(i for i in all_ids if i != correct)

    assert _click(client, token, wrong, "t2", fake_llm).status_code == 200

    wait_for_events(reporting, 2)
    assert types(reporting) == [activity.PRACTICE_TASK_PRESENTED,
                                activity.PRACTICE_ANSWER_INCORRECT]


# ---------------------------------------------------------------------------
# 4-5) Practice — pomoć
# ---------------------------------------------------------------------------
def test_hint_writes_hint_event(client, fake_llm, reporting):
    token = enter(client)
    _present_task(client, fake_llm, token)

    response = _help_turn(client, token, fake_llm, intent="hint_request",
                          message="Daj mi nagovještaj.", turn_id="t2")
    assert response.status_code == 200

    recorded = wait_for_events(reporting, 2)
    assert activity.PRACTICE_HINT_USED in types(reporting)
    hint_row = next(r for r in recorded if r[0] == activity.PRACTICE_HINT_USED)
    assert json.loads(hint_row[7])["hint_level"] >= 1


def test_full_solution_writes_solution_event(client, fake_llm, reporting):
    token = enter(client)
    _present_task(client, fake_llm, token)

    response = _help_turn(client, token, fake_llm, intent="full_solution_request",
                          message="Uradi ga ti.", turn_id="t2")
    assert response.status_code == 200

    wait_for_events(reporting, 2)
    assert activity.PRACTICE_FULL_SOLUTION_SHOWN in types(reporting)


def test_repeated_hint_click_does_not_write_a_second_event(client, fake_llm, reporting):
    token = enter(client)
    _present_task(client, fake_llm, token)
    _help_turn(client, token, fake_llm, intent="hint_request",
               message="Daj mi nagovještaj.", turn_id="t2")
    wait_for_events(reporting, 2)

    # Ponovljen klik: isti tekst, nula poziva, ISTI nivo ljestvice.
    _help_turn(client, token, fake_llm, intent="hint_request",
               message="Daj mi nagovještaj.", turn_id="t3")
    wait_for_events(reporting, 3, timeout=1.0)

    hints = [t for t in types(reporting) if t == activity.PRACTICE_HINT_USED]
    assert len(hints) == 1, "ponovljen nagovještaj ne smije biti nova aktivnost"


# ---------------------------------------------------------------------------
# 6-7) Explain i Quick
# ---------------------------------------------------------------------------
def test_explain_success_writes_one_event(client, fake_llm, reporting):
    fake_llm.queue(make_explain_output("Evo objašnjenja."))
    assert chat(client, enter(client), mode="explain",
                student_message="Objasni mi razlomke.").status_code == 200

    recorded = wait_for_events(reporting, 1)
    assert [r[0] for r in recorded] == [activity.EXPLAIN_COMPLETED]
    assert recorded[0][6] == "explain"


def test_quick_success_writes_one_event(client, fake_llm, reporting):
    fake_llm.queue(make_quick_output("Rezultat je $27$."))
    assert chat(client, enter(client), mode="quick",
                student_message="Koliko je 12+15?").status_code == 200

    recorded = wait_for_events(reporting, 1)
    assert [r[0] for r in recorded] == [activity.QUICK_COMPLETED]
    assert recorded[0][6] == "quick"


def test_failed_explain_writes_nothing(client, fake_llm, reporting):
    from matbot.llm import LLMUnavailable

    fake_llm.queue(LLMUnavailable("boom"))
    response = chat(client, enter(client), mode="explain",
                    student_message="Objasni mi razlomke.")

    assert response.status_code == 200
    assert response.get_json().get("status") != "ready"
    wait_for_events(reporting, 1, timeout=1.0)
    assert types(reporting) == [], "neuspio turn nije aktivnost učenika"


# ---------------------------------------------------------------------------
# 8-9) Kontrolni
# ---------------------------------------------------------------------------
def _exam_payload(**over):
    payload = {"session_id": "sess-exam", "grade": 6, "oblast_id": "6-04",
               "selected_oblast": "6-04", "relative": ""}
    payload.update(over)
    return payload


def test_valid_kontrolni_generation_writes_generated_event(client, flask_app,
                                                           fake_llm, reporting):
    from tests.test_kontrolni import EchoKontrolniLLM, start_payload

    flask_app.config["MATBOT_LLM"] = EchoKontrolniLLM()
    token = enter(client)
    response = client.post("/api/ai-tutor/exam/start", json=start_payload(),
                           headers={auth.TOKEN_HEADER: token})
    assert response.status_code == 200 and response.get_json()["status"] == "ready"

    recorded = wait_for_events(reporting, 1)
    assert [r[0] for r in recorded] == [activity.KONTROLNI_GENERATED]
    assert recorded[0][6] == "kontrolni"


def test_failed_closed_kontrolni_writes_no_completion(client, flask_app, fake_llm,
                                                      reporting):
    """Pad zatvoreno NE SMIJE izgledati kao da je učenik radio kontrolni."""
    from matbot.llm import LLMUnavailable

    fake_llm.queue(LLMUnavailable("boom"))
    fake_llm.queue(LLMUnavailable("boom"))
    token = enter(client)
    from tests.test_kontrolni import start_payload

    response = client.post("/api/ai-tutor/exam/start", json=start_payload(),
                           headers={auth.TOKEN_HEADER: token})

    assert response.status_code == 200
    assert response.get_json().get("status") != "ready"
    wait_for_events(reporting, 1, timeout=1.0)
    assert types(reporting) == []


def test_kontrolni_submit_writes_completion_without_duplicating_the_score(
        client, flask_app, fake_llm, reporting):
    """Aktivnost nosi SAMO "sta i kada".

    Ocjena je namjerno izvan `learning_activity` (Dio 11): autoritativan
    rezultat zivi u `assessment_attempts`, a ishodi po pitanju u
    `assessment_item_results`. Dvije kopije istog broja bi se razisle."""
    from tests.test_kontrolni import EchoKontrolniLLM, start_payload

    flask_app.config["MATBOT_LLM"] = EchoKontrolniLLM()
    token = enter(client)
    started = client.post("/api/ai-tutor/exam/start", json=start_payload(),
                          headers={auth.TOKEN_HEADER: token}).get_json()
    wait_for_events(reporting, 1)

    store = flask_app.config["MATBOT_EXAM_STORE"]
    answers = {q["id"]: q["correct_option_id"]
               for q in store.get(start_payload()["session_id"])["questions"]}
    graded = client.post("/api/ai-tutor/exam/submit",
                         json={"session_id": start_payload()["session_id"],
                               "exam_id": started["exam_id"], "answers": answers},
                         headers={auth.TOKEN_HEADER: token})
    assert graded.status_code == 200 and graded.get_json()["score"] == 5

    recorded = wait_for_events(reporting, 2)
    completion = next(r for r in recorded if r[0] == activity.KONTROLNI_COMPLETED)
    assert completion[7] is None, "ocjena se vise ne duplira u aktivnosti"

    dump = str(rows(reporting, "SELECT * FROM learning_activity"))
    for forbidden in ('"score"', '"percentage"', '"total"'):
        assert forbidden not in dump


# ---------------------------------------------------------------------------
# 10) Anoniman rad
# ---------------------------------------------------------------------------
def test_anonymous_use_writes_no_activity(client, fake_llm, reporting):
    queue_two_call(fake_llm)
    response = chat(client, enter(client, email=None))

    assert response.status_code == 200
    assert response.get_json()["status"] == "ready"
    wait_for_events(reporting, 1, timeout=1.0)
    assert types(reporting) == []
    assert rows(reporting, "SELECT COUNT(*) FROM students")[0][0] == 0


# ---------------------------------------------------------------------------
# 11) Idempotentnost
# ---------------------------------------------------------------------------
def test_duplicate_delivery_creates_one_row(reporting):
    """Isti događaj dva puta → JEDAN red. Odluku donosi UNIQUE(source, event_key)
    u bazi, ne provjera u Pythonu."""
    database = reporting_db.get_database()
    student_id = database.get_or_create_student(PROVIDER_THINKIFIC_EMAIL, TEST_EMAIL)
    event = activity.ActivityEvent(
        activity.PRACTICE_TASK_PRESENTED, "practice:s:abc:presented",
        mode="practice", grade=6, lesson_id="6-01-005")

    first = database.record_learning_activity(student_id, [event])
    second = database.record_learning_activity(student_id, [event])

    assert first == 1 and second == 0
    assert rows(reporting, "SELECT COUNT(*) FROM learning_activity")[0][0] == 1


def test_same_practice_turn_delivered_twice_creates_one_row(client, flask_app,
                                                            fake_llm, reporting):
    token = enter(client)
    _present_task(client, fake_llm, token)
    correct, _ = _correct_option_id(flask_app)
    _click(client, token, correct, "turn-dup", fake_llm)
    wait_for_events(reporting, 2)

    # Ista akcija, isti `client_turn_id` — retry pregledača.
    _click(client, token, correct, "turn-dup", fake_llm)
    wait_for_events(reporting, 3, timeout=1.0)

    answers = [t for t in types(reporting) if t == activity.PRACTICE_ANSWER_CORRECT]
    assert len(answers) == 1


# ---------------------------------------------------------------------------
# 12-13) Izolacija kvara
# ---------------------------------------------------------------------------
def test_unavailable_reporting_db_leaves_tutor_output_unchanged(client, fake_llm,
                                                                monkeypatch):
    from tests.test_reporting_db_failure_policy import ExplodingDatabase

    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://test.invalid")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token-not-real")
    reporting_db.set_database(ExplodingDatabase())
    try:
        queue_two_call(fake_llm)
        response = chat(client, enter(client))
    finally:
        reporting_db.set_database(None)

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "ready" and body["last_tutor_task"]
    raw = response.get_data(as_text=True)
    assert "activity" not in raw and "learning_activity" not in raw


def test_hung_reporting_db_does_not_keep_delaying_the_tutor(client, fake_llm,
                                                            monkeypatch):
    """Zaglavljen Turso ne smije trajno usporiti tutora.

    ISKRENA GRANICA, izmjerena a ne pretpostavljena: identitet se razrješava
    SINHRONO (turnu treba `students.id`), pa na zaglavljenoj bazi prvi turnovi
    plate izvještajni rok od 2 s. Ali semafor drži najviše
    `REPORTING_DB_MAX_INFLIGHT` poziva u letu i uzima se NEBLOKIRAJUĆE — čim su
    slotovi zauzeti zaglavljenim pozivima, svaki sljedeći turn pada na
    `reporting_db_busy` ODMAH i ne plaća ništa. Dakle kašnjenje je ograničeno na
    prvih N turnova po ispadu, ne na svaki.

    Ovo je i razlog zašto upis DOGAĐAJA (ono što ova faza dodaje) uopšte ne ulazi
    u mjerenje: on je asinhron i turn ga nikad ne čeka."""
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
        # Turn koji zauzme jedini slot i ostane zaglavljen.
        fake_llm.queue(make_explain_output("Prvo objašnjenje."))
        first = chat(client, token, mode="explain", turn_id="warm",
                     student_message="Objasni mi razlomke.")
        # Sljedeći turn zatiče pun semafor -> odbacivanje bez čekanja.
        fake_llm.queue(make_explain_output("Drugo objašnjenje."))
        started = time.perf_counter()
        response = chat(client, token, mode="explain", turn_id="measured",
                        student_message="Objasni mi razlomke.")
        elapsed = time.perf_counter() - started
    finally:
        hanging.close()
        reporting_db.set_database(None)
        reporting_db.shutdown()

    assert first.status_code == 200 and first.get_json()["status"] == "ready"
    assert response.status_code == 200
    assert response.get_json()["status"] == "ready"
    assert elapsed < 0.5, (
        "zasićen izvještajni sloj mora odbacivati, a ne zadržavati turn")


# ---------------------------------------------------------------------------
# 14-15) Privatnost
# ---------------------------------------------------------------------------
def test_no_raw_email_or_student_text_is_persisted(client, flask_app, fake_llm,
                                                   reporting):
    token = enter(client)
    _present_task(client, fake_llm, token)
    correct, _ = _correct_option_id(flask_app)
    _click(client, token, correct, "t2", fake_llm)
    _help_turn(client, token, fake_llm, intent="hint_request",
               message="TAJNA-PORUKA-UCENIKA daj mi nagovjestaj",
               turn_id="t3")
    wait_for_events(reporting, 3)

    dump = str(rows(reporting, "SELECT * FROM learning_activity"))
    assert TEST_EMAIL not in dump
    assert "ucenik.test" not in dump
    assert "@" not in dump
    assert "TAJNA-PORUKA-UCENIKA" not in dump
    # Ni tekst zadatka ni odgovor tutora ne smiju biti u zapisu.
    session = flask_app.config["MATBOT_SESSION_STORE"].peek("sess-a")
    assert session["current_task"][:40] not in dump


def test_metadata_contains_only_structured_facts(client, fake_llm, reporting):
    queue_two_call(fake_llm)
    chat(client, enter(client))
    wait_for_events(reporting, 1)

    for (metadata,) in rows(reporting, "SELECT metadata_json FROM learning_activity"):
        if metadata is None:
            continue
        parsed = json.loads(metadata)
        assert isinstance(parsed, dict)
        # Ocjena NIJE u ovom skupu: ona je vlasnistvo `assessment_attempts`.
        allowed = {"difficulty", "difficulty_level", "hint_level",
                   "question_count"}
        assert set(parsed) <= allowed, parsed
        # Prvorazredne kolone se NE ponavljaju u metapodacima.
        assert not {"grade", "lesson_id", "mode", "area_name"} & set(parsed)


# ---------------------------------------------------------------------------
# 16) Interna aktivnost motora nije aktivnost učenika
# ---------------------------------------------------------------------------
def test_rejected_model_package_writes_no_event(client, fake_llm, reporting):
    """Paket koji padne na validaciji nikad ne stiže učeniku — ni u izvještaj."""
    from tests.conftest import make_tutor_draft, make_reviewer_final

    bad = make_task_payload(text="Koliko je $\\ty{2}$?")
    fake_llm.queue(make_tutor_draft(new_task=bad))
    fake_llm.queue(make_reviewer_final(decision="approve", final=None))

    response = chat(client, enter(client))
    assert response.status_code == 200
    wait_for_events(reporting, 1, timeout=1.0)
    assert activity.PRACTICE_TASK_PRESENTED not in types(reporting)


def test_two_call_route_writes_one_task_event(client, fake_llm, reporting):
    """Tutor + Recenzent su DVA modelska poziva, ali JEDAN zadatak za učenika."""
    queue_two_call(fake_llm)
    chat(client, enter(client))
    wait_for_events(reporting, 1)

    presented = [t for t in types(reporting) if t == activity.PRACTICE_TASK_PRESENTED]
    assert len(presented) == 1


# ---------------------------------------------------------------------------
# 17) Isti učenik, druga sesija
# ---------------------------------------------------------------------------
def test_events_from_two_sessions_attach_to_one_student(client, fake_llm, reporting):
    queue_two_call(fake_llm)
    chat(client, enter(client), session="browser-1", turn_id="a1")
    wait_for_events(reporting, 1)
    queue_two_call(fake_llm)
    chat(client, enter(client), session="browser-2", turn_id="b1")
    recorded = wait_for_events(reporting, 2)

    assert len(recorded) == 2
    assert rows(reporting, "SELECT COUNT(*) FROM students")[0][0] == 1
    owners = {r[0] for r in rows(reporting, "SELECT student_id FROM learning_activity")}
    assert owners == {student_id_of(reporting)}


# ---------------------------------------------------------------------------
# Ostalo: zapis bez identiteta i granice sabirnika
# ---------------------------------------------------------------------------
def test_note_outside_capture_is_a_no_op():
    activity.note(activity.QUICK_COMPLETED, "quick:x:y")   # ne smije baciti
    assert activity.active() is False


def test_unknown_event_type_is_dropped(caplog):
    with activity.capture() as events:
        with caplog.at_level(logging.INFO, logger="matbot.activity"):
            activity.note("izmisljeni_dogadjaj", "k")
    assert events == []
    assert "unknown_event_type" in caplog.text


def test_turn_event_limit_is_bounded():
    with activity.capture() as events:
        for index in range(activity.MAX_EVENTS_PER_TURN + 5):
            activity.note(activity.QUICK_COMPLETED, "quick:s:%d" % index)
    assert len(events) == activity.MAX_EVENTS_PER_TURN
