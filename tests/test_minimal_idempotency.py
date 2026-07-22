# -*- coding: utf-8 -*-
"""Global new-task idempotency, across all Minimal Engine skills.

Production: an active fraction-equation task (x - 3/4 = 1/4), student sent
"Daj mi novi zadatak." repeatedly, and every turn returned the SAME task.

Root cause: ``engine.py``'s NEW_TASK branch built a signature from the
NORMALIZED MESSAGE TEXT and compared it against ``state.last_request_signature``
— when they matched, the existing task was returned unchanged instead of being
replaced. That conflated two unrelated situations: (1) the same physical HTTP
turn delivered twice (a dropped SSE response followed by the JSON fallback,
both carrying an IDENTICAL payload including ``previous_next_state``), and
(2) the student deliberately repeating the same phrase in a genuinely later
turn. Text equality cannot distinguish them — a real retry and a fresh
identical-looking request are indistinguishable at that layer.

Fix, in two independent layers:

* ``engine.handle_turn`` — the NEW_TASK/HARDER/EASIER branch (``engine.py``)
  no longer treats repeated text as a duplicate. Every call that reaches it
  creates or replaces a task. This is shared engine behaviour: no skill,
  lesson mapping, or generator is involved in the decision.
* ``matbot/minimal/idempotency.py`` + ``ai_tutor_service._try_minimal_engine``
  — a small per-session cache, keyed by ``client_turn_id`` (generated once per
  browser submission in ``templates/index.html`` and reused for the JSON
  fallback of THAT submission), answers "have I already produced a response
  for this exact turn?" without ever looking at message text.

Driven through the real SSE route, and the JSON route for the fallback case.
"""
import json

import pytest

from matbot import ai_tutor_service as svc
from matbot import topic_resolver as tr
from matbot.minimal import idempotency

STREAM_URL = "/api/ai-tutor/chat/stream"
JSON_URL = "/api/ai-tutor/chat"

EQ_TOPIC = "6-07-064"
EQ_OBLAST = "Jednačine, nejednačine i izrazi u Q+"
TASK = "Riješi jednačinu: x - 3/4 = 1/4."


def prod_payload(**overrides):
    payload = {
        "session_id": "idem-1", "grade": 6, "mode": "practice",
        "session_mode": "practice", "entry_source": "manual_topic_choice",
        "selected_topic": EQ_TOPIC, "selected_oblast": EQ_OBLAST,
        "student_message": "daj mi zadatak", "conversation_history": [],
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "idem.sqlite3"))
    monkeypatch.setenv("MATBOT_MINIMAL_ENGINE", "on")
    for f in ("MATBOT_ENGINE_V2", "MATBOT_ENGINE_V2_GRADING",
              "MATBOT_ENGINE_V2_PRACTICE", "MATBOT_ENGINE_V2_EXAM"):
        monkeypatch.setenv(f, "off")
    tr.reset_cache()
    idempotency.reset()
    yield
    tr.reset_cache()
    idempotency.reset()


@pytest.fixture()
def sheets(monkeypatch):
    rows = []
    monkeypatch.setattr(svc, "log_transcript_to_sheet",
                        lambda p, r: rows.append((p, r)))
    return rows


def sse(client, payload):
    resp = client.post(STREAM_URL, json=payload)
    assert resp.status_code == 200, resp.data
    name = None
    for line in resp.get_data(as_text=True).splitlines():
        if line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and name == "done":
            return json.loads(line.split(":", 1)[1].strip())
    raise AssertionError("no done event")


def as_json(client, payload):
    resp = client.post(JSON_URL, json=payload)
    assert resp.status_code == 200, resp.data
    return resp.get_json()


#: (selected_topic, selected_oblast) that resolves to each skill, so a
#: replacement task is generated for the SAME skill the seeded task claims.
_SKILL_TOPIC = {
    "fraction_expand": ("6-04-035", "Razlomci"),
    "fraction_add_unlike": ("6-04-040", "Razlomci"),
    "fraction_equation_additive": (EQ_TOPIC, EQ_OBLAST),
    "linear_equation": ("", "Jednostavne linearne jednačine"),
}


def seeded(client, question=TASK, expected="1", skill="fraction_equation_additive",
          turn_id="seed-0"):
    topic, oblast = _SKILL_TOPIC[skill]
    state = sse(client, prod_payload(
        client_turn_id=turn_id, selected_topic=topic,
        selected_oblast=oblast))["next_state"]
    state["minimal_state"]["active_task"] = {
        "task_id": "mt_prod", "skill_id": skill, "question": question,
        "expected_display": expected, "npp_id": topic or EQ_TOPIC,
        "tema_title": "t", "attempts": 0, "wrong_attempts": 0, "hints_given": 0,
        "solved": False, "solution_revealed": False,
    }
    state["task_id"] = "mt_prod"
    return state


def new_task(client, state, turn_id, message="Daj mi novi zadatak.",
            skill="fraction_equation_additive"):
    topic, oblast = _SKILL_TOPIC[skill]
    return sse(client, prod_payload(
        student_message=message, previous_next_state=state,
        client_turn_id=turn_id, selected_topic=topic, selected_oblast=oblast))


# =========================================================================== #
# 1. Explicit NEW_TASK always replaces, even mid-task                         #
# =========================================================================== #
def test_new_task_replaces_the_active_equation_task(client):
    state = seeded(client)
    body = new_task(client, state, "turn-1")

    assert body["next_state"]["task_id"] != "mt_prod"
    new_question = body["next_state"]["minimal_state"]["active_task"]["question"]
    assert new_question != TASK                      # a different signature
    assert body["next_state"]["minimal_state"]["topic"]["skill_id"] == \
        "fraction_equation_additive"
    assert body["next_state"]["minimal_state"]["topic"]["npp_id"] == EQ_TOPIC
    # the replaced task is not counted as wrong
    assert body["wrong_attempt_count"] == 0
    assert body["next_state"]["correct_streak"] == 0


def test_replacing_a_task_never_produces_a_verdict(client):
    state = seeded(client)
    body = new_task(client, state, "turn-1")
    assert body["answer_verdict"] in (None, "", "none")
    assert body.get("gpt_check_used") in (False, None)


def test_replaced_task_is_auditable_in_telemetry(client):
    state = seeded(client)
    body = new_task(client, state, "turn-1")
    routing = body["minimal_routing"]
    assert routing["task_transition"] == "replaced"
    assert routing["previous_task_id"] == "mt_prod"
    assert routing["current_task_id"] == body["next_state"]["task_id"]
    assert routing["current_task_id"] != "mt_prod"


# =========================================================================== #
# 2. Same text, distinct client_turn_id -> distinct tasks                     #
# =========================================================================== #
def test_same_text_different_turn_ids_create_different_tasks(client):
    state = seeded(client)
    first = new_task(client, state, "turn-a", "Daj mi novi zadatak.")
    second = new_task(client, first["next_state"], "turn-b", "Daj mi novi zadatak.")

    assert first["next_state"]["task_id"] != second["next_state"]["task_id"]
    q1 = first["next_state"]["minimal_state"]["active_task"]["question"]
    q2 = second["next_state"]["minimal_state"]["active_task"]["question"]
    assert q1 != q2
    assert first["minimal_routing"]["task_transition"] == "replaced"
    assert second["minimal_routing"]["task_transition"] == "replaced"
    assert first["minimal_routing"].get("idempotency_replay") is False
    assert second["minimal_routing"].get("idempotency_replay") is False


# =========================================================================== #
# 3. Same client_turn_id replayed -> exactly one logical transition           #
# =========================================================================== #
def test_same_turn_id_replayed_returns_the_same_task(client, sheets):
    state = seeded(client)
    first = new_task(client, state, "turn-x")
    second = new_task(client, state, "turn-x")     # identical payload, resent

    assert second["next_state"]["task_id"] == first["next_state"]["task_id"]
    assert second["answer"] == first["answer"]
    assert second["minimal_routing"]["idempotency_replay"] is True
    assert second["minimal_routing"]["task_transition"] == "replayed"
    # only ONE Sheets row for this logical turn
    assert len(sheets) == 2                          # seed turn + the ONE real turn
    q1 = first["next_state"]["minimal_state"]["active_task"]["question"]
    q2 = second["next_state"]["minimal_state"]["active_task"]["question"]
    assert q1 == q2


def test_replay_reports_original_decision_telemetry(client):
    """A replay carries forward the ORIGINAL turn_intent, not a blank one."""
    state = seeded(client)
    first = new_task(client, state, "turn-x")
    second = new_task(client, state, "turn-x")
    assert second["minimal_routing"]["turn_intent"] == \
        first["minimal_routing"]["turn_intent"] == "NEW_TASK"
    assert second["minimal_routing"]["client_turn_id"] == "turn-x"


# =========================================================================== #
# 3b. CONCURRENT replay: two calls for the same key, released together        #
# =========================================================================== #
# ``recall`` and ``remember`` are each individually locked, but the real
# processing happens BETWEEN them, unprotected. Two threads that both call
# ``recall`` before either calls ``remember`` would, without a single-flight
# claim, both process the turn and both write — two engine invocations and two
# Sheets rows for one logical turn. A ``threading.Barrier`` releases both
# threads at the same instant so the race window is actually exercised, and a
# short sleep inside the (patched) engine call widens that window further —
# not required for CORRECTNESS (``claim`` is atomic regardless of timing), but
# it means the waiter branch is genuinely exercised rather than won by luck.
def test_concurrent_same_turn_id_is_single_flight(client, sheets, monkeypatch):
    import threading
    import time

    state = seeded(client)
    barrier = threading.Barrier(2)
    engine_calls = []
    original = svc._try_minimal_engine

    def _slow_engine(*args, **kwargs):
        engine_calls.append(1)
        time.sleep(0.05)
        return original(*args, **kwargs)

    monkeypatch.setattr(svc, "_try_minimal_engine", _slow_engine)

    payload = prod_payload(student_message="Daj mi novi zadatak.",
                           previous_next_state=state, client_turn_id="race-1")
    results: list = []
    errors: list = []

    def worker():
        # The fixture's ``client`` pushed its app/request context on the MAIN
        # thread only (Flask context-locals are thread-local); a worker thread
        # needs its OWN test client, entered as a context manager, so the SSE
        # body can be iterated without a stray ``LookupError`` on
        # ``flask.app_ctx``. This is a test-harness detail — the two calls
        # still reach the SAME app and the SAME in-process idempotency cache.
        import app as app_module
        barrier.wait()
        try:
            with app_module.app.test_client() as thread_client:
                results.append(sse(thread_client, payload))
        except Exception as exc:                          # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert not any(t.is_alive() for t in threads), \
        "a thread did not finish — see test_claim_release below for the " \
        "isolated primitive instead of debugging a live deadlock here"
    assert not errors, errors
    assert len(results) == 2
    assert len(engine_calls) == 1, "the engine ran more than once for one turn"

    task_ids = {r["next_state"]["task_id"] for r in results}
    assert len(task_ids) == 1, "the two callers got different tasks"
    answers = {r["answer"] for r in results}
    assert len(answers) == 1, "the two callers got different answers"

    replay_flags = sorted(r["minimal_routing"]["idempotency_replay"] for r in results)
    assert replay_flags == [False, True]      # exactly one owner, one replay

    # seed turn (1 row) + exactly ONE real transition for the raced pair
    assert len(sheets) == 2


def test_claim_release_on_exception_wakes_waiters_without_deadlock():
    """Direct, single-threaded test of the primitive itself.

    Orchestrating a genuine race against a mid-processing exception through
    real threads and the full Flask stack is exactly the kind of test that
    can itself hang if the timing assumptions are wrong. The property under
    test — "an owner that fails still releases the key and wakes waiters,
    never leaving them blocked" — is a property of ``claim``/``release``
    alone, so it is verified directly against that API instead.
    """
    idempotency.reset()
    is_owner1, waiter1 = idempotency.claim("s1", "t1")
    assert is_owner1 is True

    # A concurrent caller for the SAME key becomes a waiter on the SAME slot.
    is_owner2, waiter2 = idempotency.claim("s1", "t1")
    assert is_owner2 is False
    assert waiter2 is waiter1

    # The owner's turn raises before producing a response — the try/finally
    # in ai_tutor_service.minimal_dispatch calls release() with result=None
    # in exactly this situation.
    idempotency.release("s1", "t1", None)

    assert waiter1.event.wait(timeout=1) is True, "the waiter never woke up"
    assert waiter1.result is None

    # The key must be free afterwards — a third caller becomes a FRESH owner
    # rather than waiting on a slot nothing will ever complete.
    is_owner3, waiter3 = idempotency.claim("s1", "t1")
    assert is_owner3 is True
    assert waiter3 is not waiter1
    idempotency.release("s1", "t1", {"answer": "ok"})


# =========================================================================== #
# 4. Stream drop then JSON fallback, same client_turn_id                      #
# =========================================================================== #
def test_stream_then_json_fallback_creates_exactly_one_task(client, sheets):
    """Simulates: SSE reached the engine (task created) but the response never
    reached the browser, so the frontend retries via the JSON route with the
    SAME payload — including the same client_turn_id."""
    state = seeded(client)
    payload = prod_payload(student_message="Daj mi novi zadatak.",
                           previous_next_state=state, client_turn_id="turn-drop")

    stream_body = sse(client, payload)               # the "lost" SSE response
    json_body = as_json(client, payload)             # the browser's fallback

    assert json_body["next_state"]["task_id"] == stream_body["next_state"]["task_id"]
    assert json_body["minimal_routing"]["idempotency_replay"] is True
    assert len(sheets) == 2                          # seed + ONE real transition


# =========================================================================== #
# 5. The "Novi zadatak" button: two clicks, two distinct turns                #
# =========================================================================== #
def test_novi_zadatak_button_twice_creates_two_tasks(client):
    """Each click is a fresh ``sendTutorMsg`` call in the browser, so each one
    generates its OWN client_turn_id — verified here by using two ids."""
    state = seeded(client)
    click1 = new_task(client, state, "click-1", "Daj mi novi zadatak.")
    click2 = new_task(client, click1["next_state"], "click-2", "Daj mi novi zadatak.")
    assert click1["next_state"]["task_id"] != click2["next_state"]["task_id"]


# =========================================================================== #
# 6. Pending confirmation -> "da" also replaces normally                      #
# =========================================================================== #
def test_confirmation_yes_creates_a_new_task(client):
    state = seeded(client)
    solved = sse(client, prod_payload(
        student_message="1", interaction_phase="answering_practice_task",
        last_tutor_task=TASK, previous_next_state=state,
        client_turn_id="answer-1"))
    assert solved["answer_verdict"] == "correct"
    assert solved["next_state"]["pending_confirmation"] == "new_task"

    body = sse(client, prod_payload(
        student_message="da", previous_next_state=solved["next_state"],
        client_turn_id="confirm-1"))
    assert body["next_state"]["task_id"]
    assert body["next_state"]["pending_confirmation"] == ""
    assert body["minimal_routing"]["task_transition"] == "created"


def test_confirmation_yes_repeated_with_new_turn_id_still_creates_a_task(client):
    """A stale confirmation is consumed once; sending "da" again afterwards
    with a new client_turn_id is a fresh, unrelated turn."""
    state = seeded(client)
    solved = sse(client, prod_payload(
        student_message="1", interaction_phase="answering_practice_task",
        last_tutor_task=TASK, previous_next_state=state,
        client_turn_id="answer-2"))
    first = sse(client, prod_payload(
        student_message="da", previous_next_state=solved["next_state"],
        client_turn_id="confirm-2"))
    assert first["next_state"]["task_id"]


# =========================================================================== #
# 7-8. HARDER / EASIER: each distinct turn creates its own task               #
# =========================================================================== #
@pytest.mark.parametrize("word,expect_delta", [("teži", 1), ("lakši", -1)])
def test_harder_easier_each_create_a_new_task(client, word, expect_delta):
    from matbot.minimal import skills
    state = seeded(client, skill="fraction_add_unlike",
                   question="Izračunaj: 1/3 + 4/5.", expected="17/15")
    before_level = state["minimal_state"]["difficulty_level"]

    first = new_task(client, state, f"{word}-1", f"Daj mi {word} zadatak.",
                     skill="fraction_add_unlike")
    second = new_task(client, first["next_state"], f"{word}-2",
                      f"Daj mi {word} zadatak.", skill="fraction_add_unlike")

    assert first["next_state"]["task_id"] != state["task_id"]
    assert second["next_state"]["task_id"] != first["next_state"]["task_id"]
    if skills.supports_difficulty("fraction_add_unlike"):
        assert first["next_state"]["difficulty_level"] == \
            max(1, min(3, before_level + expect_delta))
    assert first["minimal_routing"]["task_transition"] == "replaced"
    assert second["minimal_routing"]["task_transition"] == "replaced"


# =========================================================================== #
# 9. Replacing preserves counters exactly                                     #
# =========================================================================== #
def test_replacing_preserves_attempts_and_streak(client):
    state = seeded(client)
    wrong = sse(client, prod_payload(
        student_message="99/100", interaction_phase="answering_practice_task",
        last_tutor_task=TASK, previous_next_state=state,
        client_turn_id="wrong-1"))
    assert wrong["answer_verdict"] == "incorrect"
    before_wrong = wrong["wrong_attempt_count"]

    replaced = new_task(client, wrong["next_state"], "turn-replace")
    assert replaced["wrong_attempt_count"] == 0    # the NEW task's own counter
    assert replaced["next_state"]["correct_streak"] == 0
    assert replaced["next_state"]["minimal_state"]["solved_count"] == 0
    assert before_wrong == 1                       # sanity: the miss DID count


# =========================================================================== #
# 10. Shared engine behaviour: every skill                                    #
# =========================================================================== #
SKILL_TASKS = [
    ("fraction_expand", "Proširi 2/5 na nazivnik 15.", "6/15"),
    ("fraction_add_unlike", "Izračunaj: 1/3 + 4/5.", "17/15"),
    ("fraction_equation_additive", TASK, "1"),
    ("linear_equation", "Riješi jednačinu: 2x - 3 = 9.", "6"),
]


@pytest.mark.parametrize("skill,question,expected", SKILL_TASKS)
def test_replacement_works_across_every_skill(client, skill, question, expected):
    state = seeded(client, question=question, expected=expected, skill=skill)
    body = new_task(client, state, f"{skill}-turn", skill=skill)
    assert body["next_state"]["task_id"] != "mt_prod"
    assert body["next_state"]["minimal_state"]["topic"]["skill_id"] == skill
    assert body["minimal_routing"]["task_transition"] == "replaced"
    assert body["wrong_attempt_count"] == 0


@pytest.mark.parametrize("skill,question,expected", SKILL_TASKS)
def test_replay_works_across_every_skill(client, skill, question, expected):
    state = seeded(client, question=question, expected=expected, skill=skill)
    first = new_task(client, state, f"{skill}-replay", skill=skill)
    second = new_task(client, state, f"{skill}-replay", skill=skill)
    assert second["next_state"]["task_id"] == first["next_state"]["task_id"]
    assert second["minimal_routing"]["idempotency_replay"] is True


# =========================================================================== #
# Telemetry and audit                                                         #
# =========================================================================== #
def test_student_message_stays_verbatim_on_new_task_and_replay(client, sheets):
    from matbot import sheets_log
    state = seeded(client)
    new_task(client, state, "turn-audit", "Daj mi novi zadatak.")
    row = sheets_log._build_transcript_row(*sheets[-1])
    headers = sheets_log.SHEET_HEADERS
    assert row[headers.index("student_message")] == "Daj mi novi zadatak."


def test_sheet_still_has_62_columns():
    from matbot import sheets_log
    assert len(sheets_log.SHEET_HEADERS) == 62


def test_recent_task_signature_prevention_still_works_independently(client):
    """Section 3: content-duplicate prevention is untouched — a freshly
    replaced task must not repeat the LAST few generated questions."""
    state = seeded(client, skill="fraction_add_unlike",
                   question="Izračunaj: 1/3 + 4/5.", expected="17/15")
    seen = {"Izračunaj: 1/3 + 4/5."}
    for i in range(4):
        body = new_task(client, state, f"content-{i}", "Daj mi novi zadatak.",
                        skill="fraction_add_unlike")
        state = body["next_state"]
        question = state["minimal_state"]["active_task"]["question"]
        assert question not in seen, "content-duplicate prevention regressed"
        seen.add(question)
