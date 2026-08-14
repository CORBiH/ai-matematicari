r"""Odnos djelilac/sadržilac mora biti SERVERSKI dokazan, ne prepušten modelu.

ŽIVI BLOKER ZAVRŠNE PRIJEMNE KAMPANJE (6. razred, model-ruta). Objavljen je
zadatak „Koja tvrdnja tačno opisuje odnos brojeva $5$ i $20$?“ s OZNAČENIM
odgovorom

    „$5$ je sadržilac broja $20$ jer je $20=5\cdot4$.“

To je netačno: `sadržilac` znači višekratnik, pa iz $20=5\cdot4$ slijedi da je
20 sadržilac broja 5 — odnos je okrenut. Ista lekcija je u istoj kampanji
objavila i zadatak koji tu formulaciju tretira kao GREŠKU koju treba ispraviti.

ZAŠTO GA NIJEDAN POSTOJEĆI VALIDATOR NIJE UHVATIO: greška je u značenju dva
stručna termina, ne u aritmetici — `mcq_integrity` nema šta riješiti,
`option_equivalence` vidi četiri različita teksta, `mathcheck` vidi
„$20=5\cdot4$“ što je brojevno tačno.

Odnos je egzaktno provjerljiv cjelobrojnom aritmetikom, pa je od ove faze
serverski dokazan.
"""
import json
import re
from pathlib import Path

import pytest

from matbot.semantics import contracts as semantic_contracts
from matbot.semantics import detectors

ROOT = Path(__file__).resolve().parent.parent
LESSON = "6-03-001"
HISTORICAL_BLOCKER = r"$5$ je sadržilac broja $20$ jer je $20=5\cdot4$."


def _contract():
    return semantic_contracts.contract_for(LESSON)


def _status(answer_text):
    return detectors._detect_divisor_multiple(_contract(), "", answer_text).status


# ---------------------------------------------------------------------------
# 1. TAČNO ONAJ PAKET KOJI JE PROŠAO MORA SADA PASTI
# ---------------------------------------------------------------------------

def test_the_historical_release_blocker_is_now_rejected():
    result = detectors._detect_divisor_multiple(_contract(), "", HISTORICAL_BLOCKER)
    assert result.status == detectors.STATUS_FAIL
    assert result.code == detectors.CODE_FALSE_DIVISOR_MULTIPLE_CLAIM
    assert result.blocking


def test_the_blocker_is_rejected_through_the_public_registry_too():
    """Ne samo direktno — i kroz `detect`, kojim ga zove preflight i objava."""
    result = detectors.detect(_contract(),
                              "Koja tvrdnja tačno opisuje odnos brojeva $5$ i $20$?",
                              answer_text=HISTORICAL_BLOCKER)
    assert result.status == detectors.STATUS_FAIL
    assert result.code == detectors.CODE_FALSE_DIVISOR_MULTIPLE_CLAIM


def test_the_second_campaign_example_is_also_false():
    """Ista lekcija je ovu formulaciju sama tretirala kao grešku."""
    assert _status(r"$4$ je sadržilac broja $20$") == detectors.STATUS_FAIL


# ---------------------------------------------------------------------------
# 2. EGZAKTNA SEMANTIKA
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    r"$5$ je djelilac broja $20$",
    r"$4$ je djelilac broja $20$",
    r"$20$ je sadržilac broja $5$",
    r"$20$ je višekratnik broja $5$",
    r"$5$ je djelitelj broja $20$",
    r"$3$ je djelilac broja $12$",
    r"$20$ je sadržilac broja $5$ jer je $20=5\cdot4$.",
])
def test_true_relations_pass(text):
    assert _status(text) == detectors.STATUS_PASS


@pytest.mark.parametrize("text", [
    r"$5$ je sadržilac broja $20$",
    r"$20$ je djelilac broja $5$",
    r"$6$ je djelilac broja $20$",
    r"$3$ je sadržilac broja $12$",
    r"$12$ je djelilac broja $3$",
    r"$4$ je višekratnik broja $20$",
])
def test_false_relations_fail(text):
    assert _status(text) == detectors.STATUS_FAIL


def test_divisor_and_multiple_are_exact_integer_arithmetic():
    for a, b in ((5, 20), (4, 20), (3, 12), (7, 49), (6, 42)):
        assert _status(f"${a}$ je djelilac broja ${b}$") == detectors.STATUS_PASS
        assert _status(f"${b}$ je sadržilac broja ${a}$") == detectors.STATUS_PASS
        assert _status(f"${a}$ je sadržilac broja ${b}$") == detectors.STATUS_FAIL
        assert _status(f"${b}$ je djelilac broja ${a}$") == detectors.STATUS_FAIL


# ---------------------------------------------------------------------------
# 3. UNKNOWN NIKAD NE BLOKIRA
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "",
    r"$8$",
    r"$4$ nije ni djelilac ni sadržilac broja $20$",          # odričan oblik
    r"Broj $3$ je djelilac: $51 : 3 = 17$, bez ostatka",      # bez drugog broja
    r"Broj $80$ jeste sadržilac: $80 = 8 \cdot 10$",          # deterministički zapis
    r"$5$ nije povezan s brojem $20$ jer nisu jednaki.",
    r"Najveći zajednički djelilac je $6$.",
])
def test_unparseable_or_negated_claims_stay_unknown(text):
    result = detectors._detect_divisor_multiple(_contract(), "", text)
    assert result.status == detectors.STATUS_UNSUPPORTED
    assert not result.blocking


def test_a_negation_is_never_read_as_a_positive_claim():
    """Potvrdno čitanje odričnog oblika bilo bi obrnuto od značenja."""
    assert detectors.divisor_multiple_claims(
        r"$4$ nije ni djelilac ni sadržilac broja $20$") == []


# ---------------------------------------------------------------------------
# 4. OBRAZLOŽENJE
# ---------------------------------------------------------------------------

def test_a_product_reason_that_supports_the_relation_passes():
    assert _status(r"$20$ je sadržilac broja $5$ jer je $20=5\cdot4$.") \
        == detectors.STATUS_PASS


def test_a_product_reason_with_wrong_arithmetic_fails():
    assert _status(r"$20$ je sadržilac broja $5$ jer je $20=5\cdot3$.") \
        == detectors.STATUS_FAIL


def test_an_uninterpretable_reason_does_not_turn_a_true_relation_into_a_failure():
    """Ne gradimo tumačenje prirodnog jezika: nečitljivo obrazloženje uz TAČAN
    odnos ostaje PASS, ne izmišlja se presuda."""
    assert _status(r"$20$ je sadržilac broja $5$ jer je manji od njega") \
        == detectors.STATUS_PASS


# ---------------------------------------------------------------------------
# 5. RECEPT ZA RECENZENTA
# ---------------------------------------------------------------------------

def test_the_finding_explains_the_relation_and_asks_for_a_rebuild():
    result = detectors._detect_divisor_multiple(_contract(), "", HISTORICAL_BLOCKER)
    reason = result.reason
    assert "DJELILAC" in reason and "SADRŽILAC" in reason
    assert "okrenut" in reason
    assert "SVE opcije" in reason
    assert not re.search(r"\b\d-\d\d-\d\d\d\b", reason), "ID lekcije u receptu"


# ---------------------------------------------------------------------------
# 6. NEMA GRANANJA PO LEKCIJI; SUSJEDNE GARANCIJE NETAKNUTE
# ---------------------------------------------------------------------------

def test_no_lesson_identity_in_the_detector_module():
    source = (ROOT / "matbot" / "semantics" / "detectors.py").read_text(encoding="utf-8")
    assert not re.search(r"\b\d-\d\d-\d\d\d\b", source)
    assert not re.search(r"lesson_id\s*==", source)


def test_only_this_family_is_judged_by_the_new_detector():
    """Lekcije drugih porodica moraju ostati netaknute."""
    compiled = json.loads((ROOT / "data" / "lesson_semantics.compiled.json")
                          .read_text(encoding="utf-8"))["lessons"]
    others = [lid for lid, e in compiled.items()
              if e["detector"] not in detectors.DETECTORS]
    assert others
    for lesson_id in others[:15]:
        result = detectors.detect(semantic_contracts.contract_for(lesson_id), "",
                                  answer_text=r"$5$ je sadržilac broja $20$")
        assert result.status == detectors.STATUS_UNSUPPORTED


def test_the_measure_dimension_detector_is_unchanged():
    assert detectors.DETECTORS["geometry_formula_2d"] is detectors._detect_measure_dimension
    assert detectors.DETECTORS["solid_geometry_direct"] is detectors._detect_measure_dimension
    assert detectors.unit_exponents("$12$ cm²") == {2}


def test_the_scientific_notation_blocker_stays_unwired():
    assert detectors._detect_scientific_notation not in detectors.DETECTORS.values()
    assert detectors._detect_answer_class not in detectors.DETECTORS.values()


def test_single_hint_diversity_and_config_are_untouched():
    from matbot import archetype_support, config, form_variants, release_config
    assert config.practice_single_hint_enabled() is True
    assert archetype_support._enabled() is True
    assert form_variants._enabled() is True
    assert release_config.REQUIRED_RELEASE_ENV["MATBOT_PRACTICE_PIPELINE"] == \
        "universal_two_call"


def test_publication_is_refused_when_the_marked_answer_reverses_the_relation():
    """Posljednja tačka prije mutacije sesije mora pasti ZATVORENO."""
    from matbot.tutor import pipeline
    context = type("Ctx", (), {"semantic_contract": _contract()})()
    with pytest.raises(pipeline.UnifiedOutputError) as excinfo:
        pipeline._reject_if_semantic_contract_violated(
            "Koja tvrdnja tačno opisuje odnos brojeva $5$ i $20$?", context,
            "označena opcija", answer_text=HISTORICAL_BLOCKER)
    assert detectors.CODE_FALSE_DIVISOR_MULTIPLE_CLAIM in str(excinfo.value)


def test_a_repaired_answer_passes_the_same_check():
    from matbot.tutor import pipeline
    context = type("Ctx", (), {"semantic_contract": _contract()})()
    pipeline._reject_if_semantic_contract_violated(
        "Koja tvrdnja tačno opisuje odnos brojeva $5$ i $20$?", context,
        "označena opcija", answer_text=r"$20$ je sadržilac broja $5$ jer je $20=5\cdot4$.")
