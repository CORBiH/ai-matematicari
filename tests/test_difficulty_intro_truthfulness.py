r"""Uvodna rečenica ne smije tvrditi promjenu težine koja se nije desila.

PRODUKCIJSKI NALAZ i ŽIVI TALAS F4F (scenariji F09 i F10, commit 3e9bae2):

    aktivni nivo : 1
    učenik       : „Daj mi lakši zadatak.“
    objavljeno   : „Evo lakšeg zadatka.“ — a nivo je ostao 1

Ispod nivoa 1 nema ničega. Serverski kontroler to zna
(`_target_level_for` vraća max(current-1, 1)), ali je uvod biran ISKLJUČIVO iz
modelove namjere, pa je obećavao spuštanje koje se nije dogodilo.

Legacy put je ovu granicu odavno imao (`_ANOTHER_INTRO_TASK_INTRO`,
`_SAME_SUPPORTED_DIFFICULTY_INTRO` u matbot/practice.py); univerzalni put je pri
pivotu nije preuzeo.

Uvod se sada bira iz STVARNE serverske tranzicije (prethodni → ciljani nivo),
nikad iz onoga što je model rekao da radi. Sam zadatak, težina i sve provjere
ostaju nepromijenjeni — mijenja se samo istinitost rečenice.
"""
import pytest

from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor import pipeline as tutor_pipeline
from tests.conftest import (FakeLLM, make_difficulty_diagnostics, make_task_payload,
                            make_tutor_draft, queue_two_call)

LESSON, GRADE = "6-03-004", 6


@pytest.fixture(autouse=True)
def _universal_runtime(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def _turn(session_id, message, **changes):
    turn = {
        "session_id": session_id, "grade": GRADE, "selected_topic": LESSON,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    turn.update(changes)
    return turn


def _task(text, options, marked=0):
    return make_task_payload(text=text, options=options,
                             correct_option_index=marked, expected=options[marked])


TASK_1 = ("Koji od sljedećih brojeva je djeljiv sa 10?", ("70", "41", "33", "58"))
TASK_2 = ("Koji od sljedećih brojeva je djeljiv sa 5?", ("35", "42", "61", "24"))
TASK_3 = ("Koji od sljedećih brojeva je djeljiv sa 25?", ("75", "42", "61", "24"))


def _evidence_for(level):
    """Dokaz težine koji zadovoljava traženi nivo (matbot/tutor/schema.py)."""
    from matbot.tutor.schema import DifficultyEvidence

    if level >= 3:
        return DifficultyEvidence(
            reasoning_steps=3, condition_count=3, operation_count=2,
            representation_change_count=0, requires_explanation=False,
            requires_comparison=False, requires_construction=True,
            requires_proof_or_justification=True, combines_concepts=True)
    if level == 2:
        return DifficultyEvidence(
            reasoning_steps=2, condition_count=2, operation_count=2,
            representation_change_count=0, requires_explanation=False,
            requires_comparison=False, requires_construction=False,
            requires_proof_or_justification=False, combines_concepts=False)
    return DifficultyEvidence(
        reasoning_steps=1, condition_count=1, operation_count=1,
        representation_change_count=0, requires_explanation=False,
        requires_comparison=False, requires_construction=False,
        requires_proof_or_justification=False, combines_concepts=False)


def _publish(store, fake, session_id, task, message="Daj mi zadatak.", level=1,
             **turn_kwargs):
    """Objavi zadatak; `level` je nivo koji SERVER cilja za ovaj turn.

    Recenzentov `reviewed_difficulty_evidence` mora odgovarati tom nivou —
    inače turn ispravno pada na postojećoj provjeri dosljednosti dokaza, prije
    nego što uvodna rečenica uopšte nastane."""
    from tests.conftest import make_reviewer_final

    intent = turn_kwargs.pop("intent_name", "generate_task")
    diagnostics = (make_difficulty_diagnostics(
        direction="lower" if intent == "easier_task" else "higher")
        if intent in ("easier_task", "harder_task") else None)
    evidence = _evidence_for(level)
    payload = _task(*task).model_copy(update={"difficulty_evidence": evidence})
    draft = make_tutor_draft(intent=intent, new_task=payload,
                             difficulty_diagnostics=diagnostics)
    fake.queue(draft)
    fake.queue(make_reviewer_final(final=draft, reviewed_difficulty_evidence=evidence))
    return run_practice_turn(store, fake, _turn(session_id, message, **turn_kwargs))


# ---------------------------------------------------------------------------
# 1. GRANICA NIVOA 1 — NEMA NIŽEG
# ---------------------------------------------------------------------------

def test_easier_at_level_one_never_claims_the_level_was_lowered():
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, "intro-1", TASK_1)
    assert store.peek("intro-1")["difficulty_level"] == 1

    response = _publish(store, fake, "intro-1", TASK_2, "Daj mi lakši zadatak.",
                        intent_name="easier_task", difficulty_request="easier")

    assert response["status"] == "ready"
    assert "Evo lakšeg zadatka." not in response["answer"], response["answer"]
    assert store.peek("intro-1")["difficulty_level"] == 1


def test_easier_at_level_one_still_publishes_a_task():
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, "intro-2", TASK_1)
    response = _publish(store, fake, "intro-2", TASK_2, "Daj mi lakši zadatak.",
                        intent_name="easier_task", difficulty_request="easier")
    assert TASK_2[0] in response["answer"]
    assert store.peek("intro-2")["current_task"] == TASK_2[0]


def test_the_boundary_intro_names_the_level_truthfully():
    """Na najnižem nivou cilj JE poznat, pa se smije imenovati."""
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, "intro-3", TASK_1)
    response = _publish(store, fake, "intro-3", TASK_2, "Daj mi lakši zadatak.",
                        intent_name="easier_task", difficulty_request="easier")
    assert response["answer"].startswith(tutor_pipeline.INTRO_AT_EASIEST_LEVEL)


# ---------------------------------------------------------------------------
# 2. STVARNE PROMJENE I DALJE SE NAJAVLJUJU
# ---------------------------------------------------------------------------

def test_a_real_easier_transition_still_announces_it():
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, "intro-4", TASK_1)
    _publish(store, fake, "intro-4", TASK_2, "Daj mi teži zadatak.", level=2,
             intent_name="harder_task", difficulty_request="harder")
    assert store.peek("intro-4")["difficulty_level"] == 2

    response = _publish(store, fake, "intro-4", TASK_3, "Daj mi lakši zadatak.",
                        intent_name="easier_task", difficulty_request="easier")
    assert response["answer"].startswith("Evo lakšeg zadatka.")
    assert store.peek("intro-4")["difficulty_level"] == 1


def test_a_real_harder_transition_still_announces_it():
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, "intro-5", TASK_1)
    response = _publish(store, fake, "intro-5", TASK_2, "Daj mi teži zadatak.", level=2,
                        intent_name="harder_task", difficulty_request="harder")
    assert response["answer"].startswith("Evo težeg zadatka.")


def test_harder_at_the_maximum_level_does_not_promise_more():
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, "intro-6", TASK_1)
    _publish(store, fake, "intro-6", TASK_2, "Daj mi teži zadatak.", level=2,
             intent_name="harder_task", difficulty_request="harder")
    _publish(store, fake, "intro-6", TASK_3, "Daj mi teži zadatak.", level=3,
             intent_name="harder_task", difficulty_request="harder")
    assert store.peek("intro-6")["difficulty_level"] == 3

    response = _publish(store, fake, "intro-6",
                        ("Koji od sljedećih brojeva je djeljiv sa 9?", ("81", "42", "61", "24")),
                        "Daj mi teži zadatak.", level=3, intent_name="harder_task",
                        difficulty_request="harder")
    assert response["status"] == "ready"
    assert "Evo težeg zadatka." not in response["answer"], response["answer"]
    assert response["answer"].startswith(tutor_pipeline.INTRO_AT_HARDEST_LEVEL)
    assert store.peek("intro-6")["difficulty_level"] == 3


def test_a_plain_new_task_intro_is_unchanged():
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, "intro-7", TASK_1)
    response = _publish(store, fake, "intro-7", TASK_2, "Daj mi novi zadatak.",
                        intent_name="next_task")
    assert response["answer"].startswith("Evo sljedećeg zadatka.")


def test_the_intro_is_derived_from_the_server_transition_not_the_model_intent():
    """Model tvrdi easier; server zna da nivo ne može niže — server odlučuje."""
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, "intro-8", TASK_1)
    response = _publish(store, fake, "intro-8", TASK_2, "Daj mi lakši zadatak.",
                        intent_name="easier_task", difficulty_request="easier")
    assert "lakšeg" not in response["answer"].split("\n")[0]
