r"""Recenzentova ispravka mora RIJEŠITI nalaz, ne ostaviti ga ni napraviti novi.

ŽIVI RUN postStabilityFixes — četiri različita načina na koja je jedina
recenzentova prilika za ispravku propala:

    B11  7-03-008  nacrt: semantically_duplicate_options (equivalent_fraction)
                   final: ISTI par preživio ispravku (unchanged=False)
    B13  9-05-013  nacrt: difficulty_evidence_outside_target
                   final: ispravka UVELA unsafe_option_notation na SVE četiri
                          opcije + unsafe_expected_answer_notation
    B56  8-08-009  nacrt: numeric_inconsistency (solution)
                   final: numeric_inconsistency OSTAO + NOVI
                          semantically_duplicate_options (numeric_exact_vs_rounded)
    B28  9-01-005  final: `correct` uz vlastite provjere math_correct=false i
                          marked_option_correct=false

Server je u sva četiri slučaja ISPRAVNO pao zatvoreno i ništa nije objavio.
Ovi testovi ZAKLJUČAVAJU te invarijante da ih buduća izmjena ne oslabi —
nijedna se ovdje ne popušta i nijedan validator se ne uklanja.
"""
import pytest

from matbot import option_equivalence
from matbot.tutor import pipeline as tutor_pipeline
from matbot.tutor import prompts as tutor_prompts
from tests.conftest import (make_reviewer_checks, make_reviewer_final, make_task_payload,
                            make_tutor_draft, queue_two_call)

LESSON = "9-04-003"
SESSION = "rc-1"

GOOD = (r"$x=4$", r"$x=2$", r"$x=6$", r"$x=3$")
EQUIVALENT_FRACTIONS = (r"$\frac{1}{2}$", r"$\frac{2}{4}$", r"$\frac{3}{4}$", r"$\frac{1}{4}$")
EXACT_VS_ROUNDED = (r"$14\pi$", r"$7\pi$", r"$28\pi$", r"$43,96$")
UNSAFE_OPTIONS = (r"$x=\ty{4}$", r"$x=\ty{2}$", r"$x=\ty{6}$", r"$x=\ty{3}$")


@pytest.fixture(autouse=True)
def _levels_on(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def _turn(client_turn_id="rc-t1"):
    return {
        "session_id": SESSION, "grade": 9, "selected_topic": LESSON,
        "selected_oblast": "", "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": client_turn_id,
    }


def _task(options=GOOD, expected=None, **updates):
    task = make_task_payload(text=r"Riješi jednačinu: $3x=12$", options=options,
                             correct_option_index=0,
                             expected=expected if expected is not None else options[0])
    return task.model_copy(update=updates) if updates else task


def _run(store, fake_llm, draft_task, final_task, decision="correct", checks=None):
    draft = make_tutor_draft(intent="generate_task", new_task=draft_task)
    final = make_tutor_draft(intent="generate_task", new_task=final_task)
    queue_two_call(fake_llm, draft=draft,
                   reviewer=make_reviewer_final(decision=decision, final=final,
                                                checks=checks))
    return tutor_pipeline.run_turn(store, fake_llm, _turn())


def _assert_fail_closed(response, store, fake_llm):
    assert response.get("status") is None
    assert response["answer"] == tutor_pipeline.SAFE_ERROR_MESSAGE
    assert fake_llm.call_count == 2            # nikad treći poziv
    assert store.peek(SESSION) is None         # bez mutacije sesije


# ---------------------------------------------------------------------------
# 1–2. ISPRAVKA KOJA NE RIJEŠI ORIGINALNI NALAZ  (B11)
# ---------------------------------------------------------------------------

def test_correction_that_keeps_the_equivalent_option_pair_is_rejected(store, fake_llm):
    broken = _task(options=EQUIVALENT_FRACTIONS, expected=EQUIVALENT_FRACTIONS[0])
    _assert_fail_closed(_run(store, fake_llm, broken, broken), store, fake_llm)


def test_the_original_issue_must_be_gone_from_the_final_package(store, fake_llm):
    """Nije dovoljno da se paket promijenio — nalaz mora nestati."""
    broken = _task(options=EQUIVALENT_FRACTIONS, expected=EQUIVALENT_FRACTIONS[0])
    still_broken = _task(options=(r"$\frac{1}{2}$", r"$\frac{4}{8}$", r"$\frac{3}{4}$",
                                  r"$\frac{1}{4}$"),
                         expected=r"$\frac{1}{2}$")
    # Paket JESTE drugačiji, ali ekvivalentan par i dalje postoji.
    assert option_equivalence.find_equivalent_option_pairs_with_types(
        [option.text for option in still_broken.options])
    _assert_fail_closed(_run(store, fake_llm, broken, still_broken), store, fake_llm)


# ---------------------------------------------------------------------------
# 3. ISPRAVKA KOJA UVODI NOVI DEFEKT  (B13)
# ---------------------------------------------------------------------------

def test_correction_that_introduces_unsafe_options_is_rejected(store, fake_llm):
    clean_draft = _task()
    _assert_fail_closed(
        _run(store, fake_llm, clean_draft,
             _task(options=UNSAFE_OPTIONS, expected=UNSAFE_OPTIONS[0])),
        store, fake_llm)


# ---------------------------------------------------------------------------
# 4. EGZAKTNA I ZAOKRUŽENA VRIJEDNOST ISTOG ODGOVORA  (B56)
# ---------------------------------------------------------------------------

def test_exact_and_rounded_forms_of_one_answer_are_a_duplicate_pair():
    """Deterministički dokaz — ne uvodi se nova logika, ova već postoji."""
    pairs = option_equivalence.find_equivalent_option_pairs_with_types(
        list(EXACT_VS_ROUNDED))
    assert pairs and pairs[0][2] == "numeric_exact_vs_rounded"


def test_correction_with_an_exact_and_rounded_pair_is_rejected(store, fake_llm):
    _assert_fail_closed(
        _run(store, fake_llm, _task(),
             _task(options=EXACT_VS_ROUNDED, expected=EXACT_VS_ROUNDED[0])),
        store, fake_llm)


def test_genuinely_different_numeric_options_stay_allowed():
    assert option_equivalence.find_equivalent_option_pairs_with_types(
        [r"$14\pi$", r"$7\pi$", r"$28\pi$", r"$21\pi$"]) == []


# ---------------------------------------------------------------------------
# 5. ODLUKA UZ OBORENU OBAVEZNU PROVJERU  (B28)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("decision", ["approve", "correct"])
@pytest.mark.parametrize("failing", ["math_correct", "marked_option_correct"])
def test_decision_with_a_false_mandatory_check_is_rejected(store, fake_llm, decision, failing):
    task = _task()
    response = _run(store, fake_llm, task, task, decision=decision,
                    checks=make_reviewer_checks(**{failing: False}))
    _assert_fail_closed(response, store, fake_llm)


# ---------------------------------------------------------------------------
# 6. VALIDNA ISPRAVKA I DALJE PROLAZI
# ---------------------------------------------------------------------------

def test_a_real_correction_is_published(store, fake_llm):
    broken = _task(options=EQUIVALENT_FRACTIONS, expected=EQUIVALENT_FRACTIONS[0])
    response = _run(store, fake_llm, broken, _task())

    assert response["status"] == "ready"
    assert "3x=12" in response["answer"]
    assert fake_llm.call_count == 2
    assert store.peek(SESSION)["current_task"]


def test_a_plain_approve_of_a_clean_package_is_published(store, fake_llm):
    task = _task()
    response = _run(store, fake_llm, task, task, decision="approve")
    assert response["status"] == "ready"
    assert fake_llm.call_count == 2


# ---------------------------------------------------------------------------
# 7. PROMPT MORA IMENOVATI ŽIVE NAČINE PADA
# ---------------------------------------------------------------------------

def test_reviewer_prompt_forbids_introducing_a_new_defect_while_correcting():
    from matbot.tutor import lesson_context as lesson_context_module

    text = tutor_prompts.build_reviewer_instructions(
        lesson_context_module.build(9, LESSON))
    lowered = text.lower()
    assert "must not introduce a new defect" in lowered
    assert "every reported issue" in lowered


# ---------------------------------------------------------------------------
# Kapacitetna ekspanzija: ovi testovi ispituju MODEL-strategiju (Tutor +
# Recenzent) i na lekcijama koje produkcija sada rutira deterministički
# (blocking ugovor + potpun generator). Izričito isključenje je ISTI mehanizam
# koji služi i kao produkcijski rollback (MATBOT_DETERMINISTIC_PRACTICE=
# disabled) — model-put time ostaje trajno testiran, bajt za bajt kakav je bio.
# ---------------------------------------------------------------------------
import pytest as _pytest_capex


@_pytest_capex.fixture(autouse=True)
def _model_route_only_capex(monkeypatch):
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")
