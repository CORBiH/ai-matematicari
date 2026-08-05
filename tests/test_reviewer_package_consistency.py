"""Recenzentova odluka mora pratiti njegov vlastiti paket i vlastite provjere.

ŽIVI NALAZ (postIncompleteFix, 100 scenarija, universal_two_call) — recenzent je
9 puta vratio `approve`/`correct` iako je sam prijavio problem:

    6x  „odobreno uprkos oborenim provjerama“
        A01 ['marked_option_correct','task_package_consistent',…]
        A26 A39 A40 B04 B31 (task_package_consistent, task_signature_consistent,
        mathjax_valid)
    3x  „reviewer_final_mcq_integrity_rejection“ nakon `correct`
        A22 B19 B51 — ispravka je vraćena s ISTIM dokazanim defektom

Server je u svih 9 slučajeva ISPRAVNO pao zatvoreno i nije ništa objavio, ali je
učenik dobio tehničku poruku umjesto zadatka.

Ovi testovi ZAKLJUČAVAJU postojeće invarijante (da ih kasnija kalibracija težine
ne oslabi) i provjeravaju da recenzentov prompt izričito nosi pravilo odluke.
Nijedna invarijanta se ovdje ne popušta — cilj je da recenzent u svom JEDINOM
pozivu češće vrati stvarno ispravljen paket.
"""
import pytest

from matbot.tutor import prompts as tutor_prompts
from matbot.tutor import pipeline as tutor_pipeline
from matbot.tutor.schema import UnifiedOutputError, validate_reviewer
from tests.conftest import (make_reviewer_checks, make_reviewer_final, make_task_payload,
                            make_tutor_draft, queue_two_call)

LESSON = "9-04-003"
MANDATORY = ("math_correct", "marked_option_correct", "inside_lesson", "intent_handled",
             "task_solvable_and_unambiguous", "mathjax_valid", "language_age_appropriate",
             "response_addresses_student", "task_package_consistent",
             "difficulty_evidence_valid", "task_signature_consistent")


@pytest.fixture(autouse=True)
def _levels_on(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def _turn(message="Daj mi zadatak.", session_id="rev-1", client_turn_id="rev-t1"):
    return {
        "session_id": session_id, "grade": 9, "selected_topic": LESSON,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": client_turn_id,
    }


def _task(text="Riješi jednačinu: $3x=12$", options=None, expected="$x=4$"):
    return make_task_payload(
        text=text, options=options or ("$x=4$", "$x=2$", "$x=6$", "$x=3$"),
        correct_option_index=0, expected=expected)


# ---------------------------------------------------------------------------
# 1. ODLUKA NASPRAM VLASTITIH PROVJERA
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("failing", MANDATORY)
def test_approve_is_refused_when_any_mandatory_check_is_false(failing):
    """A01/A26/A39/A40/B04/B31 — nijedna obavezna provjera ne smije biti false."""
    draft = make_tutor_draft(intent="generate_task", new_task=_task())
    reviewer = make_reviewer_final(decision="approve", final=draft,
                                   checks=make_reviewer_checks(**{failing: False}))
    with pytest.raises(UnifiedOutputError) as error:
        validate_reviewer(reviewer)
    assert failing in str(error.value)


@pytest.mark.parametrize("decision", ["approve", "correct"])
def test_a_decision_without_a_final_payload_is_refused(decision):
    with pytest.raises(UnifiedOutputError):
        validate_reviewer(make_reviewer_final(decision=decision, final=None,
                                              reviewed_difficulty_evidence=None))


def test_fail_closed_requires_a_reason_code():
    with pytest.raises(UnifiedOutputError):
        validate_reviewer(make_reviewer_final(decision="fail_closed", fail_reason_code=None))
    validate_reviewer(make_reviewer_final(decision="fail_closed",
                                          fail_reason_code="unsafe_or_unverifiable"))


def test_approved_task_requires_independent_reviewer_evidence():
    draft = make_tutor_draft(intent="generate_task", new_task=_task())
    with pytest.raises(UnifiedOutputError):
        validate_reviewer(make_reviewer_final(decision="approve", final=draft,
                                              reviewed_difficulty_evidence=None))


def test_task_approval_requires_the_reviewer_to_have_solved_it_itself():
    draft = make_tutor_draft(intent="generate_task", new_task=_task())
    with pytest.raises(UnifiedOutputError):
        validate_reviewer(make_reviewer_final(
            decision="approve", final=draft,
            checks=make_reviewer_checks(independently_solved=False)))
    with pytest.raises(UnifiedOutputError):
        validate_reviewer(make_reviewer_final(
            decision="approve", final=draft,
            checks=make_reviewer_checks(independent_answer="   ")))


def test_a_valid_approve_still_passes():
    draft = make_tutor_draft(intent="generate_task", new_task=_task())
    validate_reviewer(make_reviewer_final(decision="approve", final=draft))


# ---------------------------------------------------------------------------
# 2. KOREKCIJA KOJA NIJE STVARNA KOREKCIJA
# ---------------------------------------------------------------------------

def test_correction_that_keeps_a_proven_package_defect_is_never_published(store, fake_llm):
    """A22/B19/B51 — `correct` je vratio paket s ISTIM dokazanim defektom."""
    broken = _task(options=("$x=4$", "$x=4$", "$x=6$", "$x=3$"))   # dvije iste opcije
    draft = make_tutor_draft(intent="generate_task", new_task=broken)
    queue_two_call(fake_llm, draft=draft,
                   reviewer=make_reviewer_final(decision="correct", final=draft))
    response = tutor_pipeline.run_turn(store, fake_llm, _turn())

    assert response.get("status") is None
    assert response["answer"] == tutor_pipeline.SAFE_ERROR_MESSAGE
    assert fake_llm.call_count == 2                       # nikad treći poziv
    assert store.peek("rev-1") is None                    # bez mutacije sesije


def test_a_real_correction_is_published(store, fake_llm):
    broken = _task(options=("$x=4$", "$x=4$", "$x=6$", "$x=3$"))
    repaired = make_tutor_draft(intent="generate_task", new_task=_task())
    queue_two_call(fake_llm, draft=make_tutor_draft(intent="generate_task", new_task=broken),
                   reviewer=make_reviewer_final(decision="correct", final=repaired))
    response = tutor_pipeline.run_turn(store, fake_llm, _turn())

    assert response["status"] == "ready"
    assert "3x=12" in response["answer"]
    assert fake_llm.call_count == 2


def test_fail_closed_never_mutates_the_session(store, fake_llm):
    draft = make_tutor_draft(intent="generate_task", new_task=_task())
    queue_two_call(fake_llm, draft=draft,
                   reviewer=make_reviewer_final(decision="fail_closed", final=None,
                                                fail_reason_code="math_incorrect",
                                                reviewed_difficulty_evidence=None))
    response = tutor_pipeline.run_turn(store, fake_llm, _turn())
    assert response.get("status") is None
    assert store.peek("rev-1") is None
    assert fake_llm.call_count == 2


# ---------------------------------------------------------------------------
# 3. PROMPT MORA NOSITI PRAVILO ODLUKE
# ---------------------------------------------------------------------------

def _reviewer_instructions():
    from matbot.tutor import lesson_context as lesson_context_module
    return tutor_prompts.build_reviewer_instructions(
        lesson_context_module.build(9, LESSON))


def test_reviewer_prompt_states_that_approve_needs_every_check_true():
    text = _reviewer_instructions()
    assert "DECISION CONSISTENCY RULE" in text
    assert "every mandatory check" in text


def test_reviewer_prompt_requires_recomputing_the_whole_package_after_a_correction():
    text = _reviewer_instructions()
    for field in ("correct option", "expected answer", "solution", "signature",
                  "difficulty evidence"):
        assert field in text


def test_reviewer_prompt_offers_fail_closed_as_the_safe_way_out():
    text = _reviewer_instructions()
    assert "fail_closed" in text
    assert "one call" in text
