"""Faza 4H, Workstreams J/K/L — kompaktno odobrenje, keširabilan prefiks, rok.

Forenzika: recenzent je na `approve` vraćao EHO cijelog paketa (~1400 izlaznih
tokena medijalno ≈ 15+ s generisanja pri ~86 tok/s), a zajednički prefiks
instrukcija među lekcijama bio je svega ~1,3–2,2 K znakova jer je sadržaj
zavisan od lekcije stajao u drugom pasusu. Rok turna nije postojao: skoro
istekao Tutor poziv slijepo je započinjao dug recenzentski.
"""
import pytest

from matbot import config
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor import prompts as tutor_prompts
from matbot.tutor.lesson_context import build
from matbot.tutor.schema import ReviewerFinal, UnifiedOutputError, validate_reviewer
from tests.conftest import (FakeLLM, make_reviewer_checks, make_reviewer_final,
                            make_task_payload, make_tutor_draft)

GRADE, LESSON, SESSION = 6, "6-03-004", "compact-1"


def turn(message="Daj mi zadatak.", **changes):
    payload = {
        "session_id": SESSION, "grade": GRADE, "selected_topic": LESSON,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    payload.update(changes)
    return payload


@pytest.fixture
def universal(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def divisibility_task(text="Koji od ponuđenih brojeva je djeljiv sa 25?",
                      options=("725", "714", "738", "741")):
    return make_task_payload(text=text, options=options,
                             correct_option_index=0, expected=options[0])


# ---------------------------------------------------------------------------
# 1) WORKSTREAM J — KOMPAKTNO ODOBRENJE
# ---------------------------------------------------------------------------

def test_approve_without_final_publishes_the_tutor_draft(universal):
    store, fake = SessionStore(), FakeLLM()
    task = divisibility_task()
    draft = make_tutor_draft(intent="generate_task", new_task=task)
    fake.queue(draft)
    compact = make_reviewer_final(final=draft).model_copy(update={"final": None})
    fake.queue(compact)

    response = run_practice_turn(store, fake, turn())

    assert response["status"] == "ready", response.get("answer")
    assert fake.call_count == 2
    session = store.peek(SESSION)
    assert session["current_task"] == task.text
    assert session["expected_answer_summary"] == "725"


def test_approve_echo_cannot_silently_alter_the_draft(universal):
    """Recenzent na `approve` pošalje IZMIJENJEN final — server ga ignoriše i
    objavljuje nacrt."""
    store, fake = SessionStore(), FakeLLM()
    task = divisibility_task()
    draft = make_tutor_draft(intent="generate_task", new_task=task)
    altered_task = divisibility_task(
        text="Koji od ponuđenih brojeva je djeljiv sa 10?",
        options=("730", "714", "737", "741"))
    altered = draft.model_copy(update={"new_task": altered_task})
    fake.queue(draft)
    fake.queue(make_reviewer_final(final=altered))   # decision je approve

    response = run_practice_turn(store, fake, turn())

    assert response["status"] == "ready"
    session = store.peek(SESSION)
    assert session["current_task"] == task.text          # nacrt, ne eho
    assert "10" not in session["current_task"]


def test_correct_still_requires_a_complete_final(universal):
    store, fake = SessionStore(), FakeLLM()
    task = divisibility_task()
    draft = make_tutor_draft(intent="generate_task", new_task=task)
    fake.queue(draft)
    broken = make_reviewer_final(final=draft).model_copy(
        update={"decision": "correct", "final": None})
    fake.queue(broken)

    response = run_practice_turn(store, fake, turn())

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(SESSION) is None
    assert fake.call_count == 2


def test_validate_reviewer_checks_evidence_against_the_draft_on_approve():
    context = build(GRADE, LESSON)
    task = divisibility_task().model_copy(update={
        "selected_lesson_id": context.topic_id,
        "selected_lesson_title": context.title,
        "target_difficulty_level": 1})
    draft = make_tutor_draft(intent="generate_task", new_task=task)
    from matbot.tutor.schema import DifficultyEvidence
    outside = DifficultyEvidence(
        reasoning_steps=3, condition_count=3, operation_count=4,
        representation_change_count=2, requires_explanation=True,
        requires_comparison=False, requires_construction=False,
        requires_proof_or_justification=True, combines_concepts=True)
    compact = ReviewerFinal(decision="approve", checks=make_reviewer_checks(),
                            final=None, reviewed_difficulty_evidence=outside)
    with pytest.raises(UnifiedOutputError):
        validate_reviewer(compact, draft)


# ---------------------------------------------------------------------------
# 2) WORKSTREAM L — ROK TURNA
# ---------------------------------------------------------------------------

def test_exhausted_turn_deadline_skips_the_reviewer_and_fails_safe(
        universal, monkeypatch):
    monkeypatch.setattr(config, "practice_turn_deadline_s", lambda: 0.001)
    store, fake = SessionStore(), FakeLLM()
    draft = make_tutor_draft(intent="generate_task", new_task=divisibility_task())
    fake.queue(draft)

    response = run_practice_turn(store, fake, turn())

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert fake.call_count == 1                # recenzent NIJE ni pozvan
    assert store.peek(SESSION) is None         # bez mutacije


def test_reviewer_receives_a_narrowed_remaining_budget(universal):
    store, fake = SessionStore(), FakeLLM()
    task = divisibility_task()
    draft = make_tutor_draft(intent="generate_task", new_task=task)
    fake.queue(draft)
    fake.queue(make_reviewer_final(final=draft))

    run_practice_turn(store, fake, turn())

    assert fake.reviewer_timeouts
    budget = fake.reviewer_timeouts[0]
    assert budget is not None
    assert budget <= config.AI_TIMEOUT_S
    assert budget > 0


# ---------------------------------------------------------------------------
# 3) WORKSTREAM K — KEŠIRABILAN PREFIKS
# ---------------------------------------------------------------------------

def _common_prefix_length(first, second):
    count = 0
    for a, b in zip(first, second):
        if a != b:
            break
        count += 1
    return count


def test_tutor_instructions_share_a_long_static_prefix_across_lessons():
    a = tutor_prompts.build_tutor_instructions(build(6, "6-04-009"))
    b = tutor_prompts.build_tutor_instructions(build(9, "9-05-010"))
    assert _common_prefix_length(a, b) > 6000
    # Dinamički dio (razred/lekcija) je pri KRAJU instrukcija.
    assert a.find("6. razred") > len(a) // 2


def test_reviewer_instructions_share_a_long_static_prefix_across_lessons():
    a = tutor_prompts.build_reviewer_instructions(build(6, "6-04-009"))
    b = tutor_prompts.build_reviewer_instructions(build(9, "9-05-010"))
    assert _common_prefix_length(a, b) > 6000


def test_reviewer_prompt_forbids_the_approve_echo():
    text = tutor_prompts.build_reviewer_instructions(build(GRADE, LESSON))
    assert "`final` IZOSTAVI" in text


# ---------------------------------------------------------------------------
# Kapacitetna ekspanzija: pipeline testovi ovog fajla ispituju MODEL-strategiju
# na lekciji koju produkcija sada rutira deterministički; isključenje je isti
# mehanizam kao produkcijski rollback (MATBOT_DETERMINISTIC_PRACTICE=disabled).
# Testovi promptova ispod ne diraju rutiranje.
# ---------------------------------------------------------------------------
import pytest as _pytest_capex


@_pytest_capex.fixture(autouse=True)
def _model_route_only_capex(monkeypatch):
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")
