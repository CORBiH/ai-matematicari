"""Offline regression coverage for the generic structured Tutor/Reviewer flow."""
import copy

import pytest

from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor.lesson_context import build
from matbot.tutor.schema import (DifficultyEvidence, ReviewerChecks, ReviewerFinal,
                                 TaskPayload, TaskSignature, TutorDraft, TutorOption,
                                 difficulty_evidence_errors)
from matbot.tutor.pipeline import SAFE_ERROR_MESSAGE
from tests.conftest import FakeLLM, make_difficulty_diagnostics


FAMILIES = [
    (6, "6-04-009"),  # fractions
    (6, "6-03-001"),  # divisibility
    (7, "7-02-008"),  # negative integers
    (8, "8-05-001"),  # coordinate system / geometry
    (9, "9-05-004"),  # equations / systems
]


def turn(grade, topic, message="Daj mi zadatak.", session_id="structured"):
    return {
        "session_id": session_id, "grade": grade, "selected_topic": topic,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "", "client_turn_id": "",
    }


def task_for(context, level=1, signature="one", text="Izračunaj $2+2$.",
             options=("$4$", "$3$", "$5$", "$6$"), correct=0):
    return TaskPayload(
        selected_lesson_id=context.topic_id, selected_lesson_title=context.title,
        target_difficulty_level=level, text=text, task_type="multiple_choice",
        options=[TutorOption(id="abcd"[i], text=value) for i, value in enumerate(options)],
        correct_option_index=correct, correct_option_id="abcd"[correct],
        expected_answer=options[correct], solution=options[correct],
        difficulty=("easy", "standard", "hard")[level - 1],
        difficulty_evidence=DifficultyEvidence(
            reasoning_steps=level, condition_count=level, operation_count=level,
            representation_change_count=0, requires_explanation=level == 3,
            requires_comparison=False, requires_construction=False,
            requires_proof_or_justification=level == 3, combines_concepts=level == 3,
        ),
        task_signature=TaskSignature(
            task_family="generic", operation_or_relation="calculation",
            normalized_parameters={"case": signature}, required_conditions=["valid"],
            relevant_objects=["numbers"], answer_type="multiple_choice",
        ),
    )


def checks(**changes):
    base = dict(math_correct=True, marked_option_correct=True, inside_lesson=True,
                intent_handled=True, difficulty_direction_correct=True,
                response_addresses_student=True, task_solvable_and_unambiguous=True,
                mathjax_valid=True, language_age_appropriate=True,
                independently_solved=True, independent_answer="$4$",
                task_package_consistent=True, difficulty_evidence_valid=True,
                task_signature_consistent=True)
    base.update(changes)
    return ReviewerChecks(**base)


def queue_generation(fake, task, intent="generate_task"):
    draft = TutorDraft(intent=intent, reply="Evo zadatka.", lesson_focus="tačna lekcija",
                       new_task=task,
                       difficulty_diagnostics=(None if intent not in {"harder_task", "easier_task"}
                                               else make_difficulty_diagnostics(
                                                   "higher" if intent == "harder_task" else "lower")))
    fake.queue(draft)
    fake.queue(ReviewerFinal(decision="approve", checks=checks(), final=draft))


@pytest.mark.parametrize("grade,topic", FAMILIES)
def test_representative_lessons_publish_exact_level_one_with_two_calls(monkeypatch, grade, topic):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    context, store, fake = build(grade, topic), SessionStore(), FakeLLM()
    queue_generation(fake, task_for(context, signature=topic))
    response = run_practice_turn(store, fake, turn(grade, topic, session_id=topic))
    session = store.peek(topic)
    assert response["status"] == "ready"
    assert fake.call_count == 2
    assert session["lesson_id"] == topic and session["difficulty_level"] == 1
    assert session["current_task_signature"]["structured_signature_hash"]


def test_free_form_harder_easier_and_same_level_signature_policy(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    grade, topic, context = 6, "6-03-001", build(6, "6-03-001")
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, task_for(context, 1, "first"))
    run_practice_turn(store, fake, turn(grade, topic))
    queue_generation(fake, task_for(context, 2, "harder", text="Izračunaj $3+3$.",
                                    options=("$6$", "$5$", "$7$", "$8$")), "harder_task")
    run_practice_turn(store, fake, turn(grade, topic, "Može li nešto zahtjevnije?"))
    assert store.peek("structured")["difficulty_level"] == 2
    queue_generation(fake, task_for(context, 1, "easier", text="Izračunaj $1+1$.",
                                    options=("$2$", "$1$", "$3$", "$4$")), "easier_task")
    run_practice_turn(store, fake, turn(grade, topic, "Previše mi je teško, pojednostavi."))
    assert store.peek("structured")["difficulty_level"] == 1

    before = copy.deepcopy(store.peek("structured"))
    queue_generation(fake, task_for(context, 1, "easier", text="Izračunaj $4+4$.",
                                    options=("$8$", "$7$", "$9$", "$6$")))
    response = run_practice_turn(store, fake, turn(grade, topic, "Daj još jedan sličan."))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("structured") == before
    assert fake.call_count == 8  # four requests, never a retry or third call


@pytest.mark.parametrize("kind", ["lesson", "level", "stale_expected", "bad_evidence", "duplicate"])
def test_package_inconsistencies_fail_closed_without_state_mutation(monkeypatch, kind):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(6, "6-03-001"), SessionStore(), FakeLLM()
    queue_generation(fake, task_for(context, signature="first"))
    run_practice_turn(store, fake, turn(6, context.topic_id))
    before = copy.deepcopy(store.peek("structured"))
    task = task_for(context, signature="second", text="Izračunaj $3+3$.",
                    options=("$6$", "$5$", "$7$", "$8$"))
    if kind == "lesson":
        task = task.model_copy(update={"selected_lesson_id": "6-03-002"})
    elif kind == "level":
        task = task.model_copy(update={"target_difficulty_level": 2})
    elif kind == "stale_expected":
        task = task.model_copy(update={"expected_answer": "$5$"})
    elif kind == "bad_evidence":
        task = task.model_copy(update={"difficulty_evidence": task.difficulty_evidence.model_copy(
            update={"reasoning_steps": -1})})
    else:
        task = task.model_copy(update={"task_signature": before["current_task_signature"] and
            task_for(context, signature="first").task_signature})
    queue_generation(fake, task)
    response = run_practice_turn(store, fake, turn(6, context.topic_id))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("structured") == before
    assert fake.call_count == 4


def test_proven_numeric_contradiction_still_rejects_but_parser_absence_is_not_a_gate(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    context, store, fake = build(6, "6-03-001"), SessionStore(), FakeLLM()
    # No divisibility prose parser is needed for a complete reviewer-approved package.
    queue_generation(fake, task_for(context, signature="non-parser"))
    assert run_practice_turn(store, fake, turn(6, context.topic_id))["status"] == "ready"
    before = copy.deepcopy(store.peek("structured"))
    contradiction = task_for(context, signature="contradiction", text="Pošto je $2+2=5$, izaberi odgovor.")
    queue_generation(fake, contradiction)
    assert run_practice_turn(store, fake, turn(6, context.topic_id))["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("structured") == before


def test_production_difficulty_evidence_validator_rejects_false_level_two_claim():
    evidence = DifficultyEvidence(
        reasoning_steps=1, condition_count=1, operation_count=1,
        representation_change_count=0, requires_explanation=False,
        requires_comparison=False, requires_construction=False,
        requires_proof_or_justification=False, combines_concepts=False,
    )
    assert "level_2_lacks_connected_reasoning_or_explanation" in difficulty_evidence_errors(evidence, 2)


def test_difficulty_evidence_counts_only_meaningful_connected_operations():
    level_one_too_complex = DifficultyEvidence(
        reasoning_steps=1, condition_count=1, operation_count=2,
        representation_change_count=0, requires_explanation=False,
        requires_comparison=False, requires_construction=False,
        requires_proof_or_justification=False, combines_concepts=False,
    )
    assert "level_1_is_not_direct_introductory_application" in difficulty_evidence_errors(
        level_one_too_complex, 1)

    level_two_connected_operations = level_one_too_complex
    assert difficulty_evidence_errors(level_two_connected_operations, 2) == ()

    level_two_direct = level_one_too_complex.model_copy(update={"operation_count": 1})
    assert "level_2_lacks_connected_reasoning_or_explanation" in difficulty_evidence_errors(
        level_two_direct, 2)


@pytest.mark.parametrize("difficulty_flag", [
    None, "", "true", "1", "yes", "enable", "typo", "ENABLED", " enabled ",
])
def test_universal_generation_without_exact_difficulty_flag_never_progresses(monkeypatch, difficulty_flag):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    if difficulty_flag is None:
        monkeypatch.delenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", raising=False)
    else:
        monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", difficulty_flag)

    context, store, fake = build(6, "6-04-009"), SessionStore(), FakeLLM()
    queue_generation(fake, task_for(context, level=3, signature="initial"), "harder_task")
    first = run_practice_turn(store, fake, turn(6, context.topic_id, "Daj tezi zadatak."))
    assert first["status"] == "ready"
    assert first["answer"].startswith("Evo zadatka.")
    assert store.peek("structured")["difficulty_level"] == 1
    assert store.peek("structured")["difficulty"] == "standard"

    # A saved successful streak would normally make the next task harder.
    # With the controller off it must not affect publication or session level.
    staged = store.peek("structured")
    staged["correct_streak"] = 2
    store.save(staged)
    queue_generation(fake, task_for(context, level=3, signature="streak",
                                    text="Izracunaj $3+3$.",
                                    options=("$6$", "$5$", "$7$", "$8$")), "next_task")
    second = run_practice_turn(store, fake, turn(6, context.topic_id, "Daj sljedeci zadatak."))
    assert second["status"] == "ready"
    assert store.peek("structured")["difficulty_level"] == 1
    assert store.peek("structured")["difficulty"] == "standard"


def _word_task(context, expected="Da", options=("Da", "Ne", "Mozda", "Nema odgovora")):
    return task_for(
        context, signature="word", text="Odaberi odgovor Da.", options=options,
    ).model_copy(update={
        "expected_answer": expected,
        "solution": "Rjesenje: oznacena opcija je Da.",
    })


def test_mcq_expected_answer_is_exact_option_text_while_solution_can_explain(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    context, store, fake = build(6, "6-04-009"), SessionStore(), FakeLLM()
    queue_generation(fake, _word_task(context))
    assert run_practice_turn(store, fake, turn(6, context.topic_id))["status"] == "ready"


@pytest.mark.parametrize("expected", ["Da (because...)", "da"])
def test_mcq_expected_answer_rejects_explanation_or_nonidentical_formatting(monkeypatch, expected):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    context, store, fake = build(6, "6-04-009"), SessionStore(), FakeLLM()
    queue_generation(fake, _word_task(context, expected=expected))
    assert run_practice_turn(store, fake, turn(6, context.topic_id))["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("structured") is None


def test_mcq_equivalent_but_nonidentical_math_formatting_does_not_pass(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    context, store, fake = build(6, "6-04-009"), SessionStore(), FakeLLM()
    task = task_for(context).model_copy(update={"expected_answer": "4"})
    queue_generation(fake, task)
    assert run_practice_turn(store, fake, turn(6, context.topic_id))["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("structured") is None


def test_reviewer_option_change_without_expected_answer_update_fails(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    context, store, fake = build(6, "6-04-009"), SessionStore(), FakeLLM()
    draft_task = _word_task(context)
    corrected = _word_task(
        context, options=("Ne", "Da", "Mozda", "Nema odgovora"),
    )
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="tacna lekcija", new_task=draft_task)
    reviewer_final = draft.model_copy(update={"new_task": corrected})
    fake.queue(draft)
    fake.queue(ReviewerFinal(decision="correct", checks=checks(), final=reviewer_final))

    assert run_practice_turn(store, fake, turn(6, context.topic_id))["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("structured") is None
