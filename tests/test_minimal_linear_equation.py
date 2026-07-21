# -*- coding: utf-8 -*-
"""linear_equation production session (canonical topic 6-07-064).

Three defects, all reproduced here through the SSE route the browser uses:

1. TOPIC FIDELITY. The selected lesson is
   "Jednačine s razlomcima oblika x ± a = b i a ± x = b", but the generator
   produced generic integer equations (2x + 5 = 7, 6x + 3 = 39, ...).
   Cause: ``skills.resolve_topic`` found no ``tema_ids`` binding for 6-07-064
   and fell through to the keyword pass, where the generic ``linear_equation``
   skill matches the substring "jednacin" in that tema title.
2. WRONG HINT. For ``2x - 3 = 9`` the hint said "Šta moraš oduzeti s obje
   strane da x ostane sam?" — the student must ADD 3, and x is not alone
   afterwards. Cause: ``renderer._hint_text`` clamped the level to the last
   entry of a hardcoded two-item pool that names one fixed operation.
3. SOLUTION_REQUEST. "ne znam hajde ga ti uradi" classified as HELP, because
   ``_SOLUTION_REQUEST_RE`` only matched "uradi ti", not the reversed
   "ti uradi".
"""
import json
from fractions import Fraction

import pytest

from matbot import ai_tutor_service as svc
from matbot import topic_resolver as tr
from matbot.answer_checker import derive_expected
from matbot.minimal import mathfmt, skills, solution_facts
from matbot.minimal.intent import TurnIntent, classify

STREAM_URL = "/api/ai-tutor/chat/stream"
#: The exact production topic and its real curriculum metadata.
EQ_TOPIC = "6-07-064"
EQ_OBLAST = "Jednačine, nejednačine i izrazi u Q+"
EQ_TEMA = "Jednačine s razlomcima oblika x ± a = b i a ± x = b"

TASK = "Riješi jednačinu: 2x - 3 = 9."
EXPECTED = "6"


def prod_payload(**overrides):
    payload = {
        "session_id": "eq-1", "grade": 6, "mode": "practice",
        "session_mode": "practice", "entry_source": "manual_topic_choice",
        "selected_topic": EQ_TOPIC, "selected_oblast": EQ_OBLAST,
        "student_message": "daj mi zadatak", "conversation_history": [],
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "eq.sqlite3"))
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


def seeded(client, question=TASK, expected=EXPECTED,
           skill="fraction_equation_additive"):
    """Start a session and pin one specific active task, as production did."""
    state = sse(client, prod_payload())["next_state"]
    state["minimal_state"]["active_task"] = {
        "task_id": "mt_8b0f796dc92f", "skill_id": skill,
        "question": question, "expected_display": expected,
        "npp_id": EQ_TOPIC, "tema_title": EQ_TEMA,
        "attempts": 0, "wrong_attempts": 0, "hints_given": 0,
        "solved": False, "solution_revealed": False,
    }
    state["task_id"] = "mt_8b0f796dc92f"
    return state, question


def turn(client, state, question, message):
    return sse(client, prod_payload(
        student_message=message, interaction_phase="answering_practice_task",
        last_tutor_task=question, previous_next_state=state))


# =========================================================================== #
# 1. Topic fidelity: the exact production topic                               #
# =========================================================================== #
def test_curriculum_metadata_is_the_real_lesson():
    """Guards the assumption the whole mapping decision rests on."""
    canonical = tr.resolve_topic(6, EQ_TOPIC)
    assert canonical is not None
    assert canonical.tema == EQ_TEMA
    assert canonical.oblast == EQ_OBLAST


def test_topic_binds_to_the_specific_fraction_equation_skill():
    topic = skills.resolve_topic(6, EQ_TOPIC)
    assert topic.skill_id == "fraction_equation_additive"
    assert topic.npp_id == EQ_TOPIC
    assert topic.title == EQ_TEMA


def test_generic_linear_equation_can_no_longer_claim_this_tema():
    """The keyword "jednacin" still matches the title — the explicit
    ``tema_ids`` binding is what must win."""
    generic = next(s for s in skills.SKILLS if s.skill_id == "linear_equation")
    assert any(k in skills._fold(EQ_TEMA) for k in generic.keywords)
    assert EQ_TOPIC not in generic.tema_ids
    specific = next(s for s in skills.SKILLS
                    if s.skill_id == "fraction_equation_additive")
    assert EQ_TOPIC in specific.tema_ids


def test_generated_task_matches_the_selected_lesson(client):
    """Over the wire: a fraction equation, never a generic ax + b = c."""
    body = sse(client, prod_payload())
    task = body["next_state"]["minimal_state"]["active_task"]
    assert task["skill_id"] == "fraction_equation_additive"
    question = task["question"]
    assert "x" in question
    # a fraction is present on at least one side
    assert "/" in question, question
    # and the coefficient form the old generator produced is absent
    assert not any(f"{n}x" in question.replace(" ", "") for n in range(2, 10)), \
        question


@pytest.mark.parametrize("level", [1, 2, 3])
def test_every_generated_equation_is_independently_checkable(level):
    """The expected value is computed deterministically and agrees with the
    shared answer_checker — no template, no model."""
    for seed in range(12):
        question, expected = skills.generate_question(
            "fraction_equation_additive", f"{level}-{seed}", difficulty=level)
        derived = derive_expected(question)
        assert derived is not None, question
        assert str(derived.value) == expected, (question, expected)
        assert Fraction(expected) > 0, question


# =========================================================================== #
# 2. Deterministic equation solution facts                                    #
# =========================================================================== #
def test_facts_for_the_production_equation():
    facts = solution_facts.resolve_equation_facts(TASK)
    assert facts is not None
    assert facts.coefficient == 2
    assert facts.constant == -3            # sign preserved
    assert facts.right == 9
    assert facts.removes_by_adding is True
    assert facts.move_amount == 3
    assert facts.intermediate_equation == "2x = 12"
    assert facts.needs_division is True
    assert facts.solution == 6
    assert facts.solution_equation == "x = 6"


@pytest.mark.parametrize("question,operation,intermediate,solution", [
    ("Riješi jednačinu: 2x - 3 = 9.", "add", "2x = 12", "6"),
    ("Riješi jednačinu: 2x + 5 = 11.", "subtract", "2x = 6", "3"),
    ("Riješi jednačinu: 2x - 5 = 11.", "add", "2x = 16", "8"),
    ("Riješi jednačinu: 4x - 1 = 15.", "add", "4x = 16", "4"),
    ("Riješi jednačinu: x + 1/3 = 5/6.", "subtract", "x = 1/2", "1/2"),
    ("Riješi jednačinu: 2/5 + x = 3/4.", "subtract", "x = 7/20", "7/20"),
    ("Riješi jednačinu: x - 1/4 = 1/2.", "add", "x = 3/4", "3/4"),
])
def test_facts_are_correct_for_every_supported_form(
        question, operation, intermediate, solution):
    facts = solution_facts.resolve_equation_facts(question)
    assert facts is not None, question
    assert facts.to_dict()["operation"] == operation
    assert facts.intermediate_equation == intermediate
    assert facts.to_dict()["solution"] == solution
    # cross-check against the independent checker
    assert str(derive_expected(question).value) == solution


def test_unparseable_equation_yields_no_facts():
    assert solution_facts.resolve_equation_facts("Izračunaj: 1/3 + 4/5.") is None
    assert solution_facts.resolve_equation_facts("") is None


# =========================================================================== #
# 3. Progressive hint ladder                                                  #
# =========================================================================== #
def test_hint_ladder_over_the_wire(client):
    state, question = seeded(client)
    hints = []
    for _ in range(3):
        body = turn(client, state, question, "ne znam")
        state = body["next_state"]
        hints.append(body["answer"])

    assert "Dodaj 3" in hints[0], hints[0]
    assert r"2x=12" in hints[1].replace(" ", ""), hints[1]
    assert "Podijeli obje strane sa 2" in hints[2], hints[2]

    joined = " ".join(hints)
    # the production defect, asserted directly
    assert "oduzeti" not in joined.lower(), joined
    assert "Oduzmi" not in joined, joined
    assert "ostane sam" not in joined, joined
    assert len({h for h in hints}) == 3, hints


def test_fourth_hint_is_honest_and_offers_the_solution(client):
    state, question = seeded(client)
    for _ in range(3):
        state = turn(client, state, question, "ne znam")["next_state"]
    body = turn(client, state, question, "ne znam")
    answer = body["answer"]
    assert "Isti korak" in answer, answer
    assert "postupak" in answer, answer


def test_hints_preserve_task_and_counters(client, sheets):
    state, question = seeded(client)
    for expected_count in (1, 2, 3, 4, 5):
        body = turn(client, state, question, "ne znam")
        state = body["next_state"]
        assert state["task_id"] == "mt_8b0f796dc92f"
        assert state["hint_count"] == expected_count
        assert state["correct_streak"] == 0
        task = state["minimal_state"]["active_task"]
        assert task["attempts"] == 0
        assert task["wrong_attempts"] == 0
        assert task["solved"] is False
    assert state["minimal_state"].get("solved_count", 0) == 0


def test_hint_equations_use_latex(client):
    state, question = seeded(client)
    state = turn(client, state, question, "ne znam")["next_state"]
    second = turn(client, state, question, "ne znam")["answer"]
    assert mathfmt.INLINE_OPEN in second, second
    assert mathfmt.INLINE_CLOSE in second, second


def test_hint_never_reveals_the_solution_early(client):
    """For a one-step fraction equation the intermediate IS the answer, so
    rung 2 must name the CALCULATION rather than its result."""
    state, question = seeded(client, "Riješi jednačinu: x + 1/3 = 5/6.", "1/2")
    first = turn(client, state, question, "ne znam")
    state = first["next_state"]
    second = turn(client, state, question, "ne znam")
    for body in (first, second):
        assert r"\frac{1}{2}" not in body["answer"], body["answer"]
        assert "x = 1/2" not in body["answer"], body["answer"]


# =========================================================================== #
# 4. SOLUTION_REQUEST                                                         #
# =========================================================================== #
@pytest.mark.parametrize("message", [
    "hajde ga ti uradi",
    "ne znam hajde ga ti uradi",
    "uradi ti",
    "riješi ti",
    "uradi i objasni postupak",
    "pokaži cijelo rješenje",
    "daj rješenje",
])
def test_solution_phrases_classify_as_solution_request(message):
    assert classify(message, has_active_task=True).intent == \
        TurnIntent.SOLUTION_REQUEST


def test_solution_request_wins_over_help():
    """Both phrases present: "ne znam" (HELP) and "hajde ga ti uradi"."""
    assert classify("ne znam", has_active_task=True).intent == TurnIntent.HELP
    assert classify("ne znam hajde ga ti uradi",
                    has_active_task=True).intent == TurnIntent.SOLUTION_REQUEST


def test_production_solution_request_over_the_wire(client):
    state, question = seeded(client)
    body = turn(client, state, question, "ne znam hajde ga ti uradi")

    telemetry = body["minimal_telemetry"]
    assert telemetry["turn_intent"] == "SOLUTION_REQUEST", telemetry
    assert telemetry["intent_source"] == "deterministic", telemetry
    answer = body["answer"]
    assert r"\begin{aligned}" in answer, answer
    for step in ("2x-3&=9", "2x&=12", "x&=6"):
        assert step in answer.replace(" ", ""), answer
    # display math must survive as ONE physical line
    assert "\n" not in answer[answer.index(r"\["):answer.index(r"\]")]


def test_solution_request_state_and_audit(client):
    state, question = seeded(client)
    body = turn(client, state, question, "ne znam hajde ga ti uradi")
    state = body["next_state"]

    assert state["task_status"] == "revealed"
    assert state["solution_revealed"] is True
    assert state["task_id"] == "mt_8b0f796dc92f"
    assert state["correct_streak"] == 0
    assert state["minimal_state"].get("solved_count", 0) == 0
    # a revealed task is finished, so active_task is cleared and the task's
    # identity survives in completed_task_id
    assert state["completed_task_id"] == "mt_8b0f796dc92f"
    assert state["minimal_state"]["active_task"] is None
    # a solution request is not a wrong attempt, and earns no credit
    assert body["wrong_attempt_count"] == 0
    assert body["total_attempt_count"] == 0
    assert state["task_status"] != "completed"


def test_solution_request_preserves_earlier_wrong_attempts(client):
    state, question = seeded(client)
    state = turn(client, state, question, "5")["next_state"]
    assert state["minimal_state"]["active_task"]["wrong_attempts"] == 1

    body = turn(client, state, question, "ne znam hajde ga ti uradi")
    assert body["wrong_attempt_count"] == 1   # preserved, not incremented
    assert body["solution_revealed"] is True
    assert body["task_status"] == "revealed"
    assert body["next_state"]["minimal_state"].get("solved_count", 0) == 0


def test_fraction_equation_solution_is_deterministic(client):
    state, question = seeded(client, "Riješi jednačinu: x + 1/3 = 5/6.", "1/2")
    answer = turn(client, state, question, "daj rješenje")["answer"]
    assert r"\frac{1}{3}" in answer, answer
    assert r"\frac{1}{2}" in answer, answer


# =========================================================================== #
# 5. Sign handling, answer forms, and the correction path                     #
# =========================================================================== #
def test_positive_constant_first_operation_is_subtract(client):
    state, question = seeded(client, "Riješi jednačinu: 2x + 5 = 11.", "3",
                             skill="linear_equation")
    answer = turn(client, state, question, "ne znam")["answer"]
    assert "Oduzmi 5" in answer, answer
    assert "Dodaj" not in answer, answer


def test_negative_constant_first_operation_is_add(client):
    state, question = seeded(client, "Riješi jednačinu: 2x - 5 = 11.", "8",
                             skill="linear_equation")
    answer = turn(client, state, question, "ne znam")["answer"]
    assert "Dodaj 5" in answer, answer
    assert "Oduzmi" not in answer, answer


@pytest.mark.parametrize("message", ["6", "x = 6", "x=6"])
def test_bare_number_and_x_form_both_accepted(client, message):
    state, question = seeded(client)
    body = turn(client, state, question, message)
    assert body["answer_verdict"] == "correct", body
    assert body["task_status"] == "completed"
    assert body["next_state"]["minimal_state"]["solved_count"] == 1


@pytest.mark.parametrize("message", ["1/2", "x = 1/2"])
def test_fraction_answer_forms_both_accepted(client, message):
    state, question = seeded(client, "Riješi jednačinu: x + 1/3 = 5/6.", "1/2")
    body = turn(client, state, question, message)
    assert body["answer_verdict"] == "correct", body


def test_wrong_then_correct_preserves_task_and_counters(client):
    state, question = seeded(client)
    wrong = turn(client, state, question, "5")
    assert wrong["answer_verdict"] == "incorrect"
    state = wrong["next_state"]
    assert state["task_id"] == "mt_8b0f796dc92f"
    assert state["minimal_state"]["active_task"]["wrong_attempts"] == 1
    assert state["correct_streak"] == 0

    right = turn(client, state, question, "x = 6")
    assert right["answer_verdict"] == "correct"
    final = right["next_state"]
    assert final["task_id"] == "mt_8b0f796dc92f"
    assert final["completed_task_id"] == "mt_8b0f796dc92f"
    assert final["task_status"] == "completed"
    assert right["wrong_attempt_count"] == 1  # the real wrong attempt survives
    assert right["total_attempt_count"] == 2
    assert final["correct_streak"] == 1
    assert final["minimal_state"]["solved_count"] == 1


def test_audit_values_for_equations_stay_plain_text(client, sheets):
    from matbot import sheets_log
    state, question = seeded(client)
    turn(client, state, question, "x = 6")
    row = sheets_log._build_transcript_row(*sheets[-1])
    headers = sheets_log.SHEET_HEADERS
    for name in ("student_message", "student_answer", "expected_answer",
                 "normalized_student", "last_tutor_task"):
        value = str(row[headers.index(name)])
        assert "\\frac" not in value, (name, value)
        assert "\\(" not in value, (name, value)
