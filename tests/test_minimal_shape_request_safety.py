# -*- coding: utf-8 -*-
r"""Shape-request routing was too broad: any concrete numeric equation, with
no other signal, was treated as a request for a NEW task of that shape.

Production risk: during an active equation task, a student who submits a
TRANSFORMED equation or an intermediate working step (e.g. "5/6 - x = 1/3"
while solving something else, or "x - 6 = 9" as scratch work) would have had
their in-progress task silently replaced.

Old pattern (too broad), in ``intent.py``:

    _SHAPE_X_NUMERIC_RE = re.compile(rf"\bx\s*([+\-−])\s*{_NUM}\s*=\s*{_NUM}\b")
    _SHAPE_A_NUMERIC_RE = re.compile(rf"\b{_NUM}\s*([+\-−])\s*x\s*=\s*{_NUM}\b")

matched by SYNTAX ALONE — no task-request cue required. Any bare concrete
equation ("x - 6 = 9", "5/6 - x = 1/3", "x + 1/3 = 5/6") satisfied it.

Fixed distinction:

* LITERAL placeholder shapes ("a-x=b", using the letters a/x/b themselves)
  have no other reading — they identify a requested shape on their own.
* CONCRETE numeric equations ("5/6 - x = 1/3") identify a requested shape
  ONLY together with an explicit task-request cue in the SAME message
  ("daj mi primjer", "zelim probati", "u ovom obliku", ...).
* A BARE concrete equation, with no cue, is no longer a shape request at
  all — it is left for the ordinary answer/working-step route while a task
  is active, or for a safe clarification when none is.

Driven through the real SSE route the browser uses.
"""
import json

import pytest

from matbot import ai_tutor_service as svc
from matbot import topic_resolver as tr
from matbot.minimal.intent import (
    TurnIntent,
    classify,
    is_bare_equation_statement,
    parse_shape_request,
)

STREAM_URL = "/api/ai-tutor/chat/stream"
EQ_TOPIC = "6-07-064"
EQ_OBLAST = "Jednačine, nejednačine i izrazi u Q+"
TASK = "Riješi jednačinu: x - 3/4 = 1/4."


def prod_payload(**overrides):
    payload = {
        "session_id": "safe-1", "grade": 6, "mode": "practice",
        "session_mode": "practice", "entry_source": "manual_topic_choice",
        "selected_topic": EQ_TOPIC, "selected_oblast": EQ_OBLAST,
        "student_message": "daj mi zadatak", "conversation_history": [],
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "safe.sqlite3"))
    monkeypatch.setenv("MATBOT_MINIMAL_ENGINE", "on")
    for f in ("MATBOT_ENGINE_V2", "MATBOT_ENGINE_V2_GRADING",
              "MATBOT_ENGINE_V2_PRACTICE", "MATBOT_ENGINE_V2_EXAM"):
        monkeypatch.setenv(f, "off")
    tr.reset_cache()
    yield
    tr.reset_cache()


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


def seeded(client, question=TASK, expected="1", skill="fraction_equation_additive",
          selected_topic=EQ_TOPIC, selected_oblast=EQ_OBLAST, turn_id="seed-0"):
    state = sse(client, prod_payload(
        client_turn_id=turn_id, selected_topic=selected_topic,
        selected_oblast=selected_oblast))["next_state"]
    state["minimal_state"]["active_task"] = {
        "task_id": "mt_prod", "skill_id": skill, "question": question,
        "expected_display": expected, "npp_id": selected_topic or EQ_TOPIC,
        "tema_title": "t", "attempts": 0, "wrong_attempts": 0,
        "hints_given": 0, "solved": False, "solution_revealed": False,
    }
    state["task_id"] = "mt_prod"
    return state


def turn(client, state, message, question=TASK, turn_id="t",
        selected_topic=EQ_TOPIC, selected_oblast=EQ_OBLAST):
    return sse(client, prod_payload(
        student_message=message, interaction_phase="answering_practice_task",
        last_tutor_task=question, previous_next_state=state,
        client_turn_id=turn_id, selected_topic=selected_topic,
        selected_oblast=selected_oblast))


# =========================================================================== #
# 1. Placeholder shapes always identify a requested shape                     #
# =========================================================================== #
@pytest.mark.parametrize("message,expected_shape", [
    ("a-x=b", "a_minus_x"), ("x-a=b", "x_minus_a"),
    ("a+x=b", "a_plus_x"), ("x+a=b", "x_plus_a"),
    ("a−x=b", "a_minus_x"),                       # Unicode minus
])
def test_placeholder_shapes_are_always_requests(message, expected_shape):
    assert parse_shape_request(message) == expected_shape
    assert classify(message, has_active_task=True).intent == TurnIntent.NEW_TASK


# =========================================================================== #
# 2. Concrete equations require an explicit cue                               #
# =========================================================================== #
@pytest.mark.parametrize("message", [
    "daj mi primjer 5/6 - x = 1/3",
    "zelim probati zadatak oblika 5/6 - x = 1/3",
    "hocu jednacinu gdje je razlomak minus x",
    "zelim da probam oblik a-x=b",
    "zelim uraditi zadatak a-x=b u formi ovoj",
])
def test_cued_concrete_equations_are_shape_requests(message):
    assert parse_shape_request(message) == "a_minus_x", message
    assert classify(message, has_active_task=True).intent == TurnIntent.NEW_TASK


@pytest.mark.parametrize("message", [
    "x - 6 = 9",
    "5/6 - x = 1/3",
    "x + 1/3 = 5/6",
])
def test_bare_concrete_equations_are_not_shape_requests(message):
    assert parse_shape_request(message) is None, message
    assert is_bare_equation_statement(message) is True, message


def test_cue_alone_without_an_equation_is_not_a_shape_request():
    """The cue phrase matters only WITH a concrete equation attached."""
    assert parse_shape_request("daj mi primjer") is None
    assert parse_shape_request("zelim probati") is None


# =========================================================================== #
# 3. Bare equations mid-task stay on the ordinary answer route                #
# =========================================================================== #
def test_active_fraction_equation_plus_bare_equation_is_not_new_task(client):
    state = seeded(client)
    body = turn(client, state, "5/6 - x = 1/3", turn_id="bare-1")

    assert body["next_state"]["task_id"] == "mt_prod"      # NOT replaced
    assert body["minimal_routing"]["turn_intent"] == "ANSWER"
    assert body["minimal_routing"]["requested_task_shape"] == ""
    assert body["minimal_routing"]["task_transition"] == ""


def test_active_linear_equation_plus_bare_equation_is_not_new_task(client):
    state = seeded(client, question="Riješi jednačinu: 2x - 3 = 9.",
                  expected="6", skill="linear_equation",
                  selected_topic="", selected_oblast="Jednostavne linearne jednačine")
    body = turn(client, state, "x - 6 = 9",
               question="Riješi jednačinu: 2x - 3 = 9.", turn_id="bare-2",
               selected_topic="", selected_oblast="Jednostavne linearne jednačine")

    assert body["next_state"]["task_id"] == "mt_prod"      # NOT replaced
    assert body["minimal_routing"]["turn_intent"] == "ANSWER"
    assert body["minimal_routing"]["requested_task_shape"] == ""


# =========================================================================== #
# 4. No active task: a safe clarification, never a silent task or a verdict   #
# =========================================================================== #
@pytest.mark.parametrize("message", ["5/6 - x = 1/3", "x - 6 = 9"])
def test_no_active_task_plus_bare_equation_gets_a_clarification(client, message):
    body = sse(client, prod_payload(student_message=message,
                                    client_turn_id=f"clarify-{message}"))
    assert body["next_state"]["task_id"] is None           # no task created
    assert body["answer_verdict"] in (None, "", "none")
    assert body["next_state"]["pending_confirmation"] == "task_or_explanation"
    assert body["wrong_attempt_count"] in (None, 0)
    # a genuine clarifying QUESTION, not a fabricated grading response
    assert "?" in body["answer"]


def test_clarification_does_not_touch_progress_counters(client):
    body = sse(client, prod_payload(student_message="5/6 - x = 1/3",
                                    client_turn_id="clarify-counters"))
    state = body["next_state"]
    assert state["correct_streak"] == 0
    assert state["minimal_state"]["solved_count"] == 0
    assert state["hint_count"] == 0


# =========================================================================== #
# 5. The exact production shape requests keep working                        #
# =========================================================================== #
@pytest.mark.parametrize("message", [
    "zelim da probam oblik a-x=b",
    "zelim uraditi zadatak a-x=b u formi ovoj",
    "a−x=b",
])
def test_production_shape_requests_still_create_a_minus_x_tasks(client, message):
    state = seeded(client)
    body = turn(client, state, message, turn_id=f"prod-{message}")

    from matbot.minimal import solution_facts as sf
    new_question = body["next_state"]["minimal_state"]["active_task"]["question"]
    assert sf.resolve_equation_facts(new_question).shape == "a-x=b", new_question
    assert body["next_state"]["task_id"] != "mt_prod"
    assert body["minimal_routing"]["requested_task_shape"] == "a_minus_x"
    assert body["minimal_routing"]["task_transition"] == "replaced"
    assert body["answer_verdict"] in (None, "", "none")


# =========================================================================== #
# 6. A genuinely wrong numeric answer still counts normally                   #
# =========================================================================== #
def test_wrong_numeric_answer_still_counts(client):
    state = seeded(client)
    body = turn(client, state, "99/100", turn_id="wrong-real")
    assert body["answer_verdict"] == "incorrect"
    assert body["wrong_attempt_count"] == 1
    assert body["total_attempt_count"] == 1
    assert body["next_state"]["task_id"] == "mt_prod"
    assert body["minimal_routing"]["requested_task_shape"] == ""
