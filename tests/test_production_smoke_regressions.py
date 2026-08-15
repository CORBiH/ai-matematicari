"""Regression coverage for the production difficulty/divisibility smoke trace."""
import copy
import re
from pathlib import Path

import pytest

from matbot import feedback, mathsafe
from matbot.lesson_fidelity import FidelityChecks, exact_lesson_skill_failure
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore
from tests.conftest import (queue_two_call, FakeLLM, make_fidelity_checks, make_fidelity_review,
                            make_options, make_output, make_task, queue_generation)


DIVISIBILITY = ("6-03-004", 6)
OTHER_LESSON = ("7-02-008", 7)


def _enable_levels(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def _turn(topic, grade, session_id, **changes):
    turn = {
        "session_id": session_id,
        "grade": grade,
        "selected_topic": topic,
        "selected_oblast": "",
        "student_message": "Daj mi zadatak.",
        "intent": "",
        "difficulty_request": "",
        "interaction_phase": "",
        "last_tutor_task": "",
        "interaction_type": "student_question",
        "selected_option_id": "",
        "client_turn_id": "",
    }
    turn.update(changes)
    return turn


def _divisibility_task(text, options, correct_index=0, answer_kind="short_text"):
    return make_task(
        text=text,
        options=make_options(*options),
        correct_option_index=correct_index,
        expected=options[correct_index],
        task_family="direct_computation",
        answer_kind=answer_kind,
    )


FRESH_LEVEL_1 = _divisibility_task(
    "Je li broj 47 djeljiv sa 5?",
    ("Ne", "Da", "Samo sa 2", "Ne može se odrediti"),
    answer_kind="short_text",
)
HARDER_LEVEL_2 = _divisibility_task(
    "Koji od ponuđenih brojeva je djeljiv i sa 2 i sa 3?",
    ("138", "139", "140", "141"),
    answer_kind="integer",
)


def _publish_fresh_and_answer_correctly(store, fake, monkeypatch, session_id):
    _enable_levels(monkeypatch)
    topic, grade = DIVISIBILITY
    queue_generation(fake, FRESH_LEVEL_1)
    fresh = run_practice_turn(store, fake, _turn(topic, grade, session_id))
    assert fresh["status"] == "ready"
    session = store.peek(session_id)
    correct_id = session["correct_option_id"]
    queue_two_call(fake, intent="clarification")
    correct = run_practice_turn(
        store, fake,
        _turn(topic, grade, session_id, interaction_type="choice_answer",
              selected_option_id=correct_id, student_message="[klik]", client_turn_id="correct-1"),
    )
    assert correct["answer_verdict"] == "correct"
    return fake.call_count


def test_reviewer_schema_requires_direction_check_when_it_is_absent():
    values = make_fidelity_checks().model_dump()
    values.pop("difficulty_direction_correct")
    with pytest.raises(Exception):
        FidelityChecks.model_validate(values)


def test_harder_two_rule_divisibility_task_passes_exact_lesson_gate(monkeypatch):
    _enable_levels(monkeypatch)
    store, fake = SessionStore(), FakeLLM()
    session_id = "smoke-two-rules"
    queue_generation(fake, FRESH_LEVEL_1)
    assert run_practice_turn(store, fake, _turn(*DIVISIBILITY, session_id))["status"] == "ready"
    queue_generation(fake, HARDER_LEVEL_2)
    response = run_practice_turn(store, fake, _turn(
        *DIVISIBILITY, session_id, student_message="Daj mi teži zadatak.",
        difficulty_request="harder",
    ))
    assert response["status"] == "ready"
    assert store.peek(session_id)["difficulty_level"] == 2


@pytest.mark.parametrize("text", [
    "Je li 340 djeljiv sa 10?",
    "Je li 12350 djeljiv i sa 10 i sa 25?",
    "Koji od ponuđenih brojeva je djeljiv sa 6?",
])
def test_shared_divisibility_requirement_accepts_direct_rule_application(text):
    assert exact_lesson_skill_failure(
        "Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25", text,
    ) is None


@pytest.mark.parametrize("text", [
    "Koliki je ostatak pri dijeljenju 754 sa 15?",
    "Podijeli 754 sa 15.",
    "Odredi količnik i ostatak.",
])
def test_shared_divisibility_requirement_rejects_adjacent_division_tasks(text):
    assert exact_lesson_skill_failure(
        "Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25", text,
    ) == "divisibility_rules_not_required_by_visible_task"


def test_semantic_requirement_uses_no_exact_lesson_id_branching():
    root = Path(__file__).resolve().parents[1]
    topic_id = re.compile(r"\b\d-\d{2}-\d{3}\b")
    for name in ("matbot/lesson_fidelity.py", "matbot/prompts.py", "matbot/practice.py"):
        source = (root / name).read_text(encoding="utf-8")
        assert not [line for line in source.splitlines() if topic_id.search(line)]


REMAINDER_TASK = "Koliki je ostatak pri dijeljenju broja 754 sa 15?"


def test_remainder_hint_about_only_a_proper_factor_is_replaced_with_valid_progress():
    bad_hint = "Prvo nađi ostatak pri dijeljenju broja 754 sa 5."
    replacement = feedback.ensure_hint_makes_progress(REMAINDER_TASK, bad_hint)
    assert replacement != bad_hint
    assert "$15$" in replacement
    assert "$754$" in replacement
    assert "$4$" not in replacement
