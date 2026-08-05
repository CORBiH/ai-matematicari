"""Nivo težine se commituje TEK kad je zadatak stvarno objavljen.

ŽIVI NALAZI:

  A06 (ugovorna lekcija, sve tri kampanje) — istražen i UTVRĐEN KAO ISPRAVAN.
      Vidi test na dnu: nivo prati napredovanje učenika, a ne mjerljivost
      determinističkog motora. Suprotno bi učenika trajno zaključalo na nivou 1.

  A11 / B50 (universal put) — „teže“ je odbijeno zbog dokaza težine; nivo je
      ispravno ostao 1. Ovi testovi to zaključavaju da kalibracija ne oslabi
      invarijantu.

  B51 — timeout na trećem koraku; sesija je ostala bajt za bajt ista.

Invarijanta koju ovi testovi zaključavaju: nijedan NEUSPJEH (recenzent,
duplikat, timeout, nevalidan paket) ne smije pomjeriti `difficulty_level`.
Provjereno je da univerzalni put to već drži po konstrukciji — `_publish_task`
je jedino mjesto koje piše nivo i pokreće se samo prije uspješnog `store.save`.
"""
import copy

import pytest

from matbot.llm import LLMTimeout
from matbot.tutor import pipeline as tutor_pipeline
from tests.conftest import (make_reviewer_final, make_task_payload, make_tutor_draft,
                            queue_two_call)

LESSON = "6-03-004"
SESSION = "prog-1"


@pytest.fixture(autouse=True)
def _levels_on(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def _turn(message="Daj mi zadatak.", difficulty="", client_turn_id="prog-t"):
    return {
        "session_id": SESSION, "grade": 6, "selected_topic": LESSON,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": difficulty, "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": client_turn_id,
    }


def _evidence(level):
    from matbot.tutor.schema import DifficultyEvidence
    if level == 1:
        return DifficultyEvidence(reasoning_steps=1, condition_count=1, operation_count=1,
                                  representation_change_count=0, requires_explanation=False,
                                  requires_comparison=False, requires_construction=False,
                                  requires_proof_or_justification=False, combines_concepts=False)
    if level == 2:
        return DifficultyEvidence(reasoning_steps=2, condition_count=2, operation_count=2,
                                  representation_change_count=0, requires_explanation=False,
                                  requires_comparison=False, requires_construction=False,
                                  requires_proof_or_justification=False, combines_concepts=False)
    return DifficultyEvidence(reasoning_steps=3, condition_count=3, operation_count=3,
                              representation_change_count=1, requires_explanation=False,
                              requires_comparison=False, requires_construction=False,
                              requires_proof_or_justification=True, combines_concepts=True)


_COUNTER = {"n": 0}


def _task(level, marker=None):
    """Svaki zadatak ima drugačiji potpis — duplikat se testira eksplicitno."""
    _COUNTER["n"] += 1
    tag = marker if marker is not None else _COUNTER["n"]
    from matbot.tutor.schema import SignatureParameter
    # Namjerno NIJE zadatak o djeljivosti: `mcq_integrity` za tu porodicu ima
    # stvaran oracle, pa bi fixture mjerio njega umjesto stanja nivoa.
    task = make_task_payload(text=f"Izračunaj $ {tag} + 1 $.",
                             options=(f"${tag + 1}$", f"${tag + 2}$", f"${tag + 3}$",
                                      f"${tag + 4}$"),
                             correct_option_index=0, expected=f"${tag + 1}$")
    return task.model_copy(update={
        "target_difficulty_level": level,
        "difficulty_evidence": _evidence(level),
        "task_signature": task.task_signature.model_copy(update={
            "normalized_parameters": [SignatureParameter(name="n", value=str(tag))]}),
    })


def _intent_for(direction):
    return {"harder": "harder_task", "easier": "easier_task", "": "generate_task"}[direction]


def _step(store, fake_llm, direction, level, marker=None, client_turn_id="prog-t"):
    intent = _intent_for(direction)
    kwargs = {}
    if intent in ("harder_task", "easier_task"):
        from tests.conftest import make_difficulty_diagnostics
        kwargs["difficulty_diagnostics"] = make_difficulty_diagnostics(
            "higher" if intent == "harder_task" else "lower")
    draft = make_tutor_draft(intent=intent, new_task=_task(level, marker), **kwargs)
    queue_two_call(fake_llm, draft=draft,
                   reviewer=make_reviewer_final(decision="approve", final=draft,
                                                reviewed_difficulty_evidence=_evidence(level)))
    message = {"harder": "Daj mi teži zadatak.", "easier": "Daj mi lakši zadatak.",
               "": "Daj mi zadatak."}[direction]
    return tutor_pipeline.run_turn(store, fake_llm,
                                   _turn(message, direction, client_turn_id))


def _level(store):
    session = store.peek(SESSION)
    return session["difficulty_level"] if session else None


# ---------------------------------------------------------------------------
# 1. PUNA MAŠINA STANJA 1 → 2 → 3 → 3 → 2 → 1 → 1
# ---------------------------------------------------------------------------

def test_full_level_ladder_up_and_down(store, fake_llm):
    assert _step(store, fake_llm, "", 1)["status"] == "ready"
    assert _level(store) == 1
    assert _step(store, fake_llm, "harder", 2)["status"] == "ready"
    assert _level(store) == 2
    assert _step(store, fake_llm, "harder", 3)["status"] == "ready"
    assert _level(store) == 3
    # Na vrhu ljestvice nivo ostaje 3.
    assert _step(store, fake_llm, "harder", 3)["status"] == "ready"
    assert _level(store) == 3
    assert _step(store, fake_llm, "easier", 2)["status"] == "ready"
    assert _level(store) == 2
    assert _step(store, fake_llm, "easier", 1)["status"] == "ready"
    assert _level(store) == 1
    # Na dnu ljestvice nivo ostaje 1.
    assert _step(store, fake_llm, "easier", 1)["status"] == "ready"
    assert _level(store) == 1


def test_committed_level_never_leaves_the_one_to_three_range(store, fake_llm):
    _step(store, fake_llm, "", 1)
    for _ in range(4):
        _step(store, fake_llm, "harder", 3)
        assert 1 <= _level(store) <= 3
    for _ in range(4):
        _step(store, fake_llm, "easier", 1)
        assert 1 <= _level(store) <= 3


# ---------------------------------------------------------------------------
# 2. NIJEDAN NEUSPJEH NE MIJENJA NIVO
# ---------------------------------------------------------------------------

def _snapshot(store):
    return copy.deepcopy(store.peek(SESSION))


def test_reviewer_fail_closed_leaves_the_level_untouched(store, fake_llm):
    _step(store, fake_llm, "", 1)
    before = _snapshot(store)
    draft = make_tutor_draft(intent="harder_task", new_task=_task(2),
                             difficulty_diagnostics=__import__(
                                 "tests.conftest", fromlist=["x"]
                             ).make_difficulty_diagnostics("higher"))
    queue_two_call(fake_llm, draft=draft,
                   reviewer=make_reviewer_final(decision="fail_closed", final=None,
                                                fail_reason_code="difficulty_not_changed",
                                                reviewed_difficulty_evidence=None))
    response = tutor_pipeline.run_turn(store, fake_llm, _turn("Daj mi teži zadatak.", "harder"))

    assert response.get("status") is None
    assert store.peek(SESSION) == before


def test_timeout_leaves_the_level_untouched(store, fake_llm):
    """B51 — timeout na Tutoru; sesija mora ostati bajt za bajt ista."""
    _step(store, fake_llm, "", 1)
    before = _snapshot(store)
    fake_llm.queue(LLMTimeout("APITimeoutError"))
    response = tutor_pipeline.run_turn(store, fake_llm, _turn("Daj mi teži zadatak.", "harder"))

    assert response.get("status") is None
    assert store.peek(SESSION) == before


def test_invalid_package_leaves_the_level_untouched(store, fake_llm):
    _step(store, fake_llm, "", 1)
    before = _snapshot(store)
    broken = _task(2).model_copy(update={"expected_answer": "nešto sasvim drugo"})
    draft = make_tutor_draft(intent="harder_task", new_task=broken,
                             difficulty_diagnostics=__import__(
                                 "tests.conftest", fromlist=["x"]
                             ).make_difficulty_diagnostics("higher"))
    queue_two_call(fake_llm, draft=draft,
                   reviewer=make_reviewer_final(decision="approve", final=draft,
                                                reviewed_difficulty_evidence=_evidence(2)))
    response = tutor_pipeline.run_turn(store, fake_llm, _turn("Daj mi teži zadatak.", "harder"))

    assert response.get("status") is None
    assert store.peek(SESSION) == before


def test_duplicate_task_rejection_leaves_the_level_untouched(store, fake_llm):
    _step(store, fake_llm, "", 1, marker=100)
    before = _snapshot(store)
    # Isti strukturni potpis kao prethodni zadatak → duplikat.
    _step(store, fake_llm, "harder", 2, marker=100)

    assert store.peek(SESSION) == before


def test_repeated_identical_request_does_not_corrupt_the_level(store, fake_llm):
    _step(store, fake_llm, "", 1)
    for index in range(3):
        _step(store, fake_llm, "harder", 2, client_turn_id=f"prog-r{index}")
        assert _level(store) in (1, 2)
    assert 1 <= _level(store) <= 3


# ---------------------------------------------------------------------------
# 3. UGOVORNI PUT — nivo prati NAPREDOVANJE UČENIKA, ne mjerljivost motora
# ---------------------------------------------------------------------------

def test_contract_path_commits_the_requested_level_even_when_generation_cannot_change():
    """A06 NIJE korupcija stanja — provjereno, i namjerno je ovako.

    Na ugovornoj lekciji deterministički motor ponekad NE MOŽE promijeniti
    mjerljiv profil između dva nivoa (za `6-04-009` profili nivoa 1 i 2 su
    identični, a nivoa 3 nisu). Server tada ISTINITO napiše „Evo još jednog
    zadatka slične težine“, ali svejedno commituje traženi nivo — inače bi
    učenik ostao TRAJNO zaključan na nivou 1 i nikad ne bi stigao do nivoa 3,
    gdje motor jeste sposoban napraviti teži zadatak.

    Ovaj test zaključava tu odluku da je buduća izmjena ne poništi slučajno."""
    from matbot import practice
    from matbot.contracts import difficulty as contract_difficulty
    from matbot.contracts import registry
    from matbot.session_store import SessionStore
    from tests.conftest import make_options, make_task

    contract = registry.contract_for("6-04-009")
    assert (contract_difficulty.measurable_target_profile(contract, 1)
            == contract_difficulty.measurable_target_profile(contract, 2))
    assert (contract_difficulty.measurable_target_profile(contract, 2)
            != contract_difficulty.measurable_target_profile(contract, 3))

    store = SessionStore()
    session = store.load(session_id="c-1", grade=6, lesson_id="6-04-009",
                         lesson_title="Sabiranje i oduzimanje razlomaka jednakih imenilaca",
                         oblast_id="6-04", oblast="Razlomci", mode="practice")
    session["difficulty_level"] = 1
    practice._apply_new_task(
        session, make_task(text="Izračunaj: $\frac{9}{10} - \frac{2}{10}$.",
                           expected="$\frac{7}{10}$",
                           options=make_options("$\frac{7}{10}$", "$\frac{8}{10}$",
                                                "$\frac{11}{10}$", "$\frac{7}{20}$")),
        task_family="fraction_operation", request_id="r1", target_difficulty_level=2)

    assert session["difficulty_level"] == 2


def test_truthful_intro_is_what_signals_an_unchanged_generation():
    """Oznaka koju učenik vidi, a ne stanje, nosi istinu o mjerljivoj promjeni."""
    from matbot import difficulty_level, practice

    transition = difficulty_level.transition(1, "harder")
    unchanged = practice._new_task_intro({"difficulty_request": "harder"},
                                         transition=transition, generation_changed=False)
    changed = practice._new_task_intro({"difficulty_request": "harder"},
                                       transition=transition, generation_changed=True)
    assert unchanged == practice._SAME_SUPPORTED_DIFFICULTY_INTRO
    assert changed == practice._HARDER_TASK_INTRO
