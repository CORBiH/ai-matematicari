r"""Zadatak mora stati u TRAŽENI nivo — ni ispod ni iznad njega.

ŽIVI RUN postStabilityFixes — svi difficulty rejectioni, s ručnom klasifikacijom:

    A11  L2  steps=3 cond=1 ops=3 repr=1 combines   → STVARNI NIVO 3
             tri koraka, tri operacije, promjena zapisa i kombinovanje
             koncepata; guard ispravno blokira
    B50  L2  steps=2 cond=3 ops=2 repr=0 combines   → STVARNI NIVO 3
             tri nezavisna uslova; nivo 2 dopušta najviše dva
    B50  L3  steps=3 cond=1 ops=3 repr=0            → paket zadovoljava nivo 3,
             ali recenzentov NEZAVISNI dokaz nije; odbijeno na dosljednosti

    A21  L1  steps=1 cond=1 ops=3 repr=0            → PRETEŠKO za nivo 1
             tri povezane operacije nisu jedna direktna primjena
    A28  L1  steps=2 cond=1 ops=2 repr=0            → PRETEŠKO za nivo 1
             dva koraka rezonovanja po definiciji nisu jedna primjena
    B38  L1  steps=1 cond=1 ops=4 repr=0            → PRETEŠKO za nivo 1
             četiri operacije
    B14  L1  steps=1 cond=2 ops=1 repr=1            → GRANIČNO, ostaje blokirano
             lekcija je „tekstualni zadatak sa sistemom“, a sistem po prirodi
             ima dva uslova. Prag se ipak NE diže: `condition_count >= 2` je
             ujedno i minimalni okidač nivoa 2, pa bi podizanje obrisalo
             razliku između nivoa 1 i 2 za CIJELI kurikulum.

ZAKLJUČAK: prag nivoa 1 se NE mijenja. Tri od četiri slučaja su stvarno
preteška, a četvrti se ne može popraviti bez rušenja granice L1/L2.

Popravlja se ono što jeste dokazano: recenzentova ispravka mora spustiti SVAKU
dimenziju koja krši traženi nivo (B14 je spustio korake i operacije, ali je
ostavio dva uslova), a „teže“ mora biti JEDAN ograničen korak, ne skok na
najteže što model može smisliti (A11, B50).
"""
import pytest

from matbot.tutor.schema import DifficultyEvidence, difficulty_evidence_errors


def ev(steps=1, cond=1, ops=1, repr_changes=0, explanation=False, comparison=False,
       construction=False, proof=False, combines=False):
    return DifficultyEvidence(
        reasoning_steps=steps, condition_count=cond, operation_count=ops,
        representation_change_count=repr_changes, requires_explanation=explanation,
        requires_comparison=comparison, requires_construction=construction,
        requires_proof_or_justification=proof, combines_concepts=combines)


# --- ŽIVI DOKAZI: šta OSTAJE blokirano i zašto -----------------------------

LIVE_LEVEL2_REJECTED = {
    "A11 — tri koraka, tri operacije, kombinovanje": ev(3, 1, 3, 1, combines=True),
    "B50 — tri nezavisna uslova": ev(2, 3, 2, 0, combines=True),
}

LIVE_LEVEL1_REJECTED = {
    "A21 — tri operacije": ev(1, 1, 3, 0),
    "A28 — dva koraka rezonovanja": ev(2, 1, 2, 0),
    "B38 — četiri operacije": ev(1, 1, 4, 0),
    "B14 — dva uslova (granično, prag se ne diže)": ev(1, 2, 1, 1),
}


@pytest.mark.parametrize("label,evidence", sorted(LIVE_LEVEL2_REJECTED.items()))
def test_a_level_three_shape_stays_blocked_at_level_two(label, evidence):
    assert difficulty_evidence_errors(evidence, 2), label
    # …i zaista pripada nivou 3.
    assert difficulty_evidence_errors(evidence, 3) == (), label


@pytest.mark.parametrize("label,evidence", sorted(LIVE_LEVEL1_REJECTED.items()))
def test_live_level_one_rejections_stay_rejected(label, evidence):
    """Prag nivoa 1 se NE diže — svaki od ova četiri je izvan jedne primjene."""
    assert difficulty_evidence_errors(evidence, 1), label


def test_level_one_threshold_is_unchanged_by_this_commit():
    """Granica ostaje: 1 korak, 1 uslov, <=2 operacije, <=1 promjena zapisa."""
    assert difficulty_evidence_errors(ev(1, 1, 2, 1), 1) == ()      # tačno na granici
    assert difficulty_evidence_errors(ev(2, 1, 2, 1), 1)            # korak preko
    assert difficulty_evidence_errors(ev(1, 2, 2, 1), 1)            # uslov preko
    assert difficulty_evidence_errors(ev(1, 1, 3, 1), 1)            # operacija preko
    assert difficulty_evidence_errors(ev(1, 1, 2, 2), 1)            # zapis preko


# --- REPREZENTATIVNI NIVO 2 KOJI MORA PROĆI --------------------------------

LEVEL2_ACCEPTS = {
    "dva povezana koraka": ev(2, 1, 2, 0),
    "dva uslova": ev(1, 2, 2, 0),
    "dvije operacije uz promjenu zapisa": ev(1, 1, 2, 1),
    "jednostavno poređenje": ev(1, 1, 1, 0, comparison=True),
    "traži kratko objašnjenje": ev(1, 1, 1, 0, explanation=True),
}


@pytest.mark.parametrize("label,evidence", sorted(LEVEL2_ACCEPTS.items()))
def test_a_bounded_level_two_task_is_accepted(label, evidence):
    assert difficulty_evidence_errors(evidence, 2) == (), label


LEVEL3_ONLY = {
    "tri koraka": ev(3, 1, 3, 0),
    "tri uslova": ev(2, 3, 2, 0),
    "traži dokaz": ev(2, 2, 2, 0, proof=True),
    "traži konstrukciju": ev(2, 2, 2, 0, construction=True),
    "dvije promjene zapisa": ev(2, 2, 2, 2),
}


@pytest.mark.parametrize("label,evidence", sorted(LEVEL3_ONLY.items()))
def test_a_real_level_three_task_never_passes_as_level_two(label, evidence):
    assert difficulty_evidence_errors(evidence, 2), label
    assert difficulty_evidence_errors(evidence, 3) == (), label


# --- PROMPT: ISPRAVKA MORA SPUSTITI SVAKU DIMENZIJU KOJA KRŠI --------------

def _instructions():
    from matbot.tutor import lesson_context as lesson_context_module
    from matbot.tutor import prompts as tutor_prompts
    context = lesson_context_module.build(7, "7-04-008")
    return (tutor_prompts.build_tutor_instructions(context),
            tutor_prompts.build_reviewer_instructions(context))


def test_reviewer_must_lower_every_violating_dimension():
    """B14: ispravka je spustila korake i operacije, ali ostavila dva uslova."""
    _tutor, reviewer = _instructions()
    assert "every dimension that violates" in reviewer.lower()


def test_both_prompts_state_that_harder_is_one_bounded_step():
    """A11 i B50: na traženi nivo 2 model je napravio zadatak nivoa 3."""
    tutor, reviewer = _instructions()
    assert "ONE bounded step" in tutor
    assert "ONE bounded step" in reviewer
