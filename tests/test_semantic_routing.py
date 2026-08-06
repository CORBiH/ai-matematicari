"""Faza 4B — RUTIRANJE: lekcija sa semantičkim ugovorom ide semantičkim putem.

KORIJEN PROBLEMA (Faza 4A izvještaj): router je gledao samo „ima li lekcija
K1/K3 red“, pa su četiri lekcije porodice `fraction_arithmetic_direct` u
konfiguraciji release gatea (universal_two_call + difficulty levels) padale na
legacy jednopozivni generator i time zaobilazile SVOJ semantički ugovor:
Tutor kontekst, recenzentovu ispravku i završnu semantičku revalidaciju.

Ovi testovi su napisani PRIJE popravke i dokazuju traženo ponašanje u TAČNO
onom okruženju koje koristi obavezni live release gate.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from matbot import practice  # noqa: E402
from matbot.contracts import registry as k1k3_registry  # noqa: E402
from matbot.semantics import contracts as sem_contracts  # noqa: E402
from matbot.semantics import detectors as sem_detectors  # noqa: E402
from matbot.tutor import lesson_context as lesson_context_module  # noqa: E402
from matbot.tutor import pipeline as tutor_pipeline  # noqa: E402
from tests.conftest import (make_reviewer_final, make_task_payload,  # noqa: E402
                            make_tutor_draft)

SEMANTIC_LESSONS = ("6-04-009", "6-04-010", "6-04-011", "6-04-012")
K1K3_ONLY_LESSONS = ("6-04-005", "6-04-006")   # K1/K3 bez semantičkog ugovora
DIVISIBILITY = "6-03-004"
NON_PILOT = "6-04-014"

VALID_TEXT = {
    "6-04-009": "Izračunaj $\\frac{2}{7} + \\frac{3}{7}$.",
    "6-04-010": "Izračunaj $\\frac{1}{2} + \\frac{1}{4}$.",
    "6-04-011": "Izračunaj $3 \\cdot \\frac{2}{5}$.",
    "6-04-012": "Izračunaj $\\frac{4}{5} : 2$.",
}
OPTIONS = ("$\\frac{5}{7}$", "$\\frac{5}{14}$", "$\\frac{6}{7}$", "$\\frac{1}{7}$")


@pytest.fixture
def release_gate_env(monkeypatch):
    """TAČNO okruženje obaveznog live release gatea."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    return True


def turn_for(topic_id, grade=6, **changes):
    payload = {
        "session_id": f"rt-{topic_id}", "grade": grade,
        "selected_topic": topic_id, "selected_oblast": "nepouzdano",
        "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    payload.update(changes)
    return payload


def task_for(lesson_id, text=None, options=OPTIONS, correct_index=0, level=1,
             evidence=None, variant=""):
    context = lesson_context_module.build(6, lesson_id)
    payload = make_task_payload(text=text or VALID_TEXT.get(lesson_id, "x"),
                                options=list(options),
                                correct_option_index=correct_index,
                                expected=options[correct_index])
    updates = {"selected_lesson_id": context.topic_id,
               "selected_lesson_title": context.title,
               "target_difficulty_level": level}
    if evidence is not None:
        updates["difficulty_evidence"] = evidence
    if variant:
        # Različita OBJAVA po pozivu — inače ispravno reaguje zaštita od
        # ponavljanja. Od Faze 4F se mijenja i VIDLJIVI zadatak, ne samo
        # deklarisani potpis: kanonski identitet se računa iz onoga što učenik
        # vidi, pa je varijanta koja mijenja samo `task_signature` upravo
        # produkcijski defekt koji je pustio isti zadatak dvaput.
        from matbot.tutor.schema import SignatureParameter
        updates["task_signature"] = payload.task_signature.model_copy(
            update={"normalized_parameters": [
                SignatureParameter(name="variant", value=str(variant))]})
        updates["text"] = f"{updates.get('text', payload.text)} (varijanta {variant})"
    return payload.model_copy(update=updates)


def level_two_evidence():
    from matbot.tutor.schema import DifficultyEvidence

    return DifficultyEvidence(
        reasoning_steps=2, condition_count=1, operation_count=2,
        representation_change_count=0, requires_explanation=False,
        requires_comparison=False, requires_construction=False,
        requires_proof_or_justification=False, combines_concepts=False)


@pytest.fixture
def route_spy(monkeypatch):
    """Zabilježi KOJI put je router izabrao, bez pokretanja turna."""
    seen = []
    monkeypatch.setattr(tutor_pipeline, "run_turn",
                        lambda *a, **k: seen.append("universal") or {"status": "spy"})
    monkeypatch.setattr(practice, "_run_legacy_single_call_turn",
                        lambda *a, **k: seen.append("legacy") or {"status": "spy"})
    return seen


# ---------------------------------------------------------------------------
# 1 & 10) Konfiguracija release gatea STVARNO doseže semantički put
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lesson_id", SEMANTIC_LESSONS)
def test_semantic_lessons_reach_the_universal_path_in_gate_env(
        lesson_id, release_gate_env, route_spy, fake_llm, store):
    practice.run_practice_turn(store, fake_llm, turn_for(lesson_id))
    assert route_spy == ["universal"], (lesson_id, route_spy)


@pytest.mark.parametrize("lesson_id", SEMANTIC_LESSONS)
def test_semantic_lessons_receive_the_compiled_contract(lesson_id):
    context = lesson_context_module.build(6, lesson_id)
    assert context.semantic_contract is not None
    assert context.semantic_contract.blocking
    assert context.semantic_contract.prompt_block().strip()


def test_gate_environment_is_the_one_the_release_gate_uses(release_gate_env):
    """Obje zastavice zajedno — isto što gate zahtijeva prije pokretanja."""
    from matbot import config

    assert practice._universal_pipeline_enabled()
    assert config.practice_difficulty_levels_enabled()


# ---------------------------------------------------------------------------
# 2) K1/K3 lekcija BEZ semantičkog ugovora zadržava zatečeni put
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lesson_id", K1K3_ONLY_LESSONS)
def test_k1k3_lesson_without_semantics_stays_on_legacy(
        lesson_id, release_gate_env, route_spy, fake_llm, store):
    assert k1k3_registry.contract_for(lesson_id) is not None
    assert sem_contracts.contract_for(lesson_id) is None
    practice.run_practice_turn(store, fake_llm, turn_for(lesson_id))
    assert route_spy == ["legacy"], (lesson_id, route_spy)


# ---------------------------------------------------------------------------
# 8 & 9) Nepilot i dokazano ponašanje 6-03-004 ostaju netaknuti
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lesson_id", (DIVISIBILITY, NON_PILOT, "9-05-007"))
def test_non_semantic_lessons_route_exactly_as_before(
        lesson_id, release_gate_env, route_spy, fake_llm, store):
    grade = 9 if lesson_id.startswith("9-") else 6
    practice.run_practice_turn(store, fake_llm, turn_for(lesson_id, grade=grade))
    expected = "legacy" if k1k3_registry.contract_for(lesson_id) is not None else "universal"
    assert route_spy == [expected], (lesson_id, route_spy)


def test_divisibility_lesson_has_no_semantic_contract_and_keeps_its_requirement():
    from matbot import lesson_fidelity

    context = lesson_context_module.build(6, DIVISIBILITY)
    assert context.semantic_contract is None
    assert lesson_fidelity.semantic_task_requirement(context.title) is not None


# ---------------------------------------------------------------------------
# 3) Ciljani nivo težine preživljava promjenu rutiranja
# ---------------------------------------------------------------------------

_VARIANT = [0]


def _publish(fake_llm, store, lesson_id, level=1, evidence=None, **turn_changes):
    _VARIANT[0] += 1
    task = task_for(lesson_id, level=level, evidence=evidence,
                    variant=_VARIANT[0])
    draft = make_tutor_draft(new_task=task)
    if turn_changes.get("difficulty_request") in ("easier", "harder"):
        from tests.conftest import make_difficulty_diagnostics  # noqa: F401
        intent = ("harder_task" if turn_changes["difficulty_request"] == "harder"
                  else "easier_task")
        direction = "higher" if intent == "harder_task" else "lower"
        draft = make_tutor_draft(
            intent=intent, new_task=task,
            difficulty_diagnostics=make_difficulty_diagnostics(direction=direction))
    fake_llm.queue(draft)
    fake_llm.queue(make_reviewer_final(final=draft))
    return practice.run_practice_turn(
        store, fake_llm, turn_for(lesson_id, **turn_changes))


def test_fresh_task_targets_level_one(release_gate_env, fake_llm, store):
    lesson = "6-04-009"
    response = _publish(fake_llm, store, lesson)
    assert response.get("status") == "ready"
    session = store.load(session_id=f"rt-{lesson}", grade=6, lesson_id=lesson,
                         lesson_title="t", oblast_id="6-04", oblast="Razlomci",
                         mode="practice")
    assert session["difficulty_level"] == 1


def test_harder_then_easier_moves_the_committed_level(release_gate_env, fake_llm,
                                                      store):
    lesson = "6-04-009"
    _publish(fake_llm, store, lesson)
    _publish(fake_llm, store, lesson, level=2, evidence=level_two_evidence(),
             difficulty_request="harder", student_message="Daj mi teži zadatak.")
    session = store.load(session_id=f"rt-{lesson}", grade=6, lesson_id=lesson,
                         lesson_title="t", oblast_id="6-04", oblast="Razlomci",
                         mode="practice")
    assert session["difficulty_level"] == 2

    _publish(fake_llm, store, lesson, level=1,
             difficulty_request="easier", student_message="Daj mi lakši zadatak.")
    session = store.load(session_id=f"rt-{lesson}", grade=6, lesson_id=lesson,
                         lesson_title="t", oblast_id="6-04", oblast="Razlomci",
                         mode="practice")
    assert session["difficulty_level"] == 1


def test_same_level_new_task_keeps_the_committed_level(release_gate_env,
                                                       fake_llm, store):
    lesson = "6-04-010"
    _publish(fake_llm, store, lesson)
    _publish(fake_llm, store, lesson, student_message="Daj mi novi zadatak.")
    session = store.load(session_id=f"rt-{lesson}", grade=6, lesson_id=lesson,
                         lesson_title="t", oblast_id="6-04", oblast="Razlomci",
                         mode="practice")
    assert session["difficulty_level"] == 1


# ---------------------------------------------------------------------------
# 4 & 5) Stanje sesije: napreduje na objavi, NETAKNUTO na odbijanju
# ---------------------------------------------------------------------------

def test_valid_publication_progresses_the_session(release_gate_env, fake_llm, store):
    lesson = "6-04-011"
    response = _publish(fake_llm, store, lesson)
    assert response.get("status") == "ready"
    session = store.load(session_id=f"rt-{lesson}", grade=6, lesson_id=lesson,
                         lesson_title="t", oblast_id="6-04", oblast="Razlomci",
                         mode="practice")
    assert session["current_task"]
    assert session["current_options"]
    assert session["correct_option_id"]


def test_rejected_semantic_output_leaves_the_session_untouched(
        release_gate_env, fake_llm, store):
    lesson = "6-04-011"
    wrong = task_for(lesson, text="Izračunaj $\\frac{3}{4} : \\frac{8}{9}$.")
    draft = make_tutor_draft(new_task=wrong)
    fake_llm.queue(draft)
    fake_llm.queue(make_reviewer_final(final=draft))

    response = practice.run_practice_turn(store, fake_llm, turn_for(lesson))

    assert response["answer"] == practice.SAFE_ERROR_MESSAGE
    assert "status" not in response
    session = store.load(session_id=f"rt-{lesson}", grade=6, lesson_id=lesson,
                         lesson_title="t", oblast_id="6-04", oblast="Razlomci",
                         mode="practice")
    assert not session["current_task"]
    assert not session["current_options"]
    assert session["difficulty_level"] == 1


# ---------------------------------------------------------------------------
# 6) Detektor se pokreće na TRI mjesta
# ---------------------------------------------------------------------------

def test_detector_runs_before_reviewer_after_reviewer_and_before_publication(
        release_gate_env, fake_llm, store, monkeypatch):
    lesson = "6-04-009"
    seen = []
    original = sem_detectors.detect

    def spy(contract, text):
        seen.append(getattr(contract, "lesson_id", None))
        return original(contract, text)

    monkeypatch.setattr(sem_detectors, "detect", spy)
    response = _publish(fake_llm, store, lesson)

    assert response.get("status") == "ready"
    # nacrt (prije recenzenta) + konačni paket (poslije recenzenta) + objava
    assert seen.count(lesson) >= 3, seen


# ---------------------------------------------------------------------------
# 7) Granica poziva ostaje netaknuta
# ---------------------------------------------------------------------------

def test_publication_still_costs_exactly_two_calls(release_gate_env, fake_llm, store):
    _publish(fake_llm, store, "6-04-012")
    assert fake_llm.call_count == 2


def test_rejected_turn_still_costs_exactly_two_calls(release_gate_env, fake_llm, store):
    lesson = "6-04-012"
    wrong = task_for(lesson, text="Izračunaj $\\frac{3}{8} \\cdot \\frac{9}{4}$.")
    draft = make_tutor_draft(new_task=wrong)
    fake_llm.queue(draft)
    fake_llm.queue(make_reviewer_final(final=draft))
    practice.run_practice_turn(store, fake_llm, turn_for(lesson))
    assert fake_llm.call_count == 2
