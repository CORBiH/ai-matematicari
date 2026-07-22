# -*- coding: utf-8 -*-
"""fraction_equation_additive production session (topic 6-07-064).

Active task: 1/2 + x = 3/5.

1. DUPLICATE GUIDANCE. Wrong answer "1/7" produced "Nije još tačno. Oduzmi 1/2
   s obje strane da ukloniš +1/2.", and the explicit HELP turn that followed
   repeated that same sentence. Cause: ``renderer.feedback`` called
   ``_hint_text(skill, task.hints_given, …)`` with hints_given still 0, so the
   nudge WAS ladder rung 1, and the next HELP turn asked for rung 1 again.
   Fixed by policy B — see ``_EQUATION_NUDGE``.

2. WRONG CONCEPT FAMILY. "DZašto moramo uraditi istu operaciju na obje strane
   jednačine?" was answered with fraction-EXPANSION prose. Two branches:
   ``concept_answer`` gated verified facts on ``_FRACTION_SKILLS``, which
   excluded equation skills entirely, and the ``_CONCEPT_NO_NUMBERS`` fallback
   it then fell through to is expansion-specific regardless of skill.

3. The "a - x = b" shape was solved with the "x - a = b" transformation.

Driven through the SSE route the browser actually uses.
"""
import json

import pytest

from matbot import ai_tutor_service as svc
from matbot import sheets_log
from matbot import topic_resolver as tr
from matbot.answer_checker import derive_expected
from matbot.minimal import concept_facts, mathfmt, solution_facts
from matbot.minimal.intent import TurnIntent, classify

STREAM_URL = "/api/ai-tutor/chat/stream"
EQ_TOPIC = "6-07-064"
EQ_OBLAST = "Jednačine, nejednačine i izrazi u Q+"
EQ_TEMA = "Jednačine s razlomcima oblika x ± a = b i a ± x = b"

TASK = "Riješi jednačinu: 1/2 + x = 3/5."
EXPECTED = "1/10"
CONCEPT_Q = "Zašto moramo uraditi istu operaciju na obje strane jednačine?"
TYPO_CONCEPT_Q = "DZašto moramo uraditi istu operaciju na obje strane jednačine?"


def prod_payload(**overrides):
    payload = {
        "session_id": "eqh-1", "grade": 6, "mode": "practice",
        "session_mode": "practice", "entry_source": "manual_topic_choice",
        "selected_topic": EQ_TOPIC, "selected_oblast": EQ_OBLAST,
        "student_message": "daj mi zadatak", "conversation_history": [],
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "eqh.sqlite3"))
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


@pytest.fixture()
def no_model(monkeypatch):
    """Fails the test if any OpenAI call is made on a deterministic path."""
    calls = []

    def _boom(*args, **kwargs):
        calls.append(args)
        raise AssertionError("OpenAI was called on a deterministic path")

    monkeypatch.setattr(svc, "_openai_chat", _boom, raising=False)
    return calls


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
    state = sse(client, prod_payload())["next_state"]
    state["minimal_state"]["active_task"] = {
        "task_id": "mt_prod", "skill_id": skill, "question": question,
        "expected_display": expected, "npp_id": EQ_TOPIC,
        "tema_title": EQ_TEMA, "attempts": 0, "wrong_attempts": 0,
        "hints_given": 0, "solved": False, "solution_revealed": False,
    }
    state["task_id"] = "mt_prod"
    return state, question


def turn(client, state, question, message):
    return sse(client, prod_payload(
        student_message=message, interaction_phase="answering_practice_task",
        last_tutor_task=question, previous_next_state=state))


def progress(body):
    """The fields that a HELP or concept turn must never move."""
    state = body["next_state"]
    return {
        "task_id": state["task_id"],
        "streak": state["correct_streak"],
        "solved": state["minimal_state"].get("solved_count", 0),
        "attempts": body["total_attempt_count"],
        "wrong": body["wrong_attempt_count"],
    }


# =========================================================================== #
# 1. Incorrect feedback must not duplicate the first HELP rung                #
# =========================================================================== #
def test_wrong_answer_then_help_gives_new_guidance(client):
    state, question = seeded(client)
    wrong = turn(client, state, question, "1/7")
    assert wrong["answer_verdict"] == "incorrect"
    nudge = wrong["answer"]

    helped = turn(client, wrong["next_state"], question, "ne znam sta mislis")
    hint = helped["answer"]

    # the production defect, asserted directly
    assert hint != nudge, hint
    assert "Oduzmi" not in nudge, nudge
    assert "Oduzmi" in hint, hint
    assert r"\frac{1}{2}" in hint, hint
    assert helped["next_state"]["task_id"] == "mt_prod"


def test_incorrect_feedback_is_deliberately_non_specific(client):
    state, question = seeded(client)
    answer = turn(client, state, question, "1/7")["answer"]
    assert "izdvojiti x" in answer, answer
    # no ladder rung leaked into the feedback
    for leak in ("Oduzmi", "Dodaj", "Izjednači", "Tada dobijaš"):
        assert leak not in answer, (leak, answer)


def test_help_after_wrong_answer_preserves_progress(client):
    state, question = seeded(client)
    wrong = turn(client, state, question, "1/7")
    before = progress(wrong)
    helped = turn(client, wrong["next_state"], question, "ne znam sta mislis")
    after = progress(helped)
    assert after["task_id"] == before["task_id"]
    assert after["streak"] == before["streak"]
    assert after["solved"] == before["solved"]
    assert after["attempts"] == before["attempts"]   # HELP is not an attempt
    assert after["wrong"] == before["wrong"]         # the real one is kept
    assert after["wrong"] == 1


def test_ne_znam_sta_mislis_is_deterministic_help():
    decided = classify("ne znam sta mislis", has_active_task=True)
    assert decided.intent == TurnIntent.HELP
    assert decided.matched != "model_classifier"


# =========================================================================== #
# 2. The deterministic ladder for the production task                         #
# =========================================================================== #
def test_help_ladder_progresses_over_the_wire(client, no_model):
    state, question = seeded(client)
    hints = []
    for _ in range(5):
        body = turn(client, state, question, "ne znam")
        state = body["next_state"]
        hints.append(body["answer"])

    assert "Oduzmi" in hints[0] and r"\frac{1}{2}" in hints[0], hints[0]
    assert r"x=\frac{3}{5}-\frac{1}{2}" in hints[1].replace(" ", ""), hints[1]
    assert "Izjednači nazivnike" in hints[2], hints[2]
    assert r"\frac{3}{5}=\frac{6}{10}" in hints[2], hints[2]
    assert r"\frac{1}{2}=\frac{5}{10}" in hints[2], hints[2]
    assert r"\frac{6}{10}-\frac{5}{10}" in hints[3].replace(" ", ""), hints[3]
    # rung 5 repeats honestly instead of pretending to be new help
    assert "Isti korak" in hints[4], hints[4]
    assert "postupak" in hints[4], hints[4]
    assert len(set(hints[:4])) == 4, hints


def test_no_hint_reveals_the_solution(client):
    state, question = seeded(client)
    for _ in range(6):
        body = turn(client, state, question, "ne znam")
        state = body["next_state"]
        answer = body["answer"]
        assert r"\frac{1}{10}" not in answer, answer
        assert "1/10" not in answer, answer


def test_every_help_turn_preserves_progress(client):
    state, question = seeded(client)
    for expected_count in (1, 2, 3, 4, 5):
        body = turn(client, state, question, "ne znam")
        state = body["next_state"]
        assert state["task_id"] == "mt_prod"
        assert state["hint_count"] == expected_count
        assert progress(body) == {"task_id": "mt_prod", "streak": 0,
                                  "solved": 0, "attempts": 0, "wrong": 0}
        assert state["minimal_state"]["active_task"] is not None


# =========================================================================== #
# 3. Complete deterministic solution                                          #
# =========================================================================== #
@pytest.mark.parametrize("message", [
    "hajde ti uradi", "hajde ga ti uradi", "uradi ti", "riješi ti",
    "uradi i objasni postupak", "pokaži cijelo rješenje", "daj rješenje",
    "ne znam, hajde ti uradi",
])
def test_solution_phrases_beat_help(message):
    decided = classify(message, has_active_task=True)
    assert decided.intent == TurnIntent.SOLUTION_REQUEST, message
    assert decided.matched != "model_classifier"


def test_full_solution_over_the_wire(client, no_model):
    state, question = seeded(client)
    body = turn(client, state, question, "hajde ti uradi")

    assert body["minimal_telemetry"]["turn_intent"] == "SOLUTION_REQUEST"
    answer = body["answer"]
    compact = answer.replace(" ", "")
    for row in (r"\frac{1}{2}+x&=\frac{3}{5}",
                r"x&=\frac{3}{5}-\frac{1}{2}",
                r"x&=\frac{6}{10}-\frac{5}{10}",
                r"x&=\frac{1}{10}"):
        assert row in compact, answer
    block = answer[answer.index(r"\["):answer.index(r"\]")]
    assert "\n" not in block, "display math must stay on one physical line"


def test_solution_request_state_and_audit(client):
    state, question = seeded(client)
    state = turn(client, state, question, "1/7")["next_state"]   # a real miss
    body = turn(client, state, question, "hajde ti uradi")

    assert body["task_status"] == "revealed"
    assert body["solution_revealed"] is True
    assert body["next_state"]["task_id"] == "mt_prod"
    assert body["next_state"]["correct_streak"] == 0
    assert body["next_state"]["minimal_state"].get("solved_count", 0) == 0
    assert body["wrong_attempt_count"] == 1        # preserved, not incremented
    assert body["answer_verdict"] in (None, "", "none"), body["answer_verdict"]
    assert body.get("gpt_check_used") in (False, None)


# =========================================================================== #
# 4. All four lesson shapes                                                   #
# =========================================================================== #
SHAPES = [
    ("Riješi jednačinu: x + 1/3 = 5/6.", "1/2", "x+a=b", "Oduzmi"),
    ("Riješi jednačinu: 1/3 + x = 5/6.", "1/2", "a+x=b", "Oduzmi"),
    ("Riješi jednačinu: x - 1/3 = 1/2.", "5/6", "x-a=b", "Dodaj"),
    ("Riješi jednačinu: 5/6 - x = 1/3.", "1/2", "a-x=b", "Pazi"),
]


@pytest.mark.parametrize("question,expected,shape,opening", SHAPES)
def test_every_shape_is_solved_correctly(question, expected, shape, opening):
    facts = solution_facts.resolve_equation_facts(question)
    assert facts is not None, question
    assert facts.shape == shape
    assert facts.to_dict()["solution"] == expected
    # the shared deterministic checker must agree
    assert str(derive_expected(question).value) == expected
    assert solution_facts.equation_hint(facts, 1).startswith(opening), question


@pytest.mark.parametrize("question,expected,shape,opening", SHAPES)
def test_every_shape_over_the_wire(client, question, expected, shape, opening):
    state, _ = seeded(client, question, expected)
    hint = turn(client, state, question, "ne znam")["answer"]
    assert opening in hint, hint
    solution = turn(client, state, question, "daj rješenje")["answer"]
    assert mathfmt.to_latex(expected) in solution, solution


def test_risky_subtracted_unknown_shape(client):
    """a - x = b must NOT reuse the x - a = b flow."""
    question = "Riješi jednačinu: 5/6 - x = 1/3."
    facts = solution_facts.resolve_equation_facts(question)
    assert facts.subtracted_unknown is True
    assert facts.solution == __import__("fractions").Fraction(1, 2)
    # the isolation is a - b, not b + a
    assert facts.isolate_expression == "5/6 - 1/3"
    assert solution_facts.equation_solution_steps(facts) == [
        "5/6 - x = 1/3", "x = 5/6 - 1/3", "x = 5/6 - 2/6", "x = 1/2"]

    state, _ = seeded(client, question, "1/2")
    answer = turn(client, state, question, "daj rješenje")["answer"]
    compact = answer.replace(" ", "")
    assert r"x&=\frac{5}{6}-\frac{1}{3}" in compact, answer
    assert r"x&=\frac{1}{2}" in compact, answer
    # the wrong transformation would have produced 7/6
    assert "7/6" not in answer and r"\frac{7}{6}" not in answer, answer


def test_generated_tasks_agree_with_the_checker():
    from matbot.minimal import skills
    for level in (1, 2, 3):
        for seed in range(10):
            question, expected = skills.generate_question(
                "fraction_equation_additive", f"{level}-{seed}",
                difficulty=level)
            facts = solution_facts.resolve_equation_facts(question)
            assert facts is not None, question
            assert facts.to_dict()["solution"] == expected, question
            assert str(derive_expected(question).value) == expected, question


# =========================================================================== #
# 5-6. Concept questions resolve by RESOLVED SKILL                            #
# =========================================================================== #
def test_equation_concept_question_over_the_wire(client):
    state, question = seeded(client)
    body = turn(client, state, question, CONCEPT_Q)

    answer = body["answer"]
    assert "jednakost" in answer, answer
    assert "vaga" in answer or "ravnotež" in answer, answer
    # the production defect, asserted directly
    assert "Proširivanje" not in answer, answer
    assert "brojnik i nazivnik množiš" not in answer, answer

    telemetry = body["minimal_telemetry"]
    assert telemetry["turn_intent"] == "CONCEPT_QUESTION"
    assert telemetry["intent_source"] == "deterministic"
    assert telemetry["concept_fact_kind"] == "why_same_operation"
    assert telemetry["concept_facts_resolved"] is True


def test_typo_concept_question_takes_the_same_route(client, no_model):
    state, question = seeded(client)
    body = turn(client, state, question, TYPO_CONCEPT_Q)

    telemetry = body["minimal_telemetry"]
    assert telemetry["turn_intent"] == "CONCEPT_QUESTION"
    assert telemetry["intent_source"] == "deterministic"
    assert telemetry["concept_fact_kind"] == "why_same_operation"
    assert telemetry["concept_facts_resolved"] is True
    assert "jednakost" in body["answer"]


def test_generic_linear_equation_uses_the_same_concept_facts(client):
    state, question = seeded(client, "Riješi jednačinu: 2x - 3 = 9.", "6",
                             skill="linear_equation")
    body = turn(client, state, question, CONCEPT_Q)
    assert body["minimal_telemetry"]["concept_fact_kind"] == "why_same_operation"
    assert "jednakost" in body["answer"]
    assert "Proširivanje" not in body["answer"]


def test_fraction_expand_keeps_its_own_concept_facts(client):
    state, question = seeded(client, "Proširi 3/5 na nazivnik 15.", "9/15",
                             skill="fraction_expand")
    body = turn(client, state, question,
                "Zašto množimo brojnik i nazivnik istim brojem?")
    # This phrasing has no verified expansion facts, so no kind is emitted —
    # what matters is that it stays in the EXPANSION family and never borrows
    # the equation explanation.
    assert concept_facts.concept_family("fraction_expand") == "expansion"
    assert body["minimal_telemetry"]["concept_fact_kind"] != "why_same_operation"
    assert "vaga" not in body["answer"], body["answer"]
    assert "jednakost" not in body["answer"], body["answer"]


def test_equation_lesson_does_not_borrow_expansion_prose(client):
    """A numerator/denominator question under an equation skill gets a safe
    equation clarification, never the generic expansion explanation."""
    state, question = seeded(client)
    body = turn(client, state, question,
                "Šta je brojnik a šta nazivnik ovdje?")
    answer = body["answer"]
    assert "brojnik i nazivnik množiš istim cijelim brojem" not in answer, answer
    assert "jednačine s razlomcima" in answer.lower() \
        or "jednakost" in answer, answer


def test_concept_family_is_keyed_by_skill_not_by_fractions():
    assert concept_facts.concept_family("fraction_equation_additive") == "equation"
    assert concept_facts.concept_family("linear_equation") == "equation"
    assert concept_facts.concept_family("fraction_expand") == "expansion"
    assert concept_facts.concept_family("fraction_add_unlike") == "expansion"
    assert concept_facts.concept_family("divisibility") == ""
    # divisibility must not borrow a fraction explanation
    assert concept_facts.resolve_for_skill("divisibility", CONCEPT_Q) is None


# =========================================================================== #
# 7-8. Active-task safety for concept questions                               #
# =========================================================================== #
def test_concept_question_does_not_touch_progress(client):
    state, question = seeded(client)
    state = turn(client, state, question, "1/7")["next_state"]     # one miss
    hinted = turn(client, state, question, "ne znam")            # one hint
    state = hinted["next_state"]
    before = progress(hinted)
    before_hints = state["hint_count"]

    body = turn(client, state, question, CONCEPT_Q)
    after = body["next_state"]
    assert after["task_id"] == "mt_prod"
    assert after["hint_count"] == before_hints       # the hint ladder is frozen
    assert progress(body) == before                  # nothing moved at all
    assert body["wrong_attempt_count"] == 1          # the earlier miss survives
    assert after["correct_streak"] == 0
    assert after["minimal_state"].get("solved_count", 0) == 0
    assert after["minimal_state"]["active_task"] is not None
    assert body["answer_verdict"] in (None, "", "none")
    assert EXPECTED not in body["answer"], body["answer"]


def test_concept_answer_reminds_the_student_of_the_task(client):
    state, question = seeded(client)
    answer = turn(client, state, question, CONCEPT_Q)["answer"]
    assert "Zadatak je i dalje" in answer, answer
    assert mathfmt.format_question(question) in answer, answer


# =========================================================================== #
# 9. Answers still grade normally                                             #
# =========================================================================== #
@pytest.mark.parametrize("message", ["1/10", "x = 1/10", "x=1/10"])
def test_answer_forms_remain_accepted(client, message):
    state, question = seeded(client)
    body = turn(client, state, question, message)
    assert body["answer_verdict"] == "correct", body
    assert body["task_status"] == "completed"


def test_wrong_then_correct_preserves_counters(client):
    state, question = seeded(client)
    wrong = turn(client, state, question, "1/7")
    assert wrong["answer_verdict"] == "incorrect"
    right = turn(client, wrong["next_state"], question, "1/10")
    assert right["answer_verdict"] == "correct"
    assert right["next_state"]["task_id"] == "mt_prod"
    assert right["wrong_attempt_count"] == 1
    assert right["total_attempt_count"] == 2
    assert right["next_state"]["correct_streak"] == 1
    assert right["next_state"]["minimal_state"]["solved_count"] == 1
    assert right.get("gpt_check_used") in (False, None)


# =========================================================================== #
# 10. Audit                                                                   #
# =========================================================================== #
@pytest.mark.parametrize("message", ["ne znam", CONCEPT_Q, TYPO_CONCEPT_Q])
def test_help_and_concept_turns_are_not_graded_in_audit(client, sheets, message):
    state, question = seeded(client)
    turn(client, state, question, message)
    row = sheets_log._build_transcript_row(*sheets[-1])
    headers = sheets_log.SHEET_HEADERS

    def cell(name):
        return str(row[headers.index(name)])

    assert cell("student_message") == message      # verbatim
    assert cell("student_answer") == ""
    assert cell("answer_verdict") in ("", "none")
    for name in ("student_message", "student_answer", "expected_answer",
                 "normalized_student"):
        assert "\\frac" not in cell(name), name
        assert "\\(" not in cell(name), name


def test_sheet_still_has_62_columns():
    assert len(sheets_log.SHEET_HEADERS) == 62
