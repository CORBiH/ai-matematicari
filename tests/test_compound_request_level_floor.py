"""Faza 4G — deterministički floor cilja za izričit složen zahtjev djeljivosti.

ŽIVI F4G TALAS (G03, G05; isti oblik ranije F4F F13–F15): „Daj mi MCQ zadatak
gdje broj mora biti djeljiv i sa 6 i sa 25.“ na svježoj sesiji cilja nivo 1, a
zadatak s dva uslova djeljivosti je definiciono nivo 2 (`difficulty_profile` ga
tako i mjeri). Svaki takav izričit zahtjev je zato OBAVEZNO padao zatvoreno:
recenzent je mogao ili vratiti `fail_closed` (G03: `difficulty_not_changed`)
ili izdati jedno-pravilni zadatak koji ne odgovara izričitom zahtjevu.

Popravka: kad učenikova VLASTITA poruka deterministički traži ≥2 djelioca
(ista zatvorena gramatika kojom se čita tekst zadatka; bez negacije i bez
disjunkcije), server cilja NOVI zadatak na nivo 2 i to izričito saopštava
oba modela. `easier_task`/`harder_task` ostaju čisti koraci progresije.
"""
import pytest

from matbot import mcq_integrity
from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor import pipeline as tutor_pipeline
from matbot.tutor import prompts as tutor_prompts
from matbot.tutor.lesson_context import build
from tests.conftest import (FakeLLM, make_reviewer_final, make_task_payload,
                            make_tutor_draft)
from matbot.tutor.schema import DifficultyEvidence

GRADE, TOPIC = 6, "6-03-004"
COMPOUND = "Daj mi MCQ zadatak gdje broj mora biti djeljiv i sa 6 i sa 25."
PLAIN = "Daj mi zadatak."


# ---------------------------------------------------------------------------
# 1) DETERMINISTIČKI PREDIKAT NAD PORUKOM
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    COMPOUND,
    "Daj mi zadatak gdje broj mora biti djeljiv sa 6 i istovremeno sa 25.",
    "Hoću zadatak gdje je broj djeljiv sa 4, ali i sa 9.",
    "Zadatak: broj djeljiv sa 2, 3 i 5.",
])
def test_compound_requests_are_recognized(message):
    assert mcq_integrity.explicit_compound_divisor_request(message)


@pytest.mark.parametrize("message", [
    PLAIN,
    "Daj mi zadatak o djeljivosti sa 25.",                      # jedan djelilac
    "Daj mi zadatak gdje broj NIJE djeljiv sa 6 i sa 25.",      # negacija
    "Daj mi zadatak gdje je broj djeljiv sa 4 ili sa 6.",       # disjunkcija
    "Izračunaj $\\frac{2}{7}+\\frac{3}{7}$.",                   # nije djeljivost
    "",
])
def test_non_compound_requests_are_not_recognized(message):
    assert not mcq_integrity.explicit_compound_divisor_request(message)


# ---------------------------------------------------------------------------
# 2) SERVERSKI CILJ
# ---------------------------------------------------------------------------

def fresh_session():
    return {"difficulty_level": 1, "current_task": "", "correct_streak": 0}


def test_fresh_compound_request_targets_level_two():
    assert tutor_pipeline._target_level_for(fresh_session(), "generate_task",
                                            COMPOUND) == 2


def test_fresh_plain_request_still_targets_level_one():
    assert tutor_pipeline._target_level_for(fresh_session(), "generate_task",
                                            PLAIN) == 1


def test_easier_and_harder_ignore_the_floor():
    session = {"difficulty_level": 2, "current_task": "aktivan", "correct_streak": 0}
    assert tutor_pipeline._target_level_for(session, "easier_task", COMPOUND) == 1
    assert tutor_pipeline._target_level_for(session, "harder_task", COMPOUND) == 3


def test_next_task_with_compound_message_keeps_the_floor():
    session = {"difficulty_level": 1, "current_task": "aktivan",
               "correct_streak": 0, "last_result": ""}
    assert tutor_pipeline._target_level_for(session, "next_task", COMPOUND) == 2


# ---------------------------------------------------------------------------
# 3) OBA MODELA DOBIJAJU ISTINITU SERVERSKU LINIJU
# ---------------------------------------------------------------------------

def _session_stub():
    return {"current_task": "", "current_options": [], "expected_answer_summary": "",
            "difficulty": "easy", "hint_level": 0, "difficulty_level": 1,
            "recent_tasks": [], "recent_turns": []}


def test_state_block_carries_the_notice_only_for_compound_messages():
    block = tutor_prompts._state_block(_session_stub(), COMPOUND)
    assert "EXPLICIT REQUEST NOTICE" in block
    assert "level is 2" in block
    assert "EXPLICIT REQUEST NOTICE" not in tutor_prompts._state_block(
        _session_stub(), PLAIN)


# ---------------------------------------------------------------------------
# 4) CIJELI DVOPOZIVNI PUT
# ---------------------------------------------------------------------------

def evidence_level2():
    return DifficultyEvidence(
        reasoning_steps=2, condition_count=2, operation_count=2,
        representation_change_count=0, requires_explanation=False,
        requires_comparison=False, requires_construction=False,
        requires_proof_or_justification=False, combines_concepts=True)


def compound_task(context, level=2):
    task = make_task_payload(
        text="Koji od ponuđenih brojeva je djeljiv i sa 6 i sa 25?",
        options=("150", "60", "75", "90"), correct_option_index=0, expected="150")
    return task.model_copy(update={
        "selected_lesson_id": context.topic_id,
        "selected_lesson_title": context.title,
        "target_difficulty_level": level,
        "difficulty_evidence": evidence_level2()})


def turn(message):
    return {"session_id": "floor-1", "grade": GRADE, "selected_topic": TOPIC,
            "selected_oblast": "", "student_message": message, "intent": "",
            "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
            "interaction_type": "student_question", "selected_option_id": "",
            "client_turn_id": ""}


@pytest.fixture
def universal(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def test_compound_request_publishes_an_honest_level_two_package(universal):
    context, store, fake = build(GRADE, TOPIC), SessionStore(), FakeLLM()
    draft = make_tutor_draft(intent="generate_task", new_task=compound_task(context))
    fake.queue(draft)
    fake.queue(make_reviewer_final(final=draft))

    response = run_practice_turn(store, fake, turn(COMPOUND))

    assert response.get("status") == "ready", response.get("answer")
    assert fake.call_count == 2
    session = store.peek("floor-1")
    assert session["difficulty_level"] == 2
    # Oba modela su STVARNO dobila serversku liniju o podignutom cilju.
    for captured in (fake.tutor_calls[0][1], fake.reviewer_calls[0][1]):
        assert "EXPLICIT REQUEST NOTICE" in captured


def test_plain_request_still_rejects_an_unrequested_level_two_package(universal):
    """Floor ne smije oslabiti postojeću zaštitu: bez izričitog zahtjeva
    nivo-2 paket na svježoj sesiji i dalje pada (overshooting)."""
    context, store, fake = build(GRADE, TOPIC), SessionStore(), FakeLLM()
    draft = make_tutor_draft(intent="generate_task", new_task=compound_task(context))
    fake.queue(draft)
    fake.queue(make_reviewer_final(final=draft))

    response = run_practice_turn(store, fake, turn(PLAIN))

    assert response.get("status") is None
    assert store.peek("floor-1") is None
    assert fake.call_count == 2
