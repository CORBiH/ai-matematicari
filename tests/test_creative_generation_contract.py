"""UGOVOR KREATIVNOG GENERISANJA: mašinske činjenice i težina na maksimumu.

Dva ŽIVA nalaza iz finalne kampanje, oba PROTOKOLARNA (ne arhitekturna):

A. Tutor je mašinske činjenice upisao u prezentacijskom obliku —
   `total='3\\cdot48'`, `fraction='\\tfrac{2}{3}'`. Rješavač ih je ispravno
   odbio, ali bez razlike između greške NOTACIJE i greške MATEMATIKE.

B. Potpuno ispravan `fraction_of_fraction` na nivou 3 pao je s
   `difficulty_not_changed`, jer globalno pravilo „teži zahtjev pomjera nivo
   jedan korak naviše“ na maksimumu nije ispunjivo — nivo po dizajnu ostaje 3.

Ovdje se zaključava JEDNO i DRUGO, a sve validirane kapije ostaju netaknute.
"""
import json

import pytest

from matbot.difficulty_level import MAX_LEVEL
from matbot.mathkernel import wordfacts
from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor import creative_escalation as esc
from matbot.tutor.schema import DifficultyEvidence, SignatureParameter
from tests.conftest import (FakeLLM, make_difficulty_diagnostics,
                            make_reviewer_checks, make_reviewer_final,
                            make_task_payload, make_tutor_draft)

LESSON, GRADE, TITLE = "6-04-015", 6, "Tekstualni zadaci s razlomcima"


def _contract_enum():
    from matbot.semantics import contracts as contracts_module
    return tuple(dict(contracts_module.contract_for(LESSON).parameters)
                 ["creative_problem_types"])


SUPPORTED = _contract_enum()

# Živi turn 4: 48 · 3/4 = 36, pa 36 · 1/2 = 18.
LIVE_TURN4 = {
    "text": ("Amra ima $48$ čokoladica. Prvo pokloni $\\frac{3}{4}$ od svojih "
             "čokoladica, a potom pola od onoga što je poklonila daje drugoj "
             "osobi. Koliko čokoladica je ta druga osoba DOBILA?"),
    "options": ("$18$", "$36$", "$24$", "$12$"),
    "correct_index": 0,
    "expected": "$18$",
    "solution": ("$\\frac{3}{4} \\cdot 48 = 36$, pa je "
                 "$\\frac{1}{2} \\cdot 36 = 18$ čokoladica."),
    "facts": {"type": "fraction_of_fraction", "total": "48",
              "first_fraction": "3/4", "second_fraction": "1/2"},
}


@pytest.fixture
def universal(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def turn(session_id, message):
    return {"session_id": session_id, "grade": GRADE, "selected_topic": LESSON,
            "selected_oblast": "", "student_message": message, "intent": "",
            "difficulty_request": "", "interaction_phase": "",
            "last_tutor_task": "", "interaction_type": "student_question",
            "selected_option_id": "", "client_turn_id": ""}


def published_history(session):
    out = []
    for record in session.get("recent_task_signatures") or []:
        if record.get("lesson_id") != LESSON:
            continue
        out.append(json.loads(record["structured_signature"])
                   .get("operation_or_relation"))
    return out


def draft(task, label, facts=None, options=None, expected=None,
          correct_index=None):
    payload = make_task_payload(
        text=task["text"],
        options=options if options is not None else task["options"],
        correct_option_index=(task["correct_index"] if correct_index is None
                              else correct_index),
        expected=expected if expected is not None else task["expected"],
        solution=task["solution"], difficulty="hard")
    parameters = task["facts"] if facts is None else facts
    payload = payload.model_copy(update={
        "selected_lesson_id": LESSON, "selected_lesson_title": TITLE,
        "target_difficulty_level": 3,
        "difficulty_evidence": DifficultyEvidence(
            reasoning_steps=3, condition_count=2, operation_count=3,
            representation_change_count=0, requires_explanation=False,
            requires_comparison=False, requires_construction=False,
            requires_proof_or_justification=False, combines_concepts=False),
        "task_signature": payload.task_signature.model_copy(update={
            "operation_or_relation": label,
            "normalized_parameters": [SignatureParameter(name=n, value=v)
                                      for n, v in parameters.items()]}),
    })
    return make_tutor_draft(
        intent="harder_task", reply="Evo zadatka.",
        lesson_focus="tekstualni zadaci s razlomcima",
        difficulty_diagnostics=make_difficulty_diagnostics(direction="higher"),
        new_task=payload)


def _decision(target, level=3, reason=esc.REASON_MAX_LEVEL_HARDER):
    return esc.CreativeEscalationDecision(
        reason=reason, target_archetype=target, supported_archetypes=SUPPORTED,
        recent_archetypes=(), level=level)


def warm_up(store, fake, session_id, desired):
    for message in ("Daj mi zadatak.", "Daj mi teži zadatak.",
                    "Daj mi teži zadatak."):
        assert run_practice_turn(store, fake, turn(session_id, message)
                                 )["status"] == "ready"
    assert fake.call_count == 0
    session = store.peek(session_id)
    session["recent_task_signatures"] = [
        {"lesson_id": LESSON,
         "structured_signature": json.dumps({"operation_or_relation": name}),
         "structured_signature_hash": f"seed-{name}"}
        for name in SUPPORTED if name != desired]
    store.save(session)
    recent = esc.recent_archetypes(store.peek(session_id), LESSON,
                                   supported=SUPPORTED)
    target = esc.select_target(SUPPORTED, recent)
    assert target == desired, (target, desired)
    return target


def run_creative(session_id, make_draft, desired, **checks):
    store, fake = SessionStore(), FakeLLM()
    target = warm_up(store, fake, session_id, desired)
    before = published_history(store.peek(session_id))
    tutor_draft = make_draft(target)
    fake.queue(tutor_draft)
    fake.queue(make_reviewer_final(
        decision="approve", final=tutor_draft,
        checks=make_reviewer_checks(independent_answer=LIVE_TURN4["expected"],
                                    **checks)))
    response = run_practice_turn(store, fake, turn(session_id, "Daj mi teži zadatak."))
    session = store.peek(session_id)
    return {"published": LIVE_TURN4["text"] in (session.get("current_task") or ""),
            "response": response, "target": target,
            "history_before": before, "history_after": published_history(session),
            "tutor_calls": len(fake.tutor_calls),
            "reviewer_calls": len(fake.reviewer_calls),
            "level": session.get("difficulty_level")}


APPROVE = {"matches_target_archetype": True,
           "substantially_different_from_recent": True}


# ---------------------------------------------------------------------------
# §8 — EGZAKTAN RJEŠAVAČ NAD KANONSKIM I PREZENTACIJSKIM OBLIKOM
# ---------------------------------------------------------------------------

def test_a_canonical_facts_solve_exactly():
    assert wordfacts.solve_from_parameters(
        "fraction_of_quantity", {"total": "144", "fraction": "2/3"}) == 96


@pytest.mark.parametrize("facts", [
    {"total": "3\\cdot48", "fraction": "2/3"},          # živi turn 6
    {"total": "144", "fraction": "\\tfrac{2}{3}"},      # živi turn 7 (oblik)
    {"total": "144", "fraction": "\\frac{2}{3}"},
    {"total": "$144$", "fraction": "2/3"},
    {"total": "144 olovaka", "fraction": "2/3"},
    {"total": " 144", "fraction": "2/3"},
])
def test_bc_presentation_facts_are_rejected_with_their_own_code(facts):
    package = draft(LIVE_TURN4, "fraction_of_quantity", facts=facts).new_task
    assert esc.facts_failure(_decision("fraction_of_quantity"), package) == \
        esc.FACTS_NOT_CANONICAL


def test_d_zero_denominator_is_rejected():
    package = draft(LIVE_TURN4, "fraction_of_quantity",
                    facts={"total": "144", "fraction": "2/0"}).new_task
    assert esc.facts_failure(_decision("fraction_of_quantity"), package) == \
        esc.FACTS_NOT_CANONICAL


def test_e_fractional_count_of_physical_objects_stays_rejected():
    """Živi turn 7: 95 naljepnica, 7/12 poklonjeno → ostatak 475/12.

    Dostupnost se NE popravlja dopuštanjem djelimičnih naljepnica."""
    with pytest.raises(Exception):
        wordfacts.solve_from_parameters(
            "fraction_remainder", {"total": "95", "fraction": "7/12"})


def test_no_expression_is_ever_evaluated():
    """Server ne evaluira modelove izraze — ni tačne."""
    package = draft(LIVE_TURN4, "fraction_of_quantity",
                    facts={"total": "3\\cdot48", "fraction": "2/3"}).new_task
    # 3·48 = 144 i 2/3 od toga JESTE 96, ali se izraz ne računa.
    assert esc.facts_failure(_decision("fraction_of_quantity"), package) == \
        esc.FACTS_NOT_CANONICAL


# ---------------------------------------------------------------------------
# §6 — UGOVOR JE U PROMPTU, I TO SAMO U KREATIVNOM BLOKU
# ---------------------------------------------------------------------------

def test_facts_contract_is_stated_in_the_creative_block():
    block = esc.prompt_block(_decision("fraction_of_fraction"))
    assert "MAŠINSKI PODACI" in block
    assert "\\tfrac{2}{3}" in block and "3\\cdot48" in block
    assert "cio broj" in block and "p/q" in block
    # Proza zadatka SMIJE ostati LaTeX — ograničenje je usko.
    assert "SAMO za ove činjenice" in block


def test_facts_contract_is_absent_without_escalation():
    assert esc.prompt_block(None) == ""


# ---------------------------------------------------------------------------
# §10–§12 — TEŽINA NA MAKSIMUMU
# ---------------------------------------------------------------------------

def test_max_level_block_disarms_only_the_level_increase_rule():
    block = esc.prompt_block(_decision("fraction_of_fraction", level=MAX_LEVEL))
    assert "difficulty_not_changed" in block
    assert "SE NE PRIMJENJUJE" in block
    # …ali sve ostalo ostaje provjereno.
    assert f"nivou {MAX_LEVEL}" in block
    assert "bez gradiva izvan lekcije" in block
    assert "bez izmišljenog nivoa" in block


@pytest.mark.parametrize("level", (1, 2))
def test_explicit_variety_below_max_gets_no_exception(level):
    """§12: izuzetak maksimuma ne smije procuriti na niže nivoe."""
    block = esc.prompt_block(_decision(
        "fraction_of_quantity", level=level,
        reason=esc.REASON_EXPLICIT_VARIETY))
    assert "TEŽINA NA MAKSIMUMU" not in block
    assert "difficulty_not_changed" not in block


def test_harder_at_max_is_the_only_trigger():
    variety_at_max = esc.prompt_block(_decision(
        "fraction_of_quantity", level=MAX_LEVEL,
        reason=esc.REASON_EXPLICIT_VARIETY))
    assert "TEŽINA NA MAKSIMUMU" not in variety_at_max


# ---------------------------------------------------------------------------
# §13 — ŽIVI TURN 4 MORA OBJAVITI, NA NIVOU 3
# ---------------------------------------------------------------------------

def test_live_turn4_package_publishes_at_level_three(universal):
    result = run_creative("contract-13", lambda t: draft(LIVE_TURN4, t),
                          "fraction_of_fraction", **APPROVE)
    assert result["published"] is True
    assert result["response"]["status"] == "ready"
    assert result["level"] == MAX_LEVEL          # nikad nivo 4
    assert result["history_after"][-1] == "fraction_of_fraction"
    assert result["tutor_calls"] == 1 and result["reviewer_calls"] == 1


# ---------------------------------------------------------------------------
# §14 — KREATIVNI MAKSIMUM NIJE DOZVOLA ZA PREKORAČENJE
# ---------------------------------------------------------------------------

def test_reviewer_can_still_reject_an_out_of_scope_creative_task(universal):
    """Ispravke semantike prelaza NISU gašenje provjere težine."""
    result = run_creative("contract-14", lambda t: draft(LIVE_TURN4, t),
                          "fraction_of_fraction",
                          matches_target_archetype=False,
                          substantially_different_from_recent=True)
    assert result["published"] is False
    assert result["history_after"] == result["history_before"]
    assert result["tutor_calls"] == 1 and result["reviewer_calls"] == 1


def test_difficulty_evidence_validation_is_not_disabled_anywhere():
    """Dokaz težine i dalje mora zadovoljiti nivo — globalno, ne samo ovdje."""
    from matbot.tutor import schema as tutor_schema
    level_one_evidence = DifficultyEvidence(
        reasoning_steps=3, condition_count=3, operation_count=3,
        representation_change_count=2, requires_explanation=False,
        requires_comparison=False, requires_construction=True,
        requires_proof_or_justification=False, combines_concepts=False)
    assert tutor_schema.difficulty_evidence_errors(level_one_evidence, 1)


# ---------------------------------------------------------------------------
# §15 — KOMBINOVANA MATRICA
# ---------------------------------------------------------------------------

def test_case_2_latex_facts_reject_after_tutor(universal):
    result = run_creative(
        "contract-c2",
        lambda t: draft(LIVE_TURN4, t, facts={
            "type": "fraction_of_fraction", "total": "48",
            "first_fraction": "\\tfrac{3}{4}", "second_fraction": "1/2"}),
        "fraction_of_fraction", **APPROVE)
    assert result["published"] is False
    assert result["tutor_calls"] == 1 and result["reviewer_calls"] == 0
    assert result["history_after"] == result["history_before"]


def test_case_3_unevaluated_total_rejects_after_tutor(universal):
    result = run_creative(
        "contract-c3",
        lambda t: draft(LIVE_TURN4, t, facts={
            "type": "fraction_of_fraction", "total": "3\\cdot16",
            "first_fraction": "3/4", "second_fraction": "1/2"}),
        "fraction_of_fraction", **APPROVE)
    assert result["published"] is False
    assert result["tutor_calls"] == 1 and result["reviewer_calls"] == 0
