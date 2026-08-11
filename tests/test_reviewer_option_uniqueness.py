r"""TAČNO JEDNA TAČNA OPCIJA — vlasništvo RECENZENTA (živi FINAL40 FW-F03).

ŽIVI BLOKATOR IZDANJA (kanonski FINAL40 na faf7a81, lekcija 6. razreda o
prikazu funkcije). Objavljeno je:

    „Relacija je zadana tačkama: (1,2), (2,3), (3,2).
     Predstavlja li ovaj skup tačaka funkciju?"

    c) „Da — svaki različit x u skupu ima tačno jedan odgovarajući y, pa je
        relacija funkcija."                                    ← OZNAČENO
    d) „Da — relacija je funkcija, i dopušteno je da se ista vrijednost y
        ponavlja za različite x."

OBJE su matematički tačne. Učenik koji izabere `d` nije pogriješio, a označen je
kao netačan.

ZAŠTO NIJEDAN DETERMINISTI TO NE VIDI: `option_equivalence` i `mcq_integrity`
dokazuju EKVIVALENCIJU. Te dvije opcije NISU ekvivalentne — one su dva
različita, nezavisno TAČNA iskaza. Utvrditi to znači ocijeniti matematičku
istinu proizvoljne proze, što nijedan ograničeni provjerivač ne može i ne smije
tvrditi. `marked_option_correct` je pri tome bilo ISTINITO: ono je jednostrana
tvrdnja o označenoj opciji i o ostale tri ne kaže ništa.

Zato klasa dobija MODEL-SEMANTIČKOG vlasnika: obaveznu provjeru
`exactly_one_option_correct`, svrstanu u `MODEL_ONLY_BLOCKING_CHECKS`.

OVAJ FAJL NE TVRDI DETERMINISTIČKI DOKAZ. Testovi mjere UGOVOR (šemu, matricu
autoriteta, promptove, ponašanje odluke) — nikad da Python razumije bosansku
prozu. Zamrznuti loši slučajevi služe kao fikstura ugovora i vlasništva.

ZERO poziva modela: sve je čist deterministički kod ili FakeLLM.
"""
import pytest

from matbot import mcq_integrity, option_equivalence
from matbot.tutor import package_preflight, reviewer_authority
from matbot.tutor import prompts as tutor_prompts
from matbot.tutor.lesson_context import build
from matbot.tutor.schema import (ReviewerChecks, UnifiedOutputError,
                                 validate_reviewer)
from tests.conftest import (make_reviewer_checks, make_reviewer_final,
                            make_task_payload, make_tutor_draft)

CHECK = "exactly_one_option_correct"

# --- BAD 1: doslovno objavljeni paket iz kanonskog FINAL40 -------------------
BAD1_QUESTION = (r"Relacija je zadana tačkama: $(1,2)$, $(2,3)$, $(3,2)$. "
                 r"Predstavlja li ovaj skup tačaka funkciju?")
BAD1_OPTIONS = [
    r"Ne — zato što se vrijednost $y=2$ ponavlja, relacija nije funkcija.",
    r"Ne — neki $x$ se pojavljuje dvaput pa ima više različitih vrijednosti $y$.",
    r"Da — svaki različit $x$ u skupu ima tačno jedan odgovarajući $y$, pa je "
    r"relacija funkcija.",
    r"Da — relacija je funkcija, i dopušteno je da se ista vrijednost $y$ "
    r"ponavlja za različite $x$.",
]
BAD1_MARKED = 2

# --- BAD 2: ista klasa, druga tema i druge riječi (ne prepoznaje fiksturu) ---
BAD2_QUESTION = (r"Dat je broj $24$. Da li je $24$ djeljiv sa $6$?")
BAD2_OPTIONS = [
    r"Ne — $24$ nije djeljivo sa $6$ jer je $24$ paran broj.",
    r"Ne — ostatak pri dijeljenju sa $6$ je $2$.",
    r"Da — $24 = 6 \cdot 4$, pa je djeljiv sa $6$.",
    r"Da — $24$ je djeljiv i sa $2$ i sa $3$, pa je djeljiv sa $6$.",
]
BAD2_MARKED = 2

# --- BAD 3: treća tema, dvije nezavisno tačne proze --------------------------
BAD3_QUESTION = (r"Trougao ima stranice $3$, $4$ i $5$. Da li je taj trougao "
                 r"pravougli?")
BAD3_OPTIONS = [
    r"Ne — zbir dvije kraće stranice veći je od najduže, pa nije pravougli.",
    r"Ne — pravougli trougao mora imati dvije jednake stranice.",
    r"Da — vrijedi $3^2 + 4^2 = 5^2$, pa je po obratu Pitagorine teoreme pravougli.",
    r"Da — stranice $3$, $4$, $5$ čine poznatu Pitagorinu trojku, pa je pravougli.",
]
BAD3_MARKED = 2

BAD_CASES = [
    pytest.param(BAD1_QUESTION, BAD1_OPTIONS, BAD1_MARKED, id="BAD1-live-FW-F03"),
    pytest.param(BAD2_QUESTION, BAD2_OPTIONS, BAD2_MARKED, id="BAD2-divisibility"),
    pytest.param(BAD3_QUESTION, BAD3_OPTIONS, BAD3_MARKED, id="BAD3-pythagoras"),
]

# --- GOOD kontrole -----------------------------------------------------------
GOOD_ONE_YES = [
    r"Ne, nije funkcija: vrijednost $y=5$ se ponavlja za različite $x$.",
    r"Ne, nije funkcija: postoje dva para sa istom $x$-koordinatom.",
    r"Da, jeste funkcija: svaki $x$ iz skupa $\{1,2,3\}$ ima tačno jednu vrijednost $y$.",
    r"Ne, nije funkcija: za $x=2$ nije navedena nijedna vrijednost $y$.",
]
GOOD_SHARED_VOCAB = [
    r"Da — svaki $x$ ima tačno jedan $y$, pa jeste funkcija.",
    r"Ne — svaki $y$ mora biti različit da bi bila funkcija, a ovdje nije.",
    r"Ne — funkcija mora imati jednak broj različitih $x$ i različitih $y$.",
    r"Ne — funkcija ne smije imati više od dvije tačke.",
]
GOOD_PARTLY_TRUE_DISTRACTOR = [
    r"Da — vrijedi $3^2+4^2=5^2$, pa je trougao pravougli.",
    r"Ne — $3^2+4^2=25$, ali to znači da je trougao jednakokraki.",
    r"Ne — najduža stranica je $5$, pa je trougao tupougli.",
    r"Ne — zbir svih stranica je $12$, pa trougao ne postoji.",
]


def _turn_lesson():
    return build(6, "6-10-007")


def _task(question, options, marked):
    context = _turn_lesson()
    payload = make_task_payload(text=question, options=list(options),
                                correct_option_index=marked,
                                expected=options[marked])
    return payload.model_copy(update={
        "selected_lesson_id": context.topic_id,
        "selected_lesson_title": context.title})


# ===========================================================================
# A. ŠEMA — polje postoji i OBAVEZNO je
# ===========================================================================

def test_the_field_exists_and_is_mandatory():
    assert CHECK in ReviewerChecks.model_fields
    field = ReviewerChecks.model_fields[CHECK]
    assert field.annotation is bool
    assert field.is_required(), "polje ne smije imati podrazumijevanu vrijednost"


def test_an_omitted_field_fails_schema_validation():
    """Izostavljanje pada zatvoreno — nikad tiho ne prolazi kao `true`."""
    values = make_reviewer_checks().model_dump()
    values.pop(CHECK)
    with pytest.raises(Exception):
        ReviewerChecks(**values)


# ===========================================================================
# B. AUTORITET — model-only blokirajuća, nikad deterministička ni savjetodavna
# ===========================================================================

def test_the_field_is_model_only_blocking():
    assert CHECK in reviewer_authority.MODEL_ONLY_BLOCKING_CHECKS
    assert CHECK not in reviewer_authority.DETERMINISTIC_AUTHORITY_CHECKS
    assert CHECK not in reviewer_authority.ADVISORY_CHECKS
    assert CHECK not in reviewer_authority.STRUCTURAL_CHECKS
    assert CHECK not in reviewer_authority.AUTHORITATIVE_VALIDATOR


def test_a_false_value_is_a_blocking_failure_and_never_only_diagnostic():
    checks = make_reviewer_checks(**{CHECK: False})
    assert CHECK in reviewer_authority.blocking_failed_checks(checks)
    assert CHECK not in reviewer_authority.diagnostic_failed_checks(checks)


# ===========================================================================
# C + D. NETAČNA VRIJEDNOST OBARA I `approve` I `correct`
# ===========================================================================

@pytest.mark.parametrize("decision", ["approve", "correct"])
def test_a_false_check_blocks_both_successful_decisions(decision):
    """`correct` NE SMIJE biti rupa: živi defekt je nastao baš u ispravci."""
    draft = make_tutor_draft(intent="generate_task")
    reviewer = make_reviewer_final(
        decision=decision, final=draft,
        checks=make_reviewer_checks(**{CHECK: False}))
    with pytest.raises(UnifiedOutputError) as error:
        validate_reviewer(reviewer, draft)
    assert CHECK in str(error.value)


# ===========================================================================
# E. `fail_closed` OSTAJE MOGUĆ
# ===========================================================================

def test_fail_closed_remains_valid_with_a_false_check():
    """Nesiguran paket mora ostati IZRAZIV — inače nema sigurnog izlaza."""
    reviewer = make_reviewer_final(
        decision="fail_closed", fail_reason_code="ambiguous_task",
        checks=make_reviewer_checks(**{CHECK: False}))
    validate_reviewer(reviewer, make_tutor_draft(intent="generate_task"))


# ===========================================================================
# F. `true` NIJE ZASTAVICA „PAKET JE DOBAR"
# ===========================================================================

def test_a_true_value_does_not_override_another_blocking_check():
    draft = make_tutor_draft(intent="generate_task")
    reviewer = make_reviewer_final(
        decision="correct", final=draft,
        checks=make_reviewer_checks(**{CHECK: True, "math_correct": False}))
    with pytest.raises(UnifiedOutputError) as error:
        validate_reviewer(reviewer, draft)
    assert "math_correct" in str(error.value)


def test_a_true_value_does_not_override_a_deterministic_finding():
    """Samoprijava nikad ne spašava paket koji serverski validator obori."""
    context = _turn_lesson()
    duplicate = _task(r"Izračunaj $2+2$.", ["$4$", "$4$", "$5$", "$6$"], 0)
    issues = package_preflight.collect_package_issues(
        duplicate, contract=context.semantic_contract,
        practice_contract=context.practice_contract,
        practice_policy=context.practice_policy)
    assert any(issue.code == "duplicate_option_text" for issue in issues)


# ===========================================================================
# FALSIFIKACIJA — blokada stvarno dolazi iz matrice autoriteta
# ===========================================================================

def test_the_block_comes_from_the_authority_classification(monkeypatch):
    """Da polje nije u blokirajućoj klasi, isti paket bi prošao presudu."""
    draft = make_tutor_draft(intent="generate_task")
    reviewer = make_reviewer_final(
        decision="correct", final=draft,
        checks=make_reviewer_checks(**{CHECK: False}))
    with pytest.raises(UnifiedOutputError):
        validate_reviewer(reviewer, draft)

    demoted = frozenset(
        name for name in reviewer_authority.MODEL_ONLY_BLOCKING_CHECKS
        if name != CHECK)
    monkeypatch.setattr(reviewer_authority, "MODEL_ONLY_BLOCKING_CHECKS", demoted)
    validate_reviewer(reviewer, draft)          # sada prolazi → test je stvaran


# ===========================================================================
# ZAŠTO SERVER OVO NE MOŽE DOKAZATI (poštenje dokaza)
# ===========================================================================

@pytest.mark.parametrize("question,options,marked", BAD_CASES)
def test_no_deterministic_owner_can_prove_these_cases(question, options, marked):
    """Deterministi dokazuju EKVIVALENCIJU, ne ISTINITOST.

    Kad bi ovo počelo da pada, klasa bi postala serverska i model-sloj bi bio
    suvišan; test to čini vidljivim umjesto da tiho zastari."""
    assert option_equivalence.find_textual_duplicate_pairs(options) == []
    assert option_equivalence.find_equivalent_option_pairs(options) == []
    assert mcq_integrity.publication_failure(
        question, options, marked, options[marked])[0] == ""


@pytest.mark.parametrize("question,options,marked", BAD_CASES)
def test_a_reviewer_reporting_the_defect_cannot_publish_it(question, options, marked):
    draft = make_tutor_draft(intent="generate_task",
                             new_task=_task(question, options, marked))
    reviewer = make_reviewer_final(
        decision="correct", final=draft,
        checks=make_reviewer_checks(**{CHECK: False}))
    with pytest.raises(UnifiedOutputError):
        validate_reviewer(reviewer, draft)


# ===========================================================================
# GOOD KONTROLE — odobrenje ostaje moguće
# ===========================================================================

@pytest.mark.parametrize("options,marked,why", [
    (GOOD_ONE_YES, 2, "jedno potvrdno i tri netačna odrična obrazloženja"),
    (GOOD_SHARED_VOCAB, 0, "dijele rječnik, ali je tačna samo jedna"),
    (GOOD_PARTLY_TRUE_DISTRACTOR, 0,
     "distraktor nosi tačan međukorak, ali netačan zaključak"),
])
def test_good_option_sets_remain_publishable(options, marked, why):
    draft = make_tutor_draft(
        intent="generate_task",
        new_task=_task(r"Kontrolno pitanje.", options, marked))
    validate_reviewer(
        make_reviewer_final(decision="correct", final=draft,
                            checks=make_reviewer_checks(**{CHECK: True})),
        draft)


def test_the_check_is_not_about_repeated_first_words():
    """Dvije opcije smiju počinjati istom riječi ako je tačna samo jedna."""
    options = [r"Da — jer $2+2=4$.", r"Da — jer $2+2=5$.",
               r"Ne — jer $2+2=4$.", r"Ne — jer $2 \cdot 2 = 5$."]
    draft = make_tutor_draft(intent="generate_task",
                             new_task=_task(r"Da li je $2+2=4$?", options, 0))
    validate_reviewer(
        make_reviewer_final(decision="correct", final=draft,
                            checks=make_reviewer_checks(**{CHECK: True})),
        draft)


# ===========================================================================
# PROMPT UGOVOR
# ===========================================================================

def test_the_rule_ships_to_the_reviewer_only():
    context = _turn_lesson()
    rule = tutor_prompts._REVIEWER_OPTION_UNIQUENESS_RULE
    assert rule in tutor_prompts.build_reviewer_instructions(context)
    assert rule not in tutor_prompts.build_tutor_instructions(context)
    assert rule not in tutor_prompts.build_help_instructions(context)


def test_the_rule_states_the_complete_option_invariant():
    rule = tutor_prompts._REVIEWER_OPTION_UNIQUENESS_RULE
    assert CHECK in rule
    assert "COMPLETE mathematical meaning" in rule
    assert "Exactly one complete option" in rule
    # Ključna razlika: različite riječi NE znače da su obje netačne.
    assert "DO NOT HAVE TO LOOK ALIKE" in rule
    assert "fail_closed" in rule


def test_the_rule_demands_re_evaluation_after_a_repair():
    """Živi defekt je nastao u RECENZENTOVOJ ispravci — ovo je srž pravila."""
    rule = tutor_prompts._REVIEWER_OPTION_UNIQUENESS_RULE
    assert "AFTER ANY REPAIR" in rule
    assert "RE-EVALUATE this from scratch" in rule
    assert "FINAL package" in rule


def test_the_rule_names_the_false_positive_boundaries():
    rule = tutor_prompts._REVIEWER_OPTION_UNIQUENESS_RULE
    assert "Do NOT set this false merely because" in rule
    assert "share vocabulary" in rule
    assert "true intermediate" in rule


def test_the_check_semantics_rule_separates_the_two_option_claims():
    rule = tutor_prompts._REVIEWER_CHECK_SEMANTICS_RULE
    assert CHECK in rule
    assert "asks ONLY about the marked option" in rule


def test_the_tutor_gets_the_matching_mcq_rule():
    tutor = tutor_prompts.build_tutor_instructions(_turn_lesson())
    assert "dva različita, oba tačna obrazloženja" in tutor
    # „tačno jedna OPCIJA" se ne smije pobrkati s „tačno jedno RJEŠENJE".
    assert "nije isto što i" in tutor


@pytest.mark.parametrize("grade,lesson", [
    (6, "6-10-007"), (7, "7-02-019"), (8, "8-02-002"), (9, "9-04-014")])
def test_the_rule_reaches_the_reviewer_for_every_grade(grade, lesson):
    context = build(grade, lesson)
    assert (tutor_prompts._REVIEWER_OPTION_UNIQUENESS_RULE
            in tutor_prompts.build_reviewer_instructions(context))


def test_the_rule_stays_compact():
    assert len(tutor_prompts._REVIEWER_OPTION_UNIQUENESS_RULE) < 2200


# ===========================================================================
# IZVJEŠTAJNO POŠTENJE
# ===========================================================================

def test_no_evaluator_check_claims_to_prove_prose_truth():
    from tools.practice_eval import checks as check_lib

    assert CHECK not in check_lib.known_check_names()
