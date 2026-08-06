"""Rubrika nivoa 1–3 mora prihvatiti stvaran uvodni zadatak.

ŽIVI NALAZ (tri kampanje, 110 pokušaja generisanja u posljednjoj):
24 paketa (21,8 %) odbijeno je zbog dokaza težine — 22 na ciljanom nivou 1.
Raspodjela dokaza odbijenih nivoa 1:

    14x  steps=1 cond=1 ops=1 repr=1   ← jedan korak, jedan uslov, jedna
                                          operacija i JEDNA promjena zapisa
     4x  steps=1 cond=1 ops=2 repr=0   ← jedan korak, dvije operacije
     2x  steps=1 cond=1 ops=3 repr=0   ← tri operacije
     2x  steps=1 cond=2 ops=1..2       ← dva nezavisna uslova

Prvih 18 su najminimalniji mogući zadaci: „Koji razlomak je jednak $0,5$?“ ima
tačno jednu promjenu zapisa — i ta promjena JESTE vještina lekcije. Rubrika ih
je odbijala jer je nivo 1 tražio `representation_change_count == 0` i
`operation_count <= 1`.

Kalibrišu se TAČNO DVA praga nivoa 1 (`repr <= 1`, `ops <= 2`). Sve ostalo
ostaje: jedan korak rezonovanja, jedan uslov, i svaka zastavica (objašnjenje,
konstrukcija, dokaz, kombinovanje koncepata) i dalje diskvalifikuje nivo 1.
Poređenje je od Faze 4C izuzetak: samo po sebi ne diskvalifikuje, ali uz bilo
koji dodatni korak/uslov i dalje pada. Preostala 4 živa slučaja ostaju blokirana i to je namjerno.

Nivoi 2 i 3 se NE diraju: A11 i B50 su na ciljanom nivou 2 prijavili
steps=3/ops=3 — to su stvarni zadaci nivoa 3 pogrešno označeni kao 2, i guard
ih ispravno blokira.
"""
import pytest

from matbot.tutor.schema import (DifficultyEvidence, difficulty_evidence_errors,
                                 validate_difficulty_evidence)
from tests.conftest import make_task_payload


def ev(steps=1, cond=1, ops=1, repr_changes=0, explanation=False, comparison=False,
       construction=False, proof=False, combines=False):
    return DifficultyEvidence(
        reasoning_steps=steps, condition_count=cond, operation_count=ops,
        representation_change_count=repr_changes, requires_explanation=explanation,
        requires_comparison=comparison, requires_construction=construction,
        requires_proof_or_justification=proof, combines_concepts=combines)


# --- STVARNI DOKAZI IZ ŽIVIH KAMPANJA -------------------------------------
LIVE_LEVEL1_MUST_NOW_PASS = {
    "A10/A14/A15/B06/B11/B12/B26/B27/B28/B29/B35/B48/B54/B60": ev(1, 1, 1, 1),
    "A36/B09/B10/B16": ev(1, 1, 2, 0),
}
LIVE_LEVEL1_MUST_STAY_BLOCKED = {
    "A18/B49 (tri operacije)": ev(1, 1, 3, 0),
    "A33 (dva uslova)": ev(1, 2, 1, 0),
    "B13 (dva uslova, dvije operacije)": ev(1, 2, 2, 0),
}
LIVE_LEVEL2_MUST_STAY_BLOCKED = {
    "A11 (stvarni nivo 3)": ev(3, 2, 3, 1),
    "B50 (stvarni nivo 3)": ev(3, 1, 3, 1, combines=True),
}


@pytest.mark.parametrize("label,evidence", sorted(LIVE_LEVEL1_MUST_NOW_PASS.items()))
def test_live_introductory_tasks_are_accepted_at_level_1(label, evidence):
    assert difficulty_evidence_errors(evidence, 1) == (), label


@pytest.mark.parametrize("label,evidence", sorted(LIVE_LEVEL1_MUST_STAY_BLOCKED.items()))
def test_live_over_complex_tasks_stay_blocked_at_level_1(label, evidence):
    assert difficulty_evidence_errors(evidence, 1), label


@pytest.mark.parametrize("label,evidence", sorted(LIVE_LEVEL2_MUST_STAY_BLOCKED.items()))
def test_live_level_three_shapes_stay_blocked_at_level_2(label, evidence):
    assert difficulty_evidence_errors(evidence, 2), label


# --- REPREZENTATIVNI ZADACI PO NIVOU --------------------------------------

LEVEL1_ACCEPTS = {
    "jedna direktna primjena jednog pravila": ev(1, 1, 1, 0),
    "jedna jednostavna aritmetička operacija": ev(1, 1, 1, 0),
    "osnovno uvrštavanje pa račun": ev(1, 1, 2, 0),
    "jednostavan MCQ s četiri opcije": ev(1, 1, 1, 0),
    "trivijalna promjena zapisa (0,5 → 1/2)": ev(1, 1, 1, 1),
    "minimalno poređenje (živi gate acd8f5c)": ev(1, 1, 1, 0, comparison=True),
}

LEVEL1_REJECTS = {
    "više povezanih koncepata": ev(1, 1, 1, 0, combines=True),
    "dokazivanje": ev(1, 1, 1, 0, proof=True),
    "konstrukcija": ev(1, 1, 1, 0, construction=True),
    "traži objašnjenje": ev(1, 1, 1, 0, explanation=True),
    # Faza 4C: SAMO poređenje više ne diskvalifikuje nivo 1 (lekcija čija je
    # vještina upravo poređenje inače ne bi imala nijedan zadatak nivoa 1).
    # Poređenje UZ dodatni korak i dalje pada.
    "poređenje uz dodatni korak rezonovanja": ev(2, 1, 1, 0, comparison=True),
    "poređenje uz drugi uslov": ev(1, 2, 1, 0, comparison=True),
    "višefazni problem": ev(3, 1, 1, 0),
    "više nezavisnih uslova": ev(1, 3, 1, 0),
    "napredna reprezentacijska transformacija": ev(1, 1, 1, 2),
}

LEVEL2_ACCEPTS = {
    "umjereno više koraka": ev(2, 1, 2, 0),
    "jedna smislena kombinacija bliskih vještina": ev(2, 2, 2, 0),
    "standardna primjena u kontekstu": ev(1, 1, 2, 1),
    "jednostavno poređenje": ev(1, 1, 1, 0, comparison=True),
}

LEVEL2_REJECTS = {
    "stvarni nivo 3 — tri koraka i tri operacije": ev(3, 3, 3, 1),
    "traži dokaz": ev(2, 2, 2, 0, proof=True),
    "traži konstrukciju": ev(2, 2, 2, 0, construction=True),
    "dvije promjene zapisa": ev(2, 2, 2, 2),
}

LEVEL3_ACCEPTS = {
    "tri povezana koraka": ev(3, 2, 3, 1),
    "traži dokaz": ev(2, 2, 2, 0, proof=True),
    "traži konstrukciju": ev(2, 1, 2, 0, construction=True),
    "napredna promjena zapisa": ev(2, 2, 2, 2),
}


@pytest.mark.parametrize("label,evidence", sorted(LEVEL1_ACCEPTS.items()))
def test_level_1_accepts_a_direct_introductory_task(label, evidence):
    assert difficulty_evidence_errors(evidence, 1) == (), label


@pytest.mark.parametrize("label,evidence", sorted(LEVEL1_REJECTS.items()))
def test_level_1_rejects_anything_beyond_one_direct_application(label, evidence):
    assert difficulty_evidence_errors(evidence, 1), label


@pytest.mark.parametrize("label,evidence", sorted(LEVEL2_ACCEPTS.items()))
def test_level_2_accepts_a_bounded_combination(label, evidence):
    assert difficulty_evidence_errors(evidence, 2) == (), label


@pytest.mark.parametrize("label,evidence", sorted(LEVEL2_REJECTS.items()))
def test_level_2_rejects_a_genuine_level_3_task(label, evidence):
    assert difficulty_evidence_errors(evidence, 2), label


@pytest.mark.parametrize("label,evidence", sorted(LEVEL3_ACCEPTS.items()))
def test_level_3_accepts_an_advanced_task(label, evidence):
    assert difficulty_evidence_errors(evidence, 3) == (), label


def test_negative_counts_are_always_rejected():
    for level in (1, 2, 3):
        assert difficulty_evidence_errors(ev(-1, 1, 1, 0), level)


def test_unknown_target_level_is_rejected():
    assert difficulty_evidence_errors(ev(), 4)
    assert difficulty_evidence_errors(ev(), 0)


# --- SVA TRI SLOJA MORAJU DATI ISTI ZAKLJUČAK ------------------------------

@pytest.mark.parametrize("label,evidence", sorted(LEVEL1_ACCEPTS.items()))
def test_all_three_layers_agree_for_an_accepted_level_1_task(label, evidence):
    """Tutor preflight, recenzentova invarijanta i objava dijele JEDNU funkciju."""
    from matbot.tutor import package_preflight

    task = make_task_payload().model_copy(update={
        "target_difficulty_level": 1, "difficulty_evidence": evidence})
    assert difficulty_evidence_errors(evidence, 1) == ()                 # sloj 1: preflight
    validate_difficulty_evidence(task)                                    # sloj 3: objava
    codes = [issue.code for issue in package_preflight.collect_package_issues(task)]
    assert "difficulty_evidence_outside_target" not in codes


@pytest.mark.parametrize("label,evidence", sorted(LEVEL1_REJECTS.items()))
def test_all_three_layers_agree_for_a_rejected_level_1_task(label, evidence):
    from matbot.tutor import package_preflight
    from matbot.tutor.schema import UnifiedOutputError

    task = make_task_payload().model_copy(update={
        "target_difficulty_level": 1, "difficulty_evidence": evidence})
    assert difficulty_evidence_errors(evidence, 1)
    with pytest.raises(UnifiedOutputError):
        validate_difficulty_evidence(task)
    codes = [issue.code for issue in package_preflight.collect_package_issues(task)]
    assert "difficulty_evidence_outside_target" in codes


# --- PROMPT: „TEŽE“ JE JEDAN OGRANIČEN KORAK ------------------------------

def test_prompt_states_that_harder_is_one_bounded_step():
    """A11/B50: na traženi nivo 2 model je napravio zadatak nivoa 3."""
    from matbot.tutor import lesson_context as lesson_context_module
    from matbot.tutor import prompts as tutor_prompts

    context = lesson_context_module.build(7, "7-04-008")
    for text in (tutor_prompts.build_tutor_instructions(context),
                 tutor_prompts.build_reviewer_instructions(context)):
        assert "ONE bounded step" in text
