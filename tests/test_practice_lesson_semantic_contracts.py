"""PORTANA regresijska pokrivenost ukinutog `matbot/lesson_task_validation.py`.

Taj modul je nosio šest lekcija šestog razreda kao Python grane
(`_LESSON_KIND = {"6-04-005": "expansion", …}` + `elif kind == …`). Grane su
uklonjene, ali NIJEDNO ponašanje koje su štitile ne smije nestati — ovaj fajl
dokazuje da svako od njih i dalje važi kroz PODATKE (ugovor lekcije) i
generički sloj: od Faze 1 to su serverski GENERATOR kostura i isti
deterministi (constraints/difficulty/verifiers) koji sada čuvaju serversku
konstrukciju umjesto modelovog dokaza.

`PORTED_BEHAVIOURS` je zamrznut manifest: test na dnu pada ako se neko ponašanje
izgubi iz ovog fajla. Time brisanje starog validatora ne može tiho obrisati i
njegovu pokrivenost.
"""
import copy
import random

import pytest

from matbot.contracts import (constraints, evidence as ev, generator, pipeline,
                              registry, verifiers)
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore
from tests.conftest import FakeLLM, frac, make_output, make_task, node

# Ponašanja koja je štitio obrisani validator po lekciji. Svako ime mora
# postojati kao `test_<ime>` u ovom fajlu (provjerava test na dnu).
PORTED_BEHAVIOURS = (
    "equal_denominator_task_passes",
    "equal_lesson_rejects_unlike_denominators",
    "unlike_lesson_rejects_equal_denominators_only",
    "unlike_denominator_task_passes",
    "multiplication_task_passes",
    "division_task_passes",
    "multiplication_lesson_rejects_wrong_operation",
    "division_lesson_rejects_wrong_operation",
    "expansion_task_passes",
    "reducing_task_passes",
    "expansion_lesson_rejects_wrong_operation",
    "not_engaged_is_explicitly_not_valid",
    "multiple_correct_options_reject",
    "no_correct_option_rejects",
    "marked_answer_mismatch_rejects",
    "expected_answer_mismatch_rejects",
    "exactly_one_classified_error_option_passes",
    "ambiguous_error_options_reject",
    "multiple_defensible_errors_reject",
    "expansion_and_reducing_error_directions_do_not_cross_lessons",
    "difficulty_requests_prefer_primary_archetype",
    "harder_valid_task_keeps_fingerprint_and_primary_skill",
    "harder_rejection_is_one_call_and_state_is_unchanged",
    "lesson_contract_prompt_is_present_for_harder_request",
)

EXPANSION, REDUCING = "6-04-005", "6-04-006"
EQUAL, UNLIKE = "6-04-009", "6-04-010"
MULTIPLY, DIVIDE = "6-04-011", "6-04-012"

CONTRACTS = registry.load_all()


def facts_of(expression):
    return ev.facts_for((expression,))


def check(topic, expression):
    return constraints.check_evidence(CONTRACTS[topic], facts_of(expression))


def generated(topic, seed=0):
    contract = CONTRACTS[topic]
    return contract, generator.generate(
        contract, contract.effective_archetypes[0], rng=random.Random(seed))


# ---------------------------------------------------------------------------
# Ista vještina, ista šifra: razlika je JEDNA vrijednost ugovora
# ---------------------------------------------------------------------------

def test_equal_denominator_task_passes():
    result = check(EQUAL, node("subtract", frac(7, 12), frac(3, 12)))
    assert result.valid, result.code


def test_equal_lesson_rejects_unlike_denominators():
    result = check(EQUAL, node("subtract", frac(7, 12), frac(1, 4)))
    assert not result.valid
    assert result.code == "denominators_must_be_equal"


def test_unlike_lesson_rejects_equal_denominators_only():
    result = check(UNLIKE, node("add", frac(2, 7), frac(3, 7)))
    assert not result.valid
    assert result.code == "denominators_must_differ"


def test_unlike_denominator_task_passes():
    result = check(UNLIKE, node("add", frac(1, 3), frac(1, 4)))
    assert result.valid, result.code


def test_multiplication_task_passes():
    contract, skeleton = generated(MULTIPLY)
    assert ev.facts_for(skeleton.primary_nodes).operations == {"multiply"}
    assert generator.self_verify(contract, skeleton) == (True, "ok")


def test_division_task_passes():
    contract, skeleton = generated(DIVIDE)
    assert ev.facts_for(skeleton.primary_nodes).operations == {"divide"}
    assert generator.self_verify(contract, skeleton) == (True, "ok")


def test_multiplication_lesson_rejects_wrong_operation():
    result = check(MULTIPLY, node("divide", frac(2, 3), frac(4, 5)))
    assert not result.valid
    assert result.code == "operation_not_allowed"


def test_division_lesson_rejects_wrong_operation():
    result = check(DIVIDE, node("multiply", frac(2, 3), frac(3, 5)))
    assert not result.valid
    assert result.code == "operation_not_allowed"


def test_expansion_task_passes():
    contract, skeleton = generated(EXPANSION)
    answer = skeleton.option_nodes[skeleton.correct_index]
    relation = constraints.check_answer_relation(contract, skeleton.reference, answer)
    assert relation.valid, relation.code
    assert abs(answer.num) > abs(skeleton.reference.num)


def test_reducing_task_passes():
    contract, skeleton = generated(REDUCING)
    answer = skeleton.option_nodes[skeleton.correct_index]
    relation = constraints.check_answer_relation(contract, skeleton.reference, answer)
    assert relation.valid, relation.code
    assert abs(answer.num) < abs(skeleton.reference.num)


def test_expansion_lesson_rejects_wrong_operation():
    """Račun (izračunaj izraz) u lekciji o proširivanju/skraćivanju — raniji
    `expansion_lesson_wrong_operation`: arhetip koji lekcija ne nudi ne može
    biti ni planiran ni generisan."""
    for topic in (EXPANSION, REDUCING):
        plan = pipeline.GenerationPlan("direct_computation", "rotation")
        prepared = pipeline.prepare_task(CONTRACTS[topic], plan, rng=random.Random(0))
        assert not prepared.ok
        assert prepared.code == "archetype_not_allowed"


# ---------------------------------------------------------------------------
# „Nije se moglo dokazati“ NIKAD ne znači „prošlo je“
# ---------------------------------------------------------------------------

def test_not_engaged_is_explicitly_not_valid():
    """Uslov o imeniocima koji se iz izraza uopšte ne može očitati mora pasti
    kao NEPROVJEREN (engaged=False), nikad kao „prošao“."""
    lonely = frac(3, 1)  # cio broj: nijedan par imenilaca se ne vidi
    result = constraints.check_denominator_relation(CONTRACTS[EQUAL], facts_of(lonely))
    assert not result.valid
    assert not result.engaged
    assert result.code == "denominator_relation_not_provable"


def _option_nodes(*pairs):
    return tuple(frac(*pair) for pair in pairs)


def test_multiple_correct_options_reject():
    from fractions import Fraction
    result = verifiers.verify_exact_rational(
        Fraction(2, 5), _option_nodes((2, 5), (6, 15), (1, 5), (4, 5)), 0)
    assert result.code == "multiple_correct_options"


def test_no_correct_option_rejects():
    from fractions import Fraction
    result = verifiers.verify_exact_rational(
        Fraction(2, 5), _option_nodes((1, 5), (3, 5), (4, 5), (6, 5)), 0)
    assert result.code == "no_correct_option"


def test_marked_answer_mismatch_rejects():
    from fractions import Fraction
    result = verifiers.verify_exact_rational(
        Fraction(2, 5), _option_nodes((1, 5), (2, 5), (4, 5), (6, 5)), 0)
    assert result.code == "marked_answer_mismatch"


def test_expected_answer_mismatch_rejects():
    """Raniji uzrok: modelov expected_answer se razilazio s istinom. Server-
    generisan kostur tu klasu čini strukturno nemogućom — očekivani odgovor JE
    tekst označene opcije, po konstrukciji, za svaki kostur."""
    for topic in (EQUAL, UNLIKE, MULTIPLY, DIVIDE, EXPANSION, REDUCING):
        for seed in range(5):
            _, skeleton = generated(topic, seed)
            assert skeleton.expected_answer == skeleton.option_texts[skeleton.correct_index]


# ---------------------------------------------------------------------------
# Zadaci o grešci — kategorija se IZVODI strukturno, ne iz bosanskih riječi.
# (identify_error je u Fazi 1 ODGOĐEN kao arhetip; deterministi ostaju za K4.)
# ---------------------------------------------------------------------------

ERROR_STEPS = (node("add", frac(7, 12), frac(3, 12)), frac(10, 24), frac(5, 12))
ERROR_CATEGORIES = ["combined_denominators", "wrong_numerator", "wrong_operation"]


def test_exactly_one_classified_error_option_passes():
    result = verifiers.verify_error_category(
        ERROR_STEPS, ERROR_CATEGORIES, 0, CONTRACTS[EQUAL].error_category_set)
    assert result.ok, result.code


def test_ambiguous_error_options_reject():
    result = verifiers.verify_error_category(
        ERROR_STEPS,
        ["combined_denominators", "combined_denominators", "wrong_operation"],
        0, CONTRACTS[EQUAL].error_category_set)
    assert result.code == "ambiguous_error_options"


def test_multiple_defensible_errors_reject():
    steps = (node("subtract", frac(7, 12), frac(1, 4)),
             node("subtract", frac(7, 12), frac(1, 8)), frac(6, 8), frac(3, 4))
    result = verifiers.verify_error_category(
        steps, ["incorrect_conversion", "wrong_numerator", "wrong_operation"],
        0, CONTRACTS[UNLIKE].error_category_set)
    assert result.code == "multiple_defensible_errors"


def test_expansion_and_reducing_error_directions_do_not_cross_lessons():
    """Raniji `expansion_error_direction_mismatch` — sada bez ijedne grane po
    lekciji: kategorija nosi smjer, a ugovor deklariše koji smjer uči."""
    steps = (frac(8, 20), frac(2, 6))
    categories = ["wrong_reduction", "wrong_operation", "unequal_scaling"]
    fine = verifiers.verify_error_category(
        steps, categories, 0, CONTRACTS[REDUCING].error_category_set)
    assert fine.ok, fine.code
    crossed = verifiers.verify_error_category(
        steps, categories, 0, CONTRACTS[EXPANSION].error_category_set)
    assert crossed.code == "error_category_outside_contract"


# ---------------------------------------------------------------------------
# Teže/lakše ne mijenja vještinu
# ---------------------------------------------------------------------------

def test_difficulty_requests_prefer_primary_archetype():
    contract = CONTRACTS[EQUAL]
    primary = contract.effective_archetypes[0]
    assert pipeline.select_archetype(
        contract, current=primary, difficulty_request="harder") == primary
    assert pipeline.select_archetype(
        contract, current="identify_error", difficulty_request="easier") == primary


# ---------------------------------------------------------------------------
# Integracija kroz cijeli Practice turn
# ---------------------------------------------------------------------------

def _turn(**updates):
    payload = {
        "session_id": "lesson-contract", "grade": 6,
        "selected_topic": EQUAL, "selected_oblast": "ignored",
        "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "",
        "last_tutor_task": "", "interaction_type": "student_question",
        "selected_option_id": "", "client_turn_id": "",
    }
    payload.update(updates)
    return payload


def test_harder_valid_task_keeps_fingerprint_and_primary_skill():
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(new_task=make_task()))   # signal; sadržaj se ignoriše
    first = run_practice_turn(store, fake, _turn())
    assert first["status"] == "ready"
    state = store.peek("lesson-contract")
    fingerprint = state["curriculum_fingerprint"]
    first_task = state["current_task"]

    fake.queue(make_output(new_task=make_task()))
    second = run_practice_turn(store, fake, _turn(
        student_message="Daj mi teži zadatak.", difficulty_request="harder"))
    assert second["status"] == "ready", second
    state = store.peek("lesson-contract")
    assert state["curriculum_fingerprint"] == fingerprint
    assert state["current_family"] == "direct_computation"
    assert state["current_task"] != first_task
    assert state["difficulty"] == "hard"


def test_harder_rejection_is_one_call_and_state_is_unchanged(monkeypatch):
    """Odbijanje UKLJUČENOG ugovora: sigurna poruka, NULA promjene stanja i
    nikad prelazak na legacy. Od Faze 1 se kostur pravi PRIJE jedinog poziva:
    kad priprema padne a aktivni zadatak postoji, turn smije nastaviti kao
    razgovor — ali model koji ipak signalizira novi zadatak pada zatvoreno,
    s najviše JEDNIM potrošenim pozivom."""
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(new_task=make_task()))
    run_practice_turn(store, fake, _turn())
    before = copy.deepcopy(store.peek("lesson-contract"))
    calls_before = fake.call_count

    def _fail(*args, **kwargs):
        raise generator.GenerationError("forced")

    monkeypatch.setattr(generator, "generate", _fail)
    fake.queue(make_output(new_task=make_task()))
    response = run_practice_turn(store, fake, _turn(
        student_message="Daj mi teži zadatak.", difficulty_request="harder"))

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in response
    assert fake.call_count == calls_before + 1
    assert store.peek("lesson-contract") == before


def test_lesson_contract_prompt_is_present_for_harder_request():
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(new_task=make_task()))
    run_practice_turn(store, fake, _turn(difficulty_request="harder"))
    prompt = fake.calls[0][1]
    assert "UGOVOR LEKCIJE" in prompt
    # Server prilaže GOTOV zadatak — model dobija tekst i opcije, ne uputstvo
    # da sam smisli matematiku.
    assert "SERVER JE VEĆ SASTAVIO NOVI ZADATAK" in prompt
    assert store.peek("lesson-contract")["current_task"] in prompt
    assert "TVOJ POSAO JE SAMO PROZA" in prompt


# ---------------------------------------------------------------------------
# Manifest: brisanje starog validatora ne smije obrisati njegovu pokrivenost
# ---------------------------------------------------------------------------

def test_every_ported_behaviour_still_has_a_test():
    import tests.test_practice_lesson_semantic_contracts as module

    missing = [name for name in PORTED_BEHAVIOURS if not hasattr(module, f"test_{name}")]
    assert not missing, (
        f"Ponašanja iz ukinutog lesson_task_validation.py bez testa: {missing}"
    )
