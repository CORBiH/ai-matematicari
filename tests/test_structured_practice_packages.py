"""Offline regression coverage for the generic structured Tutor/Reviewer flow."""
import copy

import pytest

from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor.lesson_context import build
from matbot.tutor.schema import (DifficultyEvidence, ReviewerChecks, ReviewerFinal,
                                 SignatureParameter, TaskPayload, TaskSignature, TutorDraft, TutorOption,
                                 difficulty_evidence_errors)
from matbot.tutor import pipeline as tutor_pipeline
from matbot.tutor import prompts as tutor_prompts
from matbot.tutor.pipeline import SAFE_ERROR_MESSAGE
from tests.conftest import FakeLLM, make_difficulty_diagnostics, make_task_payload


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
            normalized_parameters=[SignatureParameter(name="case", value=signature)],
            required_conditions=["valid"],
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
    fake.queue(ReviewerFinal(
        decision="approve", checks=checks(), final=draft,
        reviewed_difficulty_evidence=task.difficulty_evidence,
    ))


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


@pytest.mark.parametrize("returned_title", [
    "Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25.",
    "  Pravila djeljivosti  ",
    "Potpuno nepovezan prikazni naslov",
])
def test_exact_lesson_id_makes_returned_title_non_authoritative(monkeypatch, returned_title):
    """The title is a redundant display copy; ID and semantic review stay strict."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    context, store, fake = build(6, "6-03-004"), SessionStore(), FakeLLM()
    task = task_for(
        context, signature="live-divisibility-title", text="Koji od sljedećih brojeva je djeljiv sa 6?",
        options=("$12$", "$15$", "$25$", "$35$"),
    ).model_copy(update={"selected_lesson_title": returned_title})
    queue_generation(fake, task)

    response = run_practice_turn(store, fake, turn(6, context.topic_id))
    canonical = tutor_pipeline.validate_task_package(task, context)
    session = store.peek("structured")
    assert response["status"] == "ready"
    assert fake.call_count == 2
    assert canonical.selected_lesson_title == context.title
    assert session["lesson_id"] == context.topic_id
    assert session["lesson_title"] == context.title


def test_reviewer_cannot_replace_the_selected_lesson_id(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    context, store, fake = build(6, "6-03-004"), SessionStore(), FakeLLM()
    draft_task = task_for(context, signature="tutor-valid")
    reviewer_task = task_for(context, signature="reviewer-wrong").model_copy(
        update={"selected_lesson_id": "6-03-005"})
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="tacna lekcija", new_task=draft_task)
    fake.queue(draft)
    fake.queue(ReviewerFinal(
        decision="correct", checks=checks(),
        final=draft.model_copy(update={"new_task": reviewer_task}),
        reviewed_difficulty_evidence=reviewer_task.difficulty_evidence,
    ))

    response = run_practice_turn(store, fake, turn(6, context.topic_id))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("structured") is None
    assert fake.call_count == 2


def test_reviewer_inside_lesson_check_remains_fail_closed(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    context, store, fake = build(6, "6-03-004"), SessionStore(), FakeLLM()
    task = task_for(context, signature="inside-lesson")
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="tacna lekcija", new_task=task)
    fake.queue(draft)
    fake.queue(ReviewerFinal(
        decision="approve", checks=checks(inside_lesson=False), final=draft,
        reviewed_difficulty_evidence=task.difficulty_evidence,
    ))

    response = run_practice_turn(store, fake, turn(6, context.topic_id))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("structured") is None
    assert fake.call_count == 2


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


def _direct_level_one_evidence(**updates):
    values = dict(
        reasoning_steps=1, condition_count=1, operation_count=1,
        representation_change_count=0, requires_explanation=False,
        requires_comparison=False, requires_construction=False,
        requires_proof_or_justification=False, combines_concepts=False,
    )
    values.update(updates)
    return DifficultyEvidence(**values)


def test_fake_llm_preserves_explicit_reviewer_evidence_while_binding_fixture_metadata():
    reviewed = _direct_level_one_evidence()
    tutor_evidence = reviewed.model_copy(update={
        "condition_count": 2, "operation_count": 2, "combines_concepts": True,
    })
    tutor_task = make_task_payload().model_copy(update={
        "difficulty_evidence": tutor_evidence,
    })
    tutor = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="fixture", new_task=tutor_task)
    reviewer = ReviewerFinal(
        decision="approve", checks=checks(), final=tutor.model_copy(deep=True),
        reviewed_difficulty_evidence=reviewed,
    )
    fake = FakeLLM([tutor, reviewer])
    fixture_input = (
        "- lekcija: Pravila djeljivosti (6-03-004)\n"
        "SERVER COMMITTED DIFFICULTY LEVEL: 1"
    )

    fake.tutor_turn("fixture", fixture_input)
    result = fake.reviewer_turn("fixture", fixture_input)

    assert fake.call_count == 2
    assert result.output.reviewed_difficulty_evidence == reviewed
    assert reviewer.reviewed_difficulty_evidence == reviewed
    assert reviewer.reviewed_difficulty_evidence != tutor_evidence


@pytest.mark.parametrize("form", [
    "yes/no one-rule check", "matching-value selection", "one-operation arithmetic MCQ",
    "one-value substitution", "property recognition/classification",
])
def test_level_one_accepts_every_direct_introductory_form(form):
    # Grammar is intentionally absent from DifficultyEvidence; the same direct
    # mathematical structure permits every listed visible form.
    assert difficulty_evidence_errors(_direct_level_one_evidence(), 1) == (), form


@pytest.mark.parametrize("updates", [
    {"condition_count": 2},
    {"operation_count": 2},
    {"requires_explanation": True},
    {"requires_comparison": True},
    {"requires_construction": True},
    {"requires_proof_or_justification": True},
    {"representation_change_count": 1},
    {"combines_concepts": True},
])
def test_level_one_does_not_absorb_combined_or_advanced_evidence(updates):
    assert "level_1_is_not_direct_introductory_application" in difficulty_evidence_errors(
        _direct_level_one_evidence(**updates), 1)


def test_exact_live_divisibility_selection_publishes_as_level_one(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(6, "6-03-004"), SessionStore(), FakeLLM()
    task = task_for(
        context, signature="one-rule-selection",
        text="Koji od sljedećih brojeva je djeljiv sa 25?",
        options=("$125$", "$126$", "$127$", "$128$"),
    )
    queue_generation(fake, task)

    response = run_practice_turn(store, fake, turn(6, context.topic_id))
    session = store.peek("structured")
    assert response["status"] == "ready"
    assert fake.call_count == 2
    assert session["difficulty_level"] == 1
    assert session["current_task_signature"]["structured_signature_hash"]


def test_exact_live_divisibility_by_fifteen_publishes_as_bounded_level_two(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(6, "6-03-004"), SessionStore(), FakeLLM()
    queue_generation(fake, task_for(
        context, signature="divisibility-25-level-one",
        text="Koji od sljedećih brojeva je djeljiv sa 25?",
        options=("$125$", "$126$", "$127$", "$128$"),
    ))
    assert run_practice_turn(store, fake, turn(6, context.topic_id))["status"] == "ready"

    reviewed = task_for(
        context, level=2, signature="divisibility-15-level-two",
        text="Koji od sljedećih brojeva je djeljiv sa 15?",
        options=("$15$", "$18$", "$22$", "$26$"),
    ).model_copy(update={
        "difficulty_evidence": DifficultyEvidence(
            reasoning_steps=2, condition_count=2, operation_count=2,
            representation_change_count=0, requires_explanation=False,
            requires_comparison=False, requires_construction=False,
            requires_proof_or_justification=False, combines_concepts=True,
        ),
    })
    draft = TutorDraft(
        intent="harder_task", reply="Evo zadatka.", lesson_focus="tačna lekcija",
        new_task=reviewed,
        difficulty_diagnostics=make_difficulty_diagnostics("higher"),
    )
    reviewer = ReviewerFinal(
        decision="approve", checks=checks(), final=draft,
        reviewed_difficulty_evidence=reviewed.difficulty_evidence,
    )
    fake.queue(draft)
    fake.queue(reviewer)

    response = run_practice_turn(
        store, fake, turn(6, context.topic_id, "Daj mi teži zadatak."))
    session = store.peek("structured")
    assert response["status"] == "ready"
    assert response["answer"].startswith("Evo težeg zadatka.")
    assert fake.call_count == 4
    assert session["difficulty_level"] == 2
    assert session["current_task_difficulty_evidence"] == reviewer.reviewed_difficulty_evidence.model_dump()


def test_advanced_harder_task_preserves_completed_level_one_state(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(6, "6-03-004"), SessionStore(), FakeLLM()
    queue_generation(fake, task_for(context, signature="completed-level-one"))
    assert run_practice_turn(store, fake, turn(6, context.topic_id))["status"] == "ready"
    completed = store.peek("structured")
    completed["task_completed"] = True
    store.save(completed)
    before = copy.deepcopy(store.peek("structured"))

    advanced = task_for(context, level=2, signature="construction-at-level-two").model_copy(
        update={"difficulty_evidence": _direct_level_one_evidence(
            reasoning_steps=2, condition_count=2, operation_count=2,
            requires_construction=True,
        )})
    queue_generation(fake, advanced, "harder_task")

    assert run_practice_turn(
        store, fake, turn(6, context.topic_id, "Daj mi teži zadatak."))["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("structured") == before
    assert fake.call_count == 4


def test_level_three_package_still_publishes_after_two_bounded_transitions(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(6, "6-03-004"), SessionStore(), FakeLLM()
    queue_generation(fake, task_for(context, signature="level-one"))
    assert run_practice_turn(store, fake, turn(6, context.topic_id))["status"] == "ready"
    queue_generation(fake, task_for(context, 2, "level-two"), "harder_task")
    assert run_practice_turn(store, fake, turn(6, context.topic_id, "teži"))["status"] == "ready"
    queue_generation(fake, task_for(context, 3, "level-three"), "harder_task")

    response = run_practice_turn(store, fake, turn(6, context.topic_id, "još teži"))
    assert response["status"] == "ready"
    assert response["answer"].startswith("Evo težeg zadatka.")
    assert store.peek("structured")["difficulty_level"] == 3
    assert fake.call_count == 6


def test_reviewer_independently_repairs_exact_live_level_one_evidence(monkeypatch):
    """The second call may approve wording while correcting Tutor metadata."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(6, "6-03-004"), SessionStore(), FakeLLM()
    reviewed = _direct_level_one_evidence()
    tutor_evidence = reviewed.model_copy(update={
        "condition_count": 2, "operation_count": 2, "combines_concepts": True,
    })
    task = task_for(
        context, signature="reported-live-failure",
        text="Koji od sljedećih brojeva je djeljiv sa 6?",
        options=("$12$", "$15$", "$25$", "$35$"),
    ).model_copy(update={"difficulty_evidence": tutor_evidence})
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="tačna lekcija", new_task=task)
    reviewer = ReviewerFinal(
        decision="approve", checks=checks(), final=draft,
        reviewed_difficulty_evidence=reviewed,
    )
    original_reviewer_evidence = reviewer.reviewed_difficulty_evidence.model_copy(deep=True)
    assert tutor_evidence != original_reviewer_evidence
    fake.queue(draft)
    fake.queue(reviewer)

    response = run_practice_turn(store, fake, turn(6, context.topic_id))
    session = store.peek("structured")
    assert response["status"] == "ready"
    assert fake.call_count == 2
    assert session["difficulty_level"] == 1
    assert session["current_task_difficulty_evidence"] == reviewed.model_dump()
    assert session["current_task_difficulty_evidence"] != tutor_evidence.model_dump()
    assert draft.new_task.difficulty_evidence == tutor_evidence
    assert reviewer.reviewed_difficulty_evidence == original_reviewer_evidence
    assert reviewer.final.new_task.text == task.text
    assert reviewer.final.new_task.options == task.options
    assert reviewer.final.new_task.correct_option_id == task.correct_option_id
    assert reviewer.final.new_task.expected_answer == task.expected_answer
    assert reviewer.final.new_task.solution == task.solution
    assert reviewer.final.new_task.task_signature == task.task_signature


def test_reviewer_agreement_publishes_without_evidence_correction(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    context, store, fake = build(6, "6-03-004"), SessionStore(), FakeLLM()
    task = task_for(context, signature="reviewer-agrees")
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="tačna lekcija", new_task=task)
    fake.queue(draft)
    fake.queue(ReviewerFinal(
        decision="approve", checks=checks(), final=draft,
        reviewed_difficulty_evidence=task.difficulty_evidence,
    ))

    assert run_practice_turn(store, fake, turn(6, context.topic_id))["status"] == "ready"
    assert store.peek("structured")["current_task_difficulty_evidence"] == task.difficulty_evidence.model_dump()
    assert fake.call_count == 2


@pytest.mark.parametrize("tutor_updates,reviewer_updates", [
    ({"condition_count": 2, "operation_count": 2, "combines_concepts": True},
     {"condition_count": 2, "operation_count": 2, "combines_concepts": True}),
])
def test_authoritative_reviewer_evidence_rejects_invalid_level_one_without_state_mutation(
        monkeypatch, tutor_updates, reviewer_updates):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(6, "6-03-004"), SessionStore(), FakeLLM()
    task = task_for(context, signature=str(tutor_updates) + str(reviewer_updates)).model_copy(
        update={"difficulty_evidence": _direct_level_one_evidence(**tutor_updates)})
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="tačna lekcija", new_task=task)
    fake.queue(draft)
    fake.queue(ReviewerFinal(
        decision="approve", checks=checks(), final=draft,
        reviewed_difficulty_evidence=_direct_level_one_evidence(**reviewer_updates),
    ))

    assert run_practice_turn(store, fake, turn(6, context.topic_id))["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("structured") is None
    assert fake.call_count == 2


def test_reviewer_level_two_disagreement_rejects_and_preserves_its_evidence(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(6, "6-03-004"), SessionStore(), FakeLLM()
    tutor_evidence = _direct_level_one_evidence()
    reviewer_evidence = _direct_level_one_evidence(
        reasoning_steps=2, condition_count=2, operation_count=2,
    )
    task = task_for(context, signature="reviewer-finds-level-two").model_copy(
        update={"difficulty_evidence": tutor_evidence})
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="tačna lekcija", new_task=task)
    reviewer = ReviewerFinal(
        decision="approve", checks=checks(), final=draft,
        reviewed_difficulty_evidence=reviewer_evidence,
    )
    assert tutor_evidence != reviewer_evidence
    fake.queue(draft)
    fake.queue(reviewer)

    assert run_practice_turn(store, fake, turn(6, context.topic_id))["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("structured") is None
    assert fake.call_count == 2
    assert reviewer.reviewed_difficulty_evidence == reviewer_evidence


def test_reviewer_omitting_independent_evidence_rejects_before_publication(monkeypatch, caplog):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    context, store, fake = build(6, "6-03-004"), SessionStore(), FakeLLM()
    task = task_for(context, signature="reviewer-omits-evidence")
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="tačna lekcija", new_task=task)
    fake.queue(draft)
    fake.queue(ReviewerFinal(
        decision="approve", checks=checks(), final=draft,
        reviewed_difficulty_evidence=None,
    ))

    assert run_practice_turn(store, fake, turn(6, context.topic_id))["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("structured") is None
    assert fake.call_count == 2
    assert "stage=reviewer_payload" in caplog.text


def test_rejected_level_one_evidence_preserves_prior_session(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(6, "6-03-004"), SessionStore(), FakeLLM()
    queue_generation(fake, task_for(context, signature="committed"))
    assert run_practice_turn(store, fake, turn(6, context.topic_id))["status"] == "ready"
    before = copy.deepcopy(store.peek("structured"))
    rejected = task_for(context, signature="two-operations").model_copy(update={
        "difficulty_evidence": _direct_level_one_evidence(operation_count=2),
    })
    queue_generation(fake, rejected)

    assert run_practice_turn(store, fake, turn(6, context.topic_id))["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("structured") == before
    assert fake.call_count == 4


def test_tutor_and_reviewer_prompts_define_level_one_selection_without_yes_no_requirement():
    context = build(6, "6-03-004")
    tutor = tutor_prompts.build_tutor_instructions(context)
    reviewer = tutor_prompts.build_reviewer_instructions(context)
    assert "selection" in tutor.lower()
    assert "choosing a visible option" in tutor.lower()
    assert "one-rule selection" in reviewer.lower()
    assert "reviewed_difficulty_evidence" in reviewer
    assert "ignore tutor numerical counts" in reviewer.lower()
    assert "combines_concepts` alone is not level 3" in tutor.lower()
    assert "combines_concepts` alone is not level 3" in reviewer.lower()


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
    fake.queue(ReviewerFinal(
        decision="correct", checks=checks(), final=reviewer_final,
        reviewed_difficulty_evidence=corrected.difficulty_evidence,
    ))

    assert run_practice_turn(store, fake, turn(6, context.topic_id))["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("structured") is None


# ---------------------------------------------------------------------------
# ANOTACIJA ZBIRA CIFARA KROZ CIJEO DVOPOZIVNI PUT (živi gate b7025e4)
# ---------------------------------------------------------------------------
# Živi pad: lekcija 6-03-004, zadatak „Koji od sljedećih brojeva je djeljiv sa
# 9?“. Sve je prošlo (struktura, identitet lekcije, Tutor, nezavisan Reviewer,
# dokaz nivoa 1, MCQ paket, broj poziva) osim numeričke provjere `solution`
# polja: `numeric_equality_mismatch: '12:\\;1+2' (14) != '3' (3) [solution]`.

_LIVE_DIVISIBILITY_TEXT = "Koji od sljedećih brojeva je djeljiv sa 9?"
_LIVE_DIVISIBILITY_OPTIONS = ("$135$", "$12$", "$121$", "$142$")


def _digit_sum_task(context, solution, signature="digit-sum"):
    """MCQ paket lekcije o djeljivosti čije `solution` nosi zbirove cifara."""
    return task_for(
        context, signature=signature, text=_LIVE_DIVISIBILITY_TEXT,
        options=_LIVE_DIVISIBILITY_OPTIONS, correct=0,
    ).model_copy(update={"solution": solution})


def test_live_digit_sum_annotation_solution_publishes_in_exactly_two_calls(monkeypatch):
    """Tačno anotirano rješenje mora se objaviti — i to u TAČNO dva poziva."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    context, store, fake = build(6, "6-03-004"), SessionStore(), FakeLLM()
    solution = (
        "Zbir cifara broja $135$ je $135:\\;1+3+5=9$, pa je djeljiv sa $9$. "
        "Za $12$ je $12:\\;1+2=3$, a $3$ nije djeljivo sa $9$."
    )
    queue_generation(fake, _digit_sum_task(context, solution))

    response = run_practice_turn(store, fake, turn(6, context.topic_id))
    session = store.peek("structured")

    assert response["status"] == "ready"
    assert fake.call_count == 2
    assert len(fake.tutor_calls) == 1 and len(fake.reviewer_calls) == 1
    assert session["lesson_id"] == "6-03-004"
    assert session["solution_summary"] == solution
    # Interna dijagnostika i rješenje NIKAD ne izlaze u browser.
    assert "1+3+5" not in response["answer"]


def test_incorrect_digit_sum_annotation_rejects_transactionally(monkeypatch):
    """Stvarno pogrešna anotirana jednakost i dalje pada — bez mutacije stanja.

    Prvi turn objavi ispravan zadatak, drugi donese `$135:\\;1+3+5=8$`
    (zbir je 9). Turn se odbija, prethodna sesija ostaje netaknuta, i drugi
    turn i dalje troši TAČNO dva poziva (ukupno četiri) — bez retryja."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    context, store, fake = build(6, "6-03-004"), SessionStore(), FakeLLM()
    queue_generation(fake, _digit_sum_task(
        context, "Zbir cifara: $135:\\;1+3+5=9$.", signature="valid-first"))
    assert run_practice_turn(store, fake, turn(6, context.topic_id))["status"] == "ready"
    before = copy.deepcopy(store.peek("structured"))

    queue_generation(fake, _digit_sum_task(
        context, "Zbir cifara: $135:\\;1+3+5=8$.", signature="wrong-second"))
    response = run_practice_turn(store, fake, turn(6, context.topic_id))

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("structured") == before
    assert fake.call_count == 4


def test_digit_sum_that_is_not_the_prefix_digits_still_rejects(monkeypatch):
    """`1+3` nisu cifre broja $12$ → dvotačka ostaje dijeljenje i paket pada.

    Ovim se dokazuje da popravka NIJE „obriši sve prije dvotačke“: takva bi
    izmjena pustila `12:1+3=4` kroz samo zato što je 1+3=4."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    context, store, fake = build(6, "6-03-004"), SessionStore(), FakeLLM()
    queue_generation(fake, _digit_sum_task(context, "Zbir cifara: $12:\\;1+3=4$."))

    assert run_practice_turn(store, fake, turn(6, context.topic_id))["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("structured") is None
    assert fake.call_count == 2


def test_genuine_colon_division_in_solution_still_publishes_and_still_rejects(monkeypatch):
    """Školsko dijeljenje kroz isti put: tačno prolazi, netačno pada."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    context, store, fake = build(6, "6-03-004"), SessionStore(), FakeLLM()
    queue_generation(fake, _digit_sum_task(
        context, "Provjera dijeljenjem: $135:9=15$.", signature="division-ok"))
    assert run_practice_turn(store, fake, turn(6, context.topic_id))["status"] == "ready"
    before = copy.deepcopy(store.peek("structured"))

    queue_generation(fake, _digit_sum_task(
        context, "Provjera dijeljenjem: $135:9=16$.", signature="division-wrong"))
    assert run_practice_turn(store, fake, turn(6, context.topic_id))["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("structured") == before
    assert fake.call_count == 4
