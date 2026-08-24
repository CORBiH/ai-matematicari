"""Faza 2b — Kontrolni kao autoritativan zapis procjene.

Dvije tvrdnje se dokazuju odvojeno i ne smiju se brkati:

  1. LIFECYCLE — generisan test pravi NEDOVRŠEN pokušaj, a predaja ga DOPUNI;
     isti `exam_id` nikad ne daje dva pokušaja, ni pri obrnutom redoslijedu
     asinhronih upisa.
  2. SADRŽAJ — u tabelama procjene NEMA ničega od sadržaja: ni teksta pitanja,
     ni izabranog odgovora, ni tačnog odgovora, ni rješenja. Samo ishod.

Šema tabela je BAJT ZA BAJT produkcijska (v1) — uključujući CHECK ograničenja i
`ON DELETE CASCADE` — pa test mjeri isti ugovor koji produkcija ima. Ova faza
šemu NE MIJENJA.
"""
import json

import pytest

from matbot import activity, auth, reporting_db, student_identity
from matbot.student_identity import PROVIDER_THINKIFIC_EMAIL

from tests.test_learning_activity_capture import ACTIVITY_SCHEMA
from tests.test_reporting_db_identity import SCHEMA as IDENTITY_SCHEMA

libsql = pytest.importorskip("libsql")

TEST_EMAIL = "kontrolni.ucenik@example.com"

ASSESSMENT_SCHEMA = """
CREATE TABLE assessment_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    assessment_type TEXT NOT NULL,
    external_attempt_id TEXT,
    title TEXT,
    grade INTEGER,
    area_name TEXT,
    lesson_id TEXT,
    lesson_name TEXT,
    score_percent REAL CHECK (score_percent IS NULL
                              OR (score_percent >= 0 AND score_percent <= 100)),
    correct_count INTEGER CHECK (correct_count IS NULL OR correct_count >= 0),
    total_count INTEGER CHECK (total_count IS NULL OR total_count >= 0),
    started_at TEXT,
    completed_at TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
    UNIQUE (source, external_attempt_id)
);
CREATE TABLE assessment_item_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id INTEGER NOT NULL,
    item_key TEXT NOT NULL,
    ordinal INTEGER,
    area_name TEXT,
    lesson_id TEXT,
    lesson_name TEXT,
    difficulty TEXT,
    is_correct INTEGER CHECK (is_correct IS NULL OR is_correct IN (0, 1)),
    hints_used INTEGER NOT NULL DEFAULT 0 CHECK (hints_used >= 0),
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (attempt_id) REFERENCES assessment_attempts(id) ON DELETE CASCADE,
    UNIQUE (attempt_id, item_key)
);
"""


def build_schema(path):
    conn = libsql.connect(path)
    for block in (IDENTITY_SCHEMA, ACTIVITY_SCHEMA, ASSESSMENT_SCHEMA):
        for statement in block.strip().split(";"):
            if not statement.strip():
                continue
            # Identitetska šema nosi placeholder verzije ovih tabela.
            if any(statement.lstrip().startswith("CREATE TABLE " + name)
                   for name in ("learning_activity", "assessment_attempts",
                                "assessment_item_results")) and block is IDENTITY_SCHEMA:
                continue
            conn.execute(statement)
    conn.execute("INSERT INTO schema_migrations (version) VALUES (1)")
    conn.commit()
    conn.close()


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
    # Odvojene niti su daemon i prezivjele bi ovaj test; drenaza
    # sprjecava da zaostao upis obori sljedeci test.
    reporting_db.wait_for_pending_writes()
    reporting_db.set_database(None)


def rows(path, sql, params=()):
    conn = libsql.connect(path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def wait_for(path, table, expected, timeout=10.0):
    import threading
    waiter = threading.Event()
    for _ in range(int(timeout * 40)):
        if rows(path, "SELECT COUNT(*) FROM " + table)[0][0] >= expected:
            break
        waiter.wait(0.025)
    return rows(path, "SELECT COUNT(*) FROM " + table)[0][0]


def attempts(path):
    return rows(path, "SELECT id, student_id, source, assessment_type, "
                      "external_attempt_id, title, grade, area_name, lesson_id, "
                      "lesson_name, score_percent, correct_count, total_count, "
                      "started_at, completed_at, metadata_json "
                      "FROM assessment_attempts ORDER BY id")


def items(path):
    return rows(path, "SELECT attempt_id, item_key, ordinal, area_name, lesson_id, "
                      "lesson_name, difficulty, is_correct, hints_used, metadata_json "
                      "FROM assessment_item_results ORDER BY ordinal")


def enter(client, email=TEST_EMAIL):
    query = {student_identity.QUERY_PARAM: email} if email else {}
    response = client.get("/", query_string=query)
    body = response.get_data(as_text=True)
    marker = 'name="matbot-embed-token" content="'
    start = body.index(marker) + len(marker)
    return body[start:body.index('"', start)]


def start_exam(client, flask_app, token):
    from tests.test_kontrolni import EchoKontrolniLLM, start_payload

    flask_app.config["MATBOT_LLM"] = EchoKontrolniLLM()
    response = client.post("/api/ai-tutor/exam/start", json=start_payload(),
                           headers={auth.TOKEN_HEADER: token})
    return response.get_json()


def submit_exam(client, flask_app, token, exam_id, *, all_correct=True):
    from tests.test_kontrolni import start_payload

    store = flask_app.config["MATBOT_EXAM_STORE"]
    questions = store.get(start_payload()["session_id"])["questions"]
    answers = {}
    for index, question in enumerate(questions):
        if all_correct or index == 0:
            answers[question["id"]] = question["correct_option_id"]
        else:
            answers[question["id"]] = next(
                o["id"] for o in question["options"]
                if o["id"] != question["correct_option_id"])
    return client.post("/api/ai-tutor/exam/submit",
                       json={"session_id": start_payload()["session_id"],
                             "exam_id": exam_id, "answers": answers},
                       headers={auth.TOKEN_HEADER: token})


# ---------------------------------------------------------------------------
# 1-2) Generisanje
# ---------------------------------------------------------------------------
def test_ready_kontrolni_creates_one_incomplete_attempt(client, flask_app, fake_llm,
                                                        reporting):
    token = enter(client)
    started = start_exam(client, flask_app, token)
    assert started["status"] == "ready"

    wait_for(reporting, "assessment_attempts", 1)
    recorded = attempts(reporting)
    assert len(recorded) == 1
    row = recorded[0]
    assert row[2] == "matbot" and row[3] == "kontrolni"
    assert row[4] == started["exam_id"]
    assert row[6] == 6                              # grade
    assert row[7]                                   # area_name
    assert row[10] is None and row[11] is None      # score_percent / correct_count
    assert row[12] == 5                             # total_count = pitanja
    assert row[13] is not None                      # started_at
    assert row[14] is None                          # completed_at
    # Kontrolni obuhvata PET lekcija — nijedna nije "lekcija pokušaja".
    assert row[5] is None and row[8] is None and row[9] is None
    assert rows(reporting, "SELECT COUNT(*) FROM assessment_item_results")[0][0] == 0


def test_failed_closed_generation_creates_no_attempt(client, flask_app, fake_llm,
                                                     reporting):
    from matbot.llm import LLMUnavailable
    from tests.test_kontrolni import start_payload

    fake_llm.queue(LLMUnavailable("boom"))
    fake_llm.queue(LLMUnavailable("boom"))
    response = client.post("/api/ai-tutor/exam/start", json=start_payload(),
                           headers={auth.TOKEN_HEADER: enter(client)})

    assert response.get_json().get("status") != "ready"
    wait_for(reporting, "assessment_attempts", 1, timeout=1.0)
    assert attempts(reporting) == []


# ---------------------------------------------------------------------------
# 3-8) Predaja
# ---------------------------------------------------------------------------
def test_graded_submit_completes_the_same_attempt(client, flask_app, fake_llm,
                                                  reporting):
    token = enter(client)
    started = start_exam(client, flask_app, token)
    wait_for(reporting, "assessment_attempts", 1)

    graded = submit_exam(client, flask_app, token, started["exam_id"])
    assert graded.status_code == 200 and graded.get_json()["score"] == 5
    wait_for(reporting, "assessment_item_results", 5)

    recorded = attempts(reporting)
    assert len(recorded) == 1, "predaja ne smije napraviti drugi pokušaj"
    row = recorded[0]
    assert row[4] == started["exam_id"]
    assert row[10] == 100.0        # score_percent
    assert row[11] == 5            # correct_count
    assert row[12] == 5            # total_count
    assert row[13] is not None     # started_at očuvan iz generisanja
    assert row[14] is not None     # completed_at popunjen


def test_partial_score_is_exact(client, flask_app, fake_llm, reporting):
    token = enter(client)
    started = start_exam(client, flask_app, token)
    wait_for(reporting, "assessment_attempts", 1)

    graded = submit_exam(client, flask_app, token, started["exam_id"],
                         all_correct=False)
    body = graded.get_json()
    assert body["score"] == 1 and body["total"] == 5
    wait_for(reporting, "assessment_item_results", 5)

    row = attempts(reporting)[0]
    assert (row[11], row[12]) == (1, 5)
    assert row[10] == pytest.approx(20.0)


def test_every_graded_question_becomes_one_item_with_exact_outcome(
        client, flask_app, fake_llm, reporting):
    token = enter(client)
    started = start_exam(client, flask_app, token)
    wait_for(reporting, "assessment_attempts", 1)
    submit_exam(client, flask_app, token, started["exam_id"], all_correct=False)
    wait_for(reporting, "assessment_item_results", 5)

    recorded = items(reporting)
    assert len(recorded) == 5
    assert [r[1] for r in recorded] == ["q1", "q2", "q3", "q4", "q5"]
    assert [r[2] for r in recorded] == [1, 2, 3, 4, 5]
    # Prvo pitanje tačno, ostala netačna — tačno onako kako je predano.
    assert [r[7] for r in recorded] == [1, 0, 0, 0, 0]
    attempt_id = attempts(reporting)[0][0]
    assert {r[0] for r in recorded} == {attempt_id}
    # Kontrolni ne koristi Practice nagovještaje — nula je strukturno tačna.
    assert {r[8] for r in recorded} == {0}
    # Kurikularne oznake POSTOJE po pitanju i dolaze iz pohranjenog testa.
    assert all(r[4] for r in recorded), "lesson_id po pitanju"
    assert all(r[6] for r in recorded), "difficulty po pitanju"


# ---------------------------------------------------------------------------
# 9-11) Idempotentnost i redoslijed
# ---------------------------------------------------------------------------
def test_repeated_submit_creates_no_duplicates(client, flask_app, fake_llm,
                                               reporting):
    token = enter(client)
    started = start_exam(client, flask_app, token)
    wait_for(reporting, "assessment_attempts", 1)
    first = submit_exam(client, flask_app, token, started["exam_id"])
    wait_for(reporting, "assessment_item_results", 5)
    before = attempts(reporting)[0]

    # Ponovljena predaja: `run_submit` vraća POHRANJEN rezultat.
    second = submit_exam(client, flask_app, token, started["exam_id"])
    wait_for(reporting, "assessment_item_results", 6, timeout=1.0)

    assert first.get_json()["score"] == second.get_json()["score"]
    assert len(attempts(reporting)) == 1
    assert len(items(reporting)) == 5
    # Prvo vrijeme završetka se ČUVA, ne pomjera pri ponovljenoj isporuci.
    assert attempts(reporting)[0][14] == before[14]


def test_generated_write_arriving_after_completion_preserves_the_result(reporting):
    """DIO 5B: asinhroni upisi mogu stići obrnutim redom.

    Ovdje se generisanje namjerno izvršava POSLIJE ocjene. Nedestruktivan
    `COALESCE` upsert ne smije obrisati rezultat — a smije popuniti `started_at`
    koji ocjeni nije bio poznat."""
    from matbot.api import _kontrolni_attempt

    database = reporting_db.get_database()
    student_id = database.get_or_create_student(PROVIDER_THINKIFIC_EMAIL, TEST_EMAIL)
    exam_id = "exam-out-of-order"

    completed = _kontrolni_attempt(exam_id, grade=6, area_name="Razlomci",
                                   total_count=5, correct_count=4,
                                   score_percent=80,
                                   completed_at="2026-08-24 16:24:00")
    database.record_assessment_completed(student_id, completed, [
        {"item_key": "q1", "ordinal": 1, "is_correct": True},
        {"item_key": "q2", "ordinal": 2, "is_correct": False},
    ])
    row_after_completion = attempts(reporting)[0]
    assert row_after_completion[13] is None, "ocjena ne zna vrijeme početka"

    # Zakasnjeli upis generisanja NOSI SVOJE ISTORIJSKO vrijeme (16:20),
    # koje je RANIJE od vec upisanog zavrsetka (16:24).
    database.record_assessment_generated(student_id, _kontrolni_attempt(
        exam_id, grade=6, area_name="Razlomci", total_count=5,
        started_at="2026-08-24 16:20:00"))

    row = attempts(reporting)[0]
    assert len(attempts(reporting)) == 1
    assert (row[10], row[11], row[12]) == (80.0, 4, 5), "rezultat je obrisan"
    assert row[14] is not None, "completed_at je obrisan"
    assert row[13] == "2026-08-24 16:20:00", "started_at nije naknadno popunjen"
    assert row[14] == "2026-08-24 16:24:00", "completed_at je pomjeren"
    assert row[13] <= row[14], "hronologija je nemoguca"
    assert len(items(reporting)) == 2


def test_completion_without_prior_generation_is_valid(reporting):
    """DIO 5B: ocjena smije SAMA napraviti ispravan završen pokušaj."""
    from matbot.api import _kontrolni_attempt

    database = reporting_db.get_database()
    student_id = database.get_or_create_student(PROVIDER_THINKIFIC_EMAIL, TEST_EMAIL)
    database.record_assessment_completed(
        student_id, _kontrolni_attempt("exam-only-completed", grade=7,
                                       area_name="Geometrija", total_count=5,
                                       correct_count=3, score_percent=60,
                                       completed_at="2026-08-24 16:24:00"),
        [{"item_key": "q1", "ordinal": 1, "is_correct": True}])

    row = attempts(reporting)[0]
    assert row[4] == "exam-only-completed"
    assert (row[10], row[11], row[12]) == (60.0, 3, 5)
    assert row[14] is not None
    assert len(items(reporting)) == 1


def test_duplicate_item_insert_is_rejected_by_the_unique_contract(reporting):
    from matbot.api import _kontrolni_attempt

    database = reporting_db.get_database()
    student_id = database.get_or_create_student(PROVIDER_THINKIFIC_EMAIL, TEST_EMAIL)
    attempt = _kontrolni_attempt("exam-dup", grade=6, total_count=2,
                                 correct_count=1, score_percent=50)
    payload = [{"item_key": "q1", "ordinal": 1, "is_correct": True},
               {"item_key": "q1", "ordinal": 1, "is_correct": True}]

    database.record_assessment_completed(student_id, attempt, payload)
    database.record_assessment_completed(student_id, attempt, payload)

    assert len(attempts(reporting)) == 1
    assert len(items(reporting)) == 1


def test_partial_failure_leaves_no_half_written_assessment(reporting):
    """DIO 9: pad na drugom pitanju ne smije ostaviti pola testa.

    Drugi red ruši CHECK ograničenje (`is_correct` van {0,1} je nemoguć kroz
    naš kod, pa se ovdje pogađa NOT NULL `item_key`), a transakcija sve poništi."""
    from matbot.api import _kontrolni_attempt

    database = reporting_db.get_database()
    student_id = database.get_or_create_student(PROVIDER_THINKIFIC_EMAIL, TEST_EMAIL)
    with pytest.raises(reporting_db.ReportingUnavailable):
        database.record_assessment_completed(
            student_id, _kontrolni_attempt("exam-atomic", grade=6, total_count=2,
                                           correct_count=1, score_percent=50),
            [{"item_key": "q1", "ordinal": 1, "is_correct": True},
             {"item_key": None, "ordinal": 2, "is_correct": False}])

    assert attempts(reporting) == [], "pokušaj je ostao uprkos padu"
    assert items(reporting) == []


# ---------------------------------------------------------------------------
# 12) Anoniman učenik
# ---------------------------------------------------------------------------
def test_anonymous_kontrolni_persists_nothing(client, flask_app, fake_llm,
                                              reporting):
    token = enter(client, email=None)
    started = start_exam(client, flask_app, token)
    assert started["status"] == "ready"
    graded = submit_exam(client, flask_app, token, started["exam_id"])

    assert graded.status_code == 200 and graded.get_json()["score"] == 5
    wait_for(reporting, "assessment_attempts", 1, timeout=1.0)
    assert attempts(reporting) == [] and items(reporting) == []
    assert rows(reporting, "SELECT COUNT(*) FROM students")[0][0] == 0


# ---------------------------------------------------------------------------
# 13-14) Izolacija kvara
# ---------------------------------------------------------------------------
def test_unavailable_db_leaves_the_graded_result_untouched(client, flask_app,
                                                           fake_llm, monkeypatch):
    from tests.test_reporting_db_failure_policy import ExplodingDatabase

    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://test.invalid")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token-not-real")
    reporting_db.set_database(ExplodingDatabase())
    try:
        token = enter(client)
        started = start_exam(client, flask_app, token)
        graded = submit_exam(client, flask_app, token, started["exam_id"])
    finally:
        reporting_db.set_database(None)

    body = graded.get_json()
    assert graded.status_code == 200
    assert body["status"] == "graded" and body["score"] == 5 and body["total"] == 5
    raw = graded.get_data(as_text=True)
    for forbidden in ("assessment", "turso", "reporting", "Traceback"):
        assert forbidden not in raw.lower()


def test_hung_db_keeps_the_graded_response_bounded(client, flask_app, fake_llm,
                                                   monkeypatch):
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
        started = start_exam(client, flask_app, token)
        started_at = time.perf_counter()
        graded = submit_exam(client, flask_app, token, started["exam_id"])
        elapsed = time.perf_counter() - started_at
    finally:
        hanging.close()
        reporting_db.set_database(None)
        reporting_db.shutdown()

    assert graded.status_code == 200 and graded.get_json()["score"] == 5
    assert elapsed < 2.5, "zaglavljen izvještajni upis je zadržao ocjenjivanje"


# ---------------------------------------------------------------------------
# 15-16) Privatnost
# ---------------------------------------------------------------------------
def test_assessment_rows_contain_no_content_or_identity(client, flask_app,
                                                        fake_llm, reporting):
    token = enter(client)
    started = start_exam(client, flask_app, token)
    wait_for(reporting, "assessment_attempts", 1)
    submit_exam(client, flask_app, token, started["exam_id"], all_correct=False)
    wait_for(reporting, "assessment_item_results", 5)

    dump = str(rows(reporting, "SELECT * FROM assessment_attempts")) + \
        str(rows(reporting, "SELECT * FROM assessment_item_results"))

    assert "@" not in dump, "e-mail ili adresa u tabelama procjene"
    assert TEST_EMAIL not in dump
    # Ni tekst pitanja, ni tačan odgovor, ni izabrana opcija.
    store = flask_app.config["MATBOT_EXAM_STORE"]
    from tests.test_kontrolni import start_payload
    for question in store.get(start_payload()["session_id"])["questions"]:
        assert question["text"][:30] not in dump
        assert question["expected_answer"] not in dump
        if question.get("solution"):
            assert question["solution"][:30] not in dump
    for token_text in ("sk-", "Bearer", "SELECT ", "prompt"):
        assert token_text not in dump
    # metadata_json ostaje NULL svuda — nema "korisnog" slobodnog teksta.
    assert all(row[15] is None for row in attempts(reporting))
    assert all(row[9] is None for row in items(reporting))


# ---------------------------------------------------------------------------
# 17) Isti učenik kroz dvije sesije
# ---------------------------------------------------------------------------
def test_two_sessions_share_one_student_id(client, flask_app, fake_llm, reporting):
    token = enter(client)
    first = start_exam(client, flask_app, token)
    wait_for(reporting, "assessment_attempts", 1)
    submit_exam(client, flask_app, token, first["exam_id"])
    wait_for(reporting, "assessment_item_results", 5)

    # Drugi pregledač, ista adresa -> novi test, ISTI učenik.
    second_token = enter(client)
    second = start_exam(client, flask_app, second_token)
    wait_for(reporting, "assessment_attempts", 2)

    assert first["exam_id"] != second["exam_id"]
    assert rows(reporting, "SELECT COUNT(*) FROM students")[0][0] == 1
    owners = {row[1] for row in attempts(reporting)}
    assert len(owners) == 1


def test_foreign_key_binds_items_to_their_attempt(reporting):
    """`ON DELETE CASCADE` je dio produkcijske šeme — dokaz da veza stvarno radi."""
    from matbot.api import _kontrolni_attempt

    database = reporting_db.get_database()
    student_id = database.get_or_create_student(PROVIDER_THINKIFIC_EMAIL, TEST_EMAIL)
    database.record_assessment_completed(
        student_id, _kontrolni_attempt("exam-fk", grade=6, total_count=1,
                                       correct_count=1, score_percent=100),
        [{"item_key": "q1", "ordinal": 1, "is_correct": True}])
    attempt_id = attempts(reporting)[0][0]

    conn = libsql.connect(reporting)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("DELETE FROM assessment_attempts WHERE id = ?", (attempt_id,))
    conn.commit()
    conn.close()

    assert items(reporting) == []
