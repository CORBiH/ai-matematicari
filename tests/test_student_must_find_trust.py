"""student_must_find demoted to informational-only (matbot/task_family_validation.py).

Živi nalaz #2: ispravan `verify_ordered_pair` zadatak (konkretan uređeni par,
pitanje da li zadovoljava sistem, opcije su tvrdnje o provjeri) lažno odbijen
jer je model deklarisao student_must_find="ordered_pair" dok je ugovor
dozvoljavao samo "statement". Ista klasa propusta kao raniji task_form nalaz —
rješenje je isto: metapodatak postaje informativan, vidljivi ugovor ostaje
jedini autoritet, task_family i answer_kind (objektivno) ostaju strogi.
"""
import json

from matbot.task_family_validation import (
    CONTRACTS, FamilyContractError, validate_task_family,
)
from matbot.schema import InvalidOutputError
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore
from tests.conftest import FakeLLM, make_options, make_output, make_task


def check(family, question, options, correct_index=0, expected="", declared=None):
    try:
        validate_task_family(family, question=question, option_texts=list(options),
                             correct_option_index=correct_index,
                             expected_answer=expected, declared=declared)
        return None
    except FamilyContractError as e:
        return str(e)


LIVE_PAIR_QUESTION = ("Provjeri da li je uređeni par $(2,1)$ rješenje sistema:\n"
                     "$2x+y=5$\n$x-y=1$\nIzaberi jednu od ponuđenih opcija.")
LIVE_PAIR_OPTIONS = [
    "Par $(2,1)$ zadovoljava obje jednačine.",
    "Par $(2,1)$ zadovoljava samo prvu jednačinu.",
    "Par $(2,1)$ zadovoljava samo drugu jednačinu.",
    "Par $(2,1)$ ne zadovoljava nijednu jednačinu.",
]


# ---------------------------------------------------------------------------
# 1. Exact live case now passes
# ---------------------------------------------------------------------------

def test_verify_ordered_pair_metadata_trust_with_canonical_options_passes():
    declared = {"task_family": "verify_ordered_pair", "student_must_find": "ordered_pair",
               "answer_kind": "option_label", "task_form": "recognition"}
    error = check("verify_ordered_pair", LIVE_PAIR_QUESTION, LIVE_PAIR_OPTIONS,
                  correct_index=0, declared=declared)
    assert error is None, error


# ---------------------------------------------------------------------------
# 2. Same valid task passes under other reasonable student_must_find labels
# ---------------------------------------------------------------------------

import pytest


@pytest.mark.parametrize("smf", [
    "statement", "ordered_pair", "verification_result", "value", "comparison",
])
def test_any_reasonable_student_must_find_is_accepted(smf):
    declared = {"task_family": "verify_ordered_pair", "student_must_find": smf,
               "answer_kind": "option_label"}
    error = check("verify_ordered_pair", LIVE_PAIR_QUESTION, LIVE_PAIR_OPTIONS,
                  correct_index=0, declared=declared)
    assert error is None, f"student_must_find={smf} nije smio odbiti: {error}"


# ---------------------------------------------------------------------------
# 3. Visible solve_system task falsely declared verify_ordered_pair still fails
#    (task_family mismatch, unaffected by this fix)
# ---------------------------------------------------------------------------

def test_solve_system_task_declared_as_verify_ordered_pair_still_fails():
    solve_question = "Riješi sistem: $2x+y=8$ i $x-y=1$. Odredi uređeni par."
    solve_options = ["$(3,2)$", "$(2,3)$", "$(3,-2)$", "$(4,0)$"]
    error = check("verify_ordered_pair", solve_question, solve_options, correct_index=0,
                  declared={"task_family": "verify_ordered_pair"})
    # Task_family samo po sebi se poklapa (isto ime), ali VIDLJIVI ugovor i dalje
    # zahtijeva "pita_za_provjeru" frazu — ovaj zadatak je "riješi", ne "provjeri".
    assert error is not None
    assert "verify_ordered_pair" in error


def test_declared_task_family_mismatch_still_hard_fails():
    error = check("solve_system", LIVE_PAIR_QUESTION, LIVE_PAIR_OPTIONS, correct_index=0,
                  declared={"task_family": "verify_ordered_pair"})
    assert error is not None
    assert "deklarisao drugu porodicu" in error


# ---------------------------------------------------------------------------
# 4. verify_ordered_pair that merely asks to solve from scratch still fails
# ---------------------------------------------------------------------------

def test_verify_ordered_pair_rejects_solve_from_scratch_task():
    bad_question = "Riješi sistem $x+y=5$ i $x-y=-1$."
    bad_options = ["$(2,3)$", "$(3,2)$", "$(1,4)$", "$(4,1)$"]
    error = check("verify_ordered_pair", bad_question, bad_options)
    assert error is not None


# ---------------------------------------------------------------------------
# 5. verify_ordered_pair with no concrete ordered pair still fails
# ---------------------------------------------------------------------------

def test_verify_ordered_pair_rejects_task_without_a_concrete_pair():
    bad_question = "Da li sistem $x+y=5$ i $x-y=-1$ ima rješenje?"
    bad_options = ["Da.", "Ne.", "Beskonačno mnogo.", "Ne može se odrediti."]
    error = check("verify_ordered_pair", bad_question, bad_options)
    assert error is not None


# ---------------------------------------------------------------------------
# 6. Objectively wrong answer_kind still fails
# ---------------------------------------------------------------------------

def test_objectively_wrong_answer_kind_still_fails_for_solve_system():
    """Tačna opcija je uređeni par, deklarisano answer_kind='integer' —
    stvarna kontradikcija, mora pasti."""
    question = "Riješi sistem: $2x+y=8$ i $x-y=1$."
    options = ["$(3,2)$", "$(2,3)$", "$(3,-2)$", "$(4,0)$"]
    error = check("solve_system", question, options, correct_index=0,
                  declared={"answer_kind": "integer"})
    assert error is not None
    assert "suprotnosti sa stvarnim" in error


def test_correct_answer_kind_for_solve_system_passes():
    question = "Riješi sistem: $2x+y=8$ i $x-y=1$."
    options = ["$(3,2)$", "$(2,3)$", "$(3,-2)$", "$(4,0)$"]
    error = check("solve_system", question, options, correct_index=0,
                  declared={"answer_kind": "ordered_pair"})
    assert error is None


# ---------------------------------------------------------------------------
# 7. Canonical server metadata never leaks to the browser
# ---------------------------------------------------------------------------

def test_all_31_families_have_canonical_student_must_find_and_task_form():
    for family_id, contract in CONTRACTS.items():
        assert contract.canonical_student_must_find, f"{family_id} nema canonical_student_must_find"
        assert contract.canonical_task_form, f"{family_id} nema canonical_task_form"


def test_explicit_canonical_values_match_specification():
    expected = {
        "find_expansion_factor": "expansion_factor",
        "find_missing_numerator": "missing_numerator",
        "solve_system": "ordered_pair",
        "verify_ordered_pair": "verification_result",
        "choose_method": "method",
        "determine_number_of_solutions": "number_of_solutions",
        "choose_correct_formula": "formula",
    }
    for family_id, expected_smf in expected.items():
        assert CONTRACTS[family_id].canonical_student_must_find == expected_smf, family_id


def test_canonical_metadata_never_appears_in_practice_response():
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Riješi sistem: $2x+y=8$ i $x-y=1$.", expected="(3,2)",
        options=make_options("$(3,2)$", "$(2,3)$", "$(3,-2)$", "$(4,0)$"),
        task_family="solve_system", student_must_find="ordered_pair", answer_kind="ordered_pair")))
    r = run_practice_turn(store, fake, {
        "session_id": "sess-canon-leak", "grade": 9, "selected_topic": "9-05-003",
        "selected_oblast": "", "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "", "selected_option_id": "", "client_turn_id": "",
    })
    raw = json.dumps(r, ensure_ascii=False)
    for leaked in ("verification_result", "expansion_factor", "student_must_find",
                   "task_form", "canonical", '"answer_kind"'):
        assert leaked not in raw, f"Canonical/internal metadata procurila: {leaked}"
    # Također provjeri next_state/options/last_tutor_task specifično.
    assert "student_must_find" not in json.dumps(r.get("next_state", {}), ensure_ascii=False)
    for opt in r.get("next_state", {}).get("task", {}).get("options", []):
        assert "student_must_find" not in json.dumps(opt, ensure_ascii=False)
    assert "student_must_find" not in r.get("last_tutor_task", "")
    assert "student_must_find" not in r.get("effective_topic", "")


# ---------------------------------------------------------------------------
# 10. Invalid output still causes SAFE_ERROR_MESSAGE, one call, no mutation
# ---------------------------------------------------------------------------

def test_structurally_invalid_task_still_rejected_with_safe_message_and_no_mutation():
    store, fake = SessionStore(), FakeLLM()
    payload = {
        "session_id": "sess-still-rejected", "grade": 9, "selected_topic": "9-05-003",
        "selected_oblast": "", "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "", "selected_option_id": "", "client_turn_id": "",
    }
    # Solve-from-scratch task wrongly assigned to verify_ordered_pair-shaped
    # contract by forcing the family via a monkeypatched selection is overkill;
    # instead directly prove the underlying validator still rejects invalid
    # structure regardless of generous metadata trust.
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Proširi razlomak $\\frac{2}{5}$ tako da nazivnik bude $20$.",
        expected="x", options=make_options("$\\frac{8}{20}$", "$\\frac{2}{20}$",
                                           "$\\frac{6}{20}$", "$\\frac{4}{10}$"),
        task_family="find_expansion_factor", student_must_find="expansion_factor",
        answer_kind="integer", task_form="recognition")))
    before = store.peek("sess-still-rejected")
    r = run_practice_turn(store, fake, payload)
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert fake.call_count == 1
    after = store.peek("sess-still-rejected")
    assert after == before  # oba None — ništa nije spremljeno
