"""Faza 4A — integracija porodice `fraction_arithmetic_direct` u univerzalni put.

Dokazuje CIJELI lanac, bez ijednog stvarnog modela poziva:

    ugovor iz podataka → kompaktan Tutor kontekst → deterministički preflight
    nalaz → recenzentova ispravka → ISTA završna validacija → objava samo bez
    blokirajućih nalaza

Ključne invarijante: najviše dva poziva, nema mutacije sesije pri odbijanju,
nepilot lekcije se ponašaju bajt za bajt kao prije, a dokazano blokiranje
lekcije o pravilima djeljivosti ostaje netaknuto.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn  # noqa: E402
from matbot.semantics import contracts as sem_contracts  # noqa: E402
from matbot.tutor import lesson_context as lesson_context_module  # noqa: E402
from matbot.tutor import package_preflight, prompts as tutor_prompts  # noqa: E402
from tests.conftest import (make_reviewer_final, make_task_payload,  # noqa: E402
                            make_tutor_draft)

PILOT_LESSON = "6-04-009"          # sabiranje/oduzimanje JEDNAKIH imenilaca
PILOT_GRADE = 6
NON_PILOT_LESSON = "6-04-015"      # i dalje BEZ semantičkog ugovora (Batch #2)
DIVISIBILITY_LESSON = "6-03-004"   # dokazano blokiranje koje mora ostati isto

VALID_TEXT = "Izračunaj $\\frac{2}{7} + \\frac{3}{7}$."
WRONG_OPERATION_TEXT = "Izračunaj $\\frac{2}{7} \\cdot \\frac{3}{7}$."
VALID_OPTIONS = ("$\\frac{5}{7}$", "$\\frac{5}{14}$", "$\\frac{6}{7}$", "$\\frac{1}{7}$")
PRODUCT_OPTIONS = ("$\\frac{6}{49}$", "$\\frac{5}{7}$", "$\\frac{6}{7}$", "$\\frac{1}{7}$")


def turn_for(topic_id, grade=PILOT_GRADE, **changes):
    payload = {
        "session_id": f"sem-{topic_id}", "grade": grade,
        "selected_topic": topic_id, "selected_oblast": "nepouzdano",
        "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    payload.update(changes)
    return payload


def task_for(lesson_id, text, options=VALID_OPTIONS, correct_index=0):
    """TaskPayload s ISPRAVNIM identitetom lekcije (pipeline ga provjerava)."""
    context = lesson_context_module.build(PILOT_GRADE, lesson_id)
    payload = make_task_payload(text=text, options=list(options),
                                correct_option_index=correct_index,
                                expected=options[correct_index])
    return payload.model_copy(update={
        "selected_lesson_id": context.topic_id,
        "selected_lesson_title": context.title,
    })


# ---------------------------------------------------------------------------
# 1) UGOVOR STIŽE U OBA POZIVA — i to ISTI TEKST
# ---------------------------------------------------------------------------

def test_lesson_context_exposes_the_semantic_contract():
    context = lesson_context_module.build(PILOT_GRADE, PILOT_LESSON)
    assert context.semantic_contract is not None
    assert context.semantic_contract.lesson_id == PILOT_LESSON


def test_tutor_and_reviewer_receive_the_identical_contract_block():
    context = lesson_context_module.build(PILOT_GRADE, PILOT_LESSON)
    block = context.semantic_contract.prompt_block()
    assert block.strip()
    tutor = tutor_prompts.build_tutor_instructions(context)
    reviewer = tutor_prompts.build_reviewer_instructions(context)
    assert block in tutor
    assert block in reviewer


def test_non_pilot_lesson_prompt_has_no_contract_block():
    context = lesson_context_module.build(PILOT_GRADE, NON_PILOT_LESSON)
    assert context.semantic_contract is None
    tutor = tutor_prompts.build_tutor_instructions(context)
    assert "SEMANTIČKI UGOVOR LEKCIJE" not in tutor


def test_non_pilot_prompt_is_byte_identical_to_the_contractless_build():
    """Nepilot lekcija ne smije osjetiti Fazu 4A ni u jednom bajtu."""
    context = lesson_context_module.build(PILOT_GRADE, NON_PILOT_LESSON)
    stripped = context.__class__(**{
        **{field: getattr(context, field) for field in context.__dataclass_fields__},
        "semantic_contract": None,
    })
    assert (tutor_prompts.build_tutor_instructions(context)
            == tutor_prompts.build_tutor_instructions(stripped))
    assert (tutor_prompts.build_reviewer_instructions(context)
            == tutor_prompts.build_reviewer_instructions(stripped))


# ---------------------------------------------------------------------------
# 2) PREFLIGHT NALAZ — deterministički, prije drugog poziva
# ---------------------------------------------------------------------------

def test_preflight_reports_the_semantic_issue_for_a_wrong_operation():
    contract = sem_contracts.contract_for(PILOT_LESSON)
    task = task_for(PILOT_LESSON, WRONG_OPERATION_TEXT, PRODUCT_OPTIONS)
    issues = package_preflight.collect_package_issues(task, contract=contract)
    codes = [issue.code for issue in issues]
    assert "semantic_operation_mismatch" in codes


def test_preflight_without_contract_is_unchanged():
    task = task_for(PILOT_LESSON, WRONG_OPERATION_TEXT, PRODUCT_OPTIONS)
    issues = package_preflight.collect_package_issues(task)
    assert not [i for i in issues if i.code.startswith("semantic_")]


def test_valid_task_produces_no_semantic_issue():
    contract = sem_contracts.contract_for(PILOT_LESSON)
    task = task_for(PILOT_LESSON, VALID_TEXT)
    issues = package_preflight.collect_package_issues(task, contract=contract)
    assert not [i for i in issues if i.code.startswith("semantic_")]


def test_advisory_contract_never_blocks(monkeypatch):
    contract = sem_contracts.contract_for(PILOT_LESSON)
    advisory = contract.__class__(**{
        **{f: getattr(contract, f) for f in contract.__dataclass_fields__},
        "enforcement_mode": "advisory",
    })
    task = task_for(PILOT_LESSON, WRONG_OPERATION_TEXT, PRODUCT_OPTIONS)
    issues = package_preflight.collect_package_issues(task, contract=advisory)
    assert not [i for i in issues if i.code.startswith("semantic_")]


# ---------------------------------------------------------------------------
# 3) CIJELI TURN: nalaz → recenzent → objava / odbijanje
# ---------------------------------------------------------------------------

def test_reviewer_input_carries_the_semantic_finding(fake_llm, store):
    draft = make_tutor_draft(
        new_task=task_for(PILOT_LESSON, WRONG_OPERATION_TEXT, PRODUCT_OPTIONS))
    corrected = make_tutor_draft(new_task=task_for(PILOT_LESSON, VALID_TEXT))
    fake_llm.queue(draft)
    fake_llm.queue(make_reviewer_final(decision="correct", final=corrected))

    run_practice_turn(store, fake_llm, turn_for(PILOT_LESSON))

    _instructions, reviewer_input = fake_llm.calls[1]
    assert "semantic_operation_mismatch" in reviewer_input


def test_valid_reviewer_correction_publishes(fake_llm, store):
    draft = make_tutor_draft(
        new_task=task_for(PILOT_LESSON, WRONG_OPERATION_TEXT, PRODUCT_OPTIONS))
    corrected = make_tutor_draft(new_task=task_for(PILOT_LESSON, VALID_TEXT))
    fake_llm.queue(draft)
    fake_llm.queue(make_reviewer_final(decision="correct", final=corrected))

    response = run_practice_turn(store, fake_llm, turn_for(PILOT_LESSON))

    assert response.get("status") == "ready"
    assert "\\frac{2}{7} + \\frac{3}{7}" in response["last_tutor_task"]
    assert fake_llm.call_count == 2


def test_correction_that_keeps_the_defect_is_rejected_without_state_change(
        fake_llm, store):
    """Promjena teksta bez promjene MATEMATIKE nije ispravka."""
    draft = make_tutor_draft(
        new_task=task_for(PILOT_LESSON, WRONG_OPERATION_TEXT, PRODUCT_OPTIONS))
    still_wrong = make_tutor_draft(new_task=task_for(
        PILOT_LESSON, "Izračunaj proizvod $\\frac{2}{7} \\cdot \\frac{3}{7}$.",
        PRODUCT_OPTIONS))
    fake_llm.queue(draft)
    fake_llm.queue(make_reviewer_final(decision="correct", final=still_wrong))

    session_before = dict(store.load(
        session_id="sem-6-04-009", grade=PILOT_GRADE, lesson_id=PILOT_LESSON,
        lesson_title="x", oblast_id="6-04", oblast="Razlomci", mode="practice"))
    response = run_practice_turn(store, fake_llm, turn_for(PILOT_LESSON))

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in response
    assert fake_llm.call_count == 2
    session_after = store.load(
        session_id="sem-6-04-009", grade=PILOT_GRADE, lesson_id=PILOT_LESSON,
        lesson_title="x", oblast_id="6-04", oblast="Razlomci", mode="practice")
    assert not session_after["current_task"]
    assert session_after["current_task"] == session_before["current_task"]


def test_reviewer_approval_cannot_override_the_deterministic_finding(fake_llm, store):
    """`approve` nad dokazanim prekršajem se ne objavljuje."""
    bad = task_for(PILOT_LESSON, WRONG_OPERATION_TEXT, PRODUCT_OPTIONS)
    draft = make_tutor_draft(new_task=bad)
    fake_llm.queue(draft)
    fake_llm.queue(make_reviewer_final(decision="approve", final=draft))

    response = run_practice_turn(store, fake_llm, turn_for(PILOT_LESSON))

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert fake_llm.call_count == 2


def test_wrong_denominator_relation_is_blocked_for_the_equal_lesson(fake_llm, store):
    unlike = task_for(PILOT_LESSON, "Izračunaj $\\frac{1}{2} + \\frac{1}{3}$.",
                      ("$\\frac{5}{6}$", "$\\frac{2}{5}$", "$\\frac{1}{6}$",
                       "$\\frac{2}{6}$"))
    draft = make_tutor_draft(new_task=unlike)
    fake_llm.queue(draft)
    fake_llm.queue(make_reviewer_final(decision="approve", final=draft))

    response = run_practice_turn(store, fake_llm, turn_for(PILOT_LESSON))

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert fake_llm.call_count == 2


def test_turn_never_makes_a_third_call(fake_llm, store):
    draft = make_tutor_draft(
        new_task=task_for(PILOT_LESSON, WRONG_OPERATION_TEXT, PRODUCT_OPTIONS))
    fake_llm.queue(draft)
    fake_llm.queue(make_reviewer_final(decision="fail_closed", final=None,
                                       fail_reason_code="outside_lesson"))

    response = run_practice_turn(store, fake_llm, turn_for(PILOT_LESSON))

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert fake_llm.call_count == 2


# ---------------------------------------------------------------------------
# 4) NEPILOT PONAŠANJE OSTAJE NETAKNUTO
# ---------------------------------------------------------------------------

def test_non_pilot_lesson_publishes_exactly_as_before(fake_llm, store):
    task = task_for(NON_PILOT_LESSON, "Izračunaj $\\frac{1}{2} + \\frac{1}{3}$.",
                    ("$\\frac{5}{6}$", "$\\frac{2}{5}$", "$\\frac{1}{6}$",
                     "$\\frac{2}{6}$"))
    draft = make_tutor_draft(new_task=task)
    fake_llm.queue(draft)
    fake_llm.queue(make_reviewer_final(decision="approve", final=draft))

    response = run_practice_turn(store, fake_llm, turn_for(NON_PILOT_LESSON))

    assert response.get("status") == "ready"
    assert fake_llm.call_count == 2


def test_divisibility_blocking_behaviour_is_unchanged():
    """6-03-004 i dalje nosi svoj naslovni dokazani zahtjev — kapacitetna
    ekspanzija mu je DODALA blocking semantički ugovor, a stari sloj ostaje."""
    from matbot import lesson_fidelity

    context = lesson_context_module.build(6, DIVISIBILITY_LESSON)
    assert context.semantic_contract is not None
    assert context.semantic_contract.blocking
    requirement = lesson_fidelity.semantic_task_requirement(context.title)
    assert requirement is not None
    assert requirement.failure_for("Koji od ponuđenih brojeva je djelilac broja 84?")
    task = task_for(DIVISIBILITY_LESSON,
                    "Koji od ponuđenih brojeva je djelilac broja 84?",
                    ("$12$", "$5$", "$9$", "$25$"))
    codes = [issue.code for issue in package_preflight.collect_package_issues(task)]
    assert "divisibility_rules_not_required_by_visible_task" in codes


# ---------------------------------------------------------------------------
# Faza 4H: ovi testovi ispituju MODEL-strategiju (Tutor+Recenzent) i na
# porodičnim lekcijama koje produkcija sada rutira deterministički. Izričito
# isključenje je ISTI mehanizam koji služi i kao produkcijski rollback
# (MATBOT_DETERMINISTIC_PRACTICE=disabled) — model-put time ostaje trajno
# testiran, bajt za bajt kakav je i bio.
# ---------------------------------------------------------------------------
import pytest as _pytest_f4h


@_pytest_f4h.fixture(autouse=True)
def _model_route_only_f4h(monkeypatch):
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")
