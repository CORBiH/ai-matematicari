"""ZNAČENJE `checks.*` MORA STIĆI RECENZENTU (arhitektonska Faza 1).

ZAŠTO POSTOJI. `_REVIEWER_CHECK_SEMANTICS_RULE` je uveden commitom c7552b8 da
spriječi tačno jedan oblik payloada: uspješna odluka uz vlastitu oborenu
blokirajuću provjeru. Statička kapija Faze 0 je izmjerila da blok nikad nije
bio uvezan u `build_reviewer_instructions`, pa ga recenzent nikad nije dobio.

Klasa kvara je ostala živa i poslije toga: FW-D04 (kampanja final40_c17538a,
lekcija 7-02-019) je pao kodom

    tutor_rejected … stage=reviewer_payload intent=generate_task
    detail=odobreno uprkos oborenim provjerama: ['inside_lesson']

uz odluku `correct`. Zatečeni `_REVIEWER_DECISION_RULE` tu kontradikciju
opisuje IZRIČITO SAMO za `approve` („A single false check with `approve` is a
contradiction“), dok `validate_reviewer` — pošto `fail_closed` izlazi ranije —
`blocking_failed_checks` primjenjuje i na `approve` I na `correct`. Recenzentu
je dakle nedostajalo pravilo koje pokriva baš njegov slučaj.

ŠTA OVAJ FAJL DOKAZUJE:
  1. blok stvarno stiže u sastavljen recenzentov prompt (i nigdje drugdje);
  2. imenuje TAČNO ona četiri polja koja `reviewer_authority` drži smrtonosnim;
  3. izričito kaže da netačna provjera obara i `approve` i `correct`;
  4. NE promoviše samoprijavu u serverski autoritet — to se dokazuje i
     ponašanjem: `true` provjera ne spašava paket koji validator obori;
  5. serverski preflight nalazi i dalje stižu recenzentu i ostaju mjerodavni;
  6. granica od dva poziva se ne mijenja.

ZERO poziva modela, ZERO mreže.
"""
from __future__ import annotations

import pytest

from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor import package_preflight, reviewer_authority
from matbot.tutor import prompts as tutor_prompts
from matbot.tutor.lesson_context import build
from matbot.tutor.pipeline import REVIEWER_FINAL_INTEGRITY_CODE, SAFE_ERROR_MESSAGE
from matbot.tutor.schema import (DifficultyEvidence, ReviewerChecks, ReviewerFinal,
                                 SignatureParameter, TaskPayload, TaskSignature,
                                 TutorDraft, TutorOption, UnifiedOutputError,
                                 validate_reviewer)
from tests.conftest import (FakeLLM, make_reviewer_checks, make_reviewer_final,
                            make_task_payload, make_tutor_draft)

GRADE, TOPIC = 8, "8-04-008"
SESSION = "rev-check-semantics"


@pytest.fixture(autouse=True)
def _model_route(monkeypatch):
    # Ova lekcija se u produkciji rutira deterministički; ovdje se ciljano
    # ispituje MODEL put. Isti mehanizam je i produkcijski rollback.
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")


def _reviewer_prompt(grade=9, topic="9-02-006"):
    return tutor_prompts.build_reviewer_instructions(build(grade, topic))


# ---------------------------------------------------------------------------
# 1. PROMPT — pravilo stiže i govori tačno ono što validator radi
# ---------------------------------------------------------------------------

def test_the_check_semantics_rule_reaches_the_reviewer():
    text = _reviewer_prompt()
    assert "WHAT `checks.*` DESCRIBE" in text
    assert text.count(tutor_prompts._REVIEWER_CHECK_SEMANTICS_RULE) == 1


def test_the_rule_is_not_sent_to_the_tutor():
    """Tutor ne donosi odluku i ne popunjava `checks.*` — pravilo mu ne treba."""
    tutor = tutor_prompts.build_tutor_instructions(build(9, "9-02-006"))
    assert "WHAT `checks.*` DESCRIBE" not in tutor


def test_the_rule_names_exactly_the_server_blocking_checks():
    """Imena u promptu moraju biti TAČNO `MODEL_ONLY_BLOCKING_CHECKS`."""
    text = _reviewer_prompt()
    for name in reviewer_authority.MODEL_ONLY_BLOCKING_CHECKS:
        assert f"`{name}`" in text, name
    # Savjetodavne se NAMJERNO ne proglašavaju smrtonosnim.
    for name in reviewer_authority.ADVISORY_CHECKS:
        assert f"`{name}` are the ones the server cannot verify" not in text


def test_the_rule_covers_correct_and_not_only_approve():
    """FW-D04 je bio `correct`; zatečeni blok odluke pokriva samo `approve`."""
    text = _reviewer_prompt()
    assert "contradicts BOTH `approve` and `correct`" in text
    # Zatečeno pravilo odluke ostaje netaknuto uz novo.
    assert "A single false check with `approve` is a\n  contradiction" in text


def test_the_rule_matches_the_compact_approval_contract():
    """Poslije 523dfce recenzent na `approve` IZOSTAVLJA `final`.

    Pravilo zato govori o paketu koji se OBJAVLJUJE, ne bezuslovno o `final` —
    inače bi u istom promptu protivrječilo bloku ODLUKA."""
    text = _reviewer_prompt()
    assert "the package the server will publish" in text
    assert "for `approve`\n  it is the unchanged draft" in text
    assert "`final` IZOSTAVI" in text          # zatečeni blok odluke, netaknut


def test_the_rule_does_not_promote_self_report_into_proof():
    text = _reviewer_prompt()
    assert "never accepted as proof and never replaces those validators" in text


def test_the_rule_keeps_the_anti_overreporting_clause():
    """F4A: `language_age_appropriate=false` zbog tona je gubio ispravan paket."""
    assert "Do not lower a check merely because you are unsure about tone" in _reviewer_prompt()


def test_the_rule_ships_for_every_grade():
    for grade, topic in ((6, "6-01-001"), (7, "7-02-019"), (8, "8-04-008"),
                         (9, "9-04-014")):
        assert "WHAT `checks.*` DESCRIBE" in _reviewer_prompt(grade, topic)


# ---------------------------------------------------------------------------
# 2. SERVERSKO PONAŠANJE JE NEPROMIJENJENO (šema + matrica autoriteta)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("decision", ("approve", "correct"))
def test_a_clean_payload_remains_coherent(decision):
    validate_reviewer(make_reviewer_final(decision=decision), make_tutor_draft())


@pytest.mark.parametrize("decision", ("approve", "correct"))
@pytest.mark.parametrize("name", sorted(reviewer_authority.MODEL_ONLY_BLOCKING_CHECKS))
def test_a_false_blocking_check_contradicts_both_successful_decisions(decision, name):
    """Ovo je invarijanta koju prompt sada opisuje — server je nepromijenjen."""
    reviewer = make_reviewer_final(
        decision=decision, checks=make_reviewer_checks(**{name: False}))
    with pytest.raises(UnifiedOutputError) as error:
        validate_reviewer(reviewer, make_tutor_draft())
    assert name in str(error.value)


def test_fail_closed_stays_coherent_with_a_false_blocking_check():
    """Iskren `fail_closed` je ispravan izlaz, ne kontradikcija."""
    validate_reviewer(
        make_reviewer_final(decision="fail_closed", fail_reason_code="outside_lesson",
                            checks=make_reviewer_checks(inside_lesson=False)),
        make_tutor_draft())


def test_advisory_checks_still_do_not_veto_a_complete_package():
    """Faza 4C se ne mijenja: ton nikad ne obara ispravan paket."""
    for name in sorted(reviewer_authority.ADVISORY_CHECKS):
        validate_reviewer(
            make_reviewer_final(decision="correct",
                                checks=make_reviewer_checks(**{name: False})),
            make_tutor_draft())


# ---------------------------------------------------------------------------
# 3. KRAJ-DO-KRAJA: samoprijava NIJE dokaz, preflight jeste mjerodavan
# ---------------------------------------------------------------------------

_DUPLICATE_OPTIONS = ("$16\\sqrt{3}\\,\\text{cm}^2$", "$32\\,\\text{cm}^2$",
                      "$\\sqrt{768}\\,\\text{cm}^2$", "$64\\,\\text{cm}^2$")


def _evidence():
    return DifficultyEvidence(
        reasoning_steps=1, condition_count=1, operation_count=1,
        representation_change_count=0, requires_explanation=False,
        requires_comparison=False, requires_construction=False,
        requires_proof_or_justification=False, combines_concepts=False)


def _task(context, options):
    return TaskPayload(
        selected_lesson_id=context.topic_id, selected_lesson_title=context.title,
        target_difficulty_level=1,
        text="Jednakostraničan trougao ima stranicu dužine $8\\,\\text{cm}$. "
             "Izračunaj njegovu površinu.",
        task_type="multiple_choice",
        options=[TutorOption(id="abcd"[i], text=value)
                 for i, value in enumerate(options)],
        correct_option_index=0, correct_option_id="a",
        expected_answer=options[0],
        solution="Površina se računa po formuli za ovu lekciju.",
        difficulty="easy", difficulty_evidence=_evidence(),
        task_signature=TaskSignature(
            task_family="area", operation_or_relation="compute",
            normalized_parameters=[SignatureParameter(name="case", value="one")],
            required_conditions=["a>0"], relevant_objects=["figure"],
            answer_type="multiple_choice"))


def _all_true_checks():
    return ReviewerChecks(
        math_correct=True, marked_option_correct=True, inside_lesson=True,
        intent_handled=True, difficulty_direction_correct=True,
        response_addresses_student=True, task_solvable_and_unambiguous=True,
        mathjax_valid=True, language_age_appropriate=True, independently_solved=True,
        independent_answer="provjereno", task_package_consistent=True,
        difficulty_evidence_valid=True, task_signature_consistent=True)


def _turn():
    return {"session_id": SESSION, "grade": GRADE, "selected_topic": TOPIC,
            "selected_oblast": "", "student_message": "Daj mi zadatak.", "intent": "",
            "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
            "interaction_type": "student_question", "selected_option_id": "",
            "client_turn_id": ""}


def test_all_true_self_checks_do_not_override_a_deterministic_finding(caplog):
    """Recenzent tvrdi da je SVE u redu; server ipak obara dokazan duplikat."""
    context = build(GRADE, TOPIC)
    duplicated = _task(context, _DUPLICATE_OPTIONS)
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="izabrana lekcija", new_task=duplicated)
    fake = FakeLLM()
    fake.queue(draft)
    fake.queue(ReviewerFinal(decision="approve", checks=_all_true_checks(),
                             final=None,
                             reviewed_difficulty_evidence=duplicated.difficulty_evidence))
    store = SessionStore()
    with caplog.at_level("INFO"):
        response = run_practice_turn(store, fake, _turn())

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert REVIEWER_FINAL_INTEGRITY_CODE in caplog.text
    assert store.peek(SESSION) is None            # nijedna mutacija sesije
    assert fake.call_count == 2                   # granica od dva poziva stoji


def test_the_server_preflight_finding_still_reaches_the_reviewer_input():
    """Nalaz je serverska činjenica i mora stajati u ULAZU drugog poziva."""
    context = build(GRADE, TOPIC)
    duplicated = _task(context, _DUPLICATE_OPTIONS)
    issues = package_preflight.collect_package_issues(duplicated)
    assert any(issue.code == package_preflight.SEMANTIC_DUPLICATE_CODE
               for issue in issues)

    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="izabrana lekcija", new_task=duplicated)
    fake = FakeLLM()
    fake.queue(draft)
    fake.queue(ReviewerFinal(decision="approve", checks=_all_true_checks(), final=None,
                             reviewed_difficulty_evidence=duplicated.difficulty_evidence))
    run_practice_turn(SessionStore(), fake, _turn())

    assert len(fake.reviewer_calls) == 1
    instructions, input_text = fake.reviewer_calls[0]
    assert "SERVER-DETECTED DRAFT ISSUES" in input_text
    assert package_preflight.SEMANTIC_DUPLICATE_CODE in input_text
    # Isti poziv nosi i novo pravilo o značenju provjera.
    assert "WHAT `checks.*` DESCRIBE" in instructions


def test_the_two_call_budget_is_unchanged_on_a_clean_turn():
    context = build(GRADE, TOPIC)
    clean = _task(context, ("$16\\sqrt{3}\\,\\text{cm}^2$", "$32\\,\\text{cm}^2$",
                            "$48\\,\\text{cm}^2$", "$64\\,\\text{cm}^2$"))
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="izabrana lekcija", new_task=clean)
    fake = FakeLLM()
    fake.queue(draft)
    fake.queue(ReviewerFinal(decision="approve", checks=_all_true_checks(), final=None,
                             reviewed_difficulty_evidence=clean.difficulty_evidence))
    response = run_practice_turn(SessionStore(), fake, _turn())
    assert response["answer"] != SAFE_ERROR_MESSAGE
    assert fake.call_count == 2
    assert len(fake.tutor_calls) == 1 and len(fake.reviewer_calls) == 1
