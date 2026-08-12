"""STEPEN PROMJENLJIVE NA MODEL-PUTU — porodični detektor, lekcijski opseg.

ŽIVI QA NALAZ (direktor škole, „Izrazi s promjenljivim i brojna vrijednost
izraza“, 6-02-008): traženjem sve težih zadataka lekcija je dolazila do izraza
s $x^2$. Ugovor lekcije to je već zabranjivao (`max_variable_degree: 1`, uz
dokaz da stepenovanje u 6. razredu ne postoji ni u jednoj NPP stavki i da se
uvodi tek u 8.), a deterministički generator ga je poštovao — ali granica je
bila SAMO savjetodavna: porodični detektor nije postojao, pa je `detect`
vraćao UNSUPPORTED i model-put nije imao ništa što bi prekršaj oborilo.

OVAJ FAJL ZAKLJUČAVA:
  • prekršaj se DOKAZUJE i obara paket, u više notacija;
  • granica je LEKCIJSKA (`max_variable_degree` iz ugovora TE lekcije), pa
    lekcija čiji JESTE predmet stepenovanje ostaje netaknuta;
  • zakonit izraz se nikad ne obara, pa težina i dalje ima gdje da raste.
"""
import pytest

from matbot.semantics import contracts as semantic_contracts
from matbot.semantics import detectors

EXPRESSION_LESSON = "6-02-008"      # razred 6, „Izrazi s promjenljivim…“
# KONTROLA MORA BITI U ISTOJ PORODICI: lekcija iz druge porodice ide kroz drugi
# detektor, pa ne bi mogla otkriti razrednu (umjesto lekcijske) zabranu — prvi
# oblik ove kontrole je upravo zato preživio falsifikaciju F4.
SAME_FAMILY_POWER_LESSON = "8-07-009"   # „Kvadrat zbira i razlike“ (polynomial_basic)
OTHER_FAMILY_POWER_LESSON = "8-01-013"  # „Stepen sa cijelim izložiocem“


def _detect(lesson_id, text):
    return detectors.detect(semantic_contracts.contract_for(lesson_id), text)


# ---------------------------------------------------------------------------
# A) PREKRŠAJ SE DOKAZUJE — više notacija istog pojma
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Izračunaj vrijednost izraza $x^2 + 3$ za $x = 4$.",
    "Izračunaj vrijednost izraza $x^{2} + 3$ za $x = 4$.",
    "Izračunaj vrijednost izraza $x² + 3$ za $x = 4$.",
    "Izračunaj vrijednost izraza $2x^3 - 1$ za $x = 2$.",
    "Izračunaj vrijednost izraza $a^2 + b$ za $a = 3$ i $b = 1$.",
])
def test_variable_power_is_rejected_for_the_expression_lesson(text):
    result = _detect(EXPRESSION_LESSON, text)
    assert result.status == detectors.STATUS_FAIL, text
    assert result.code == detectors.CODE_VARIABLE_DEGREE_EXCEEDED
    assert result.blocking
    assert result.evidence["allowed_degree"] == 1


def test_variable_multiplied_by_itself_is_also_a_power():
    """`x · x` je stepen bez oznake stepena — ista zabrana.

    ZAKLJUČAK IZ KURIKULUMA, ne pretpostavka: ugovor lekcije traži da se
    promjenljiva pojavi SAMO na prvi stepen, a evidencija (KS_2018-0059,
    KS_2018-0074) traži isključivo „vrijednost izraza s promjenljivim“."""
    for text in ("Izračunaj $x \\cdot x + 1$ za $x = 3$.",
                 "Izračunaj $x \\times x$ za $x = 3$.",
                 "Izračunaj $x·x$ za $x = 3$."):
        result = _detect(EXPRESSION_LESSON, text)
        assert result.status == detectors.STATUS_FAIL, text
        assert result.evidence["degree"] == 2


# ---------------------------------------------------------------------------
# B) ZAKONIT IZRAZ SE NIKAD NE OBARA — težina ima gdje da raste
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Izračunaj vrijednost izraza $3x + 5$ za $x = 4$.",
    "Izračunaj vrijednost izraza $4a + 3b - 7$ za $a = 2$ i $b = 5$.",
    "Izračunaj vrijednost izraza $2(x + 3) - 5$ za $x = 7$.",
    "Izračunaj vrijednost izraza $12 - 3y + 2z$ za $y = 2$ i $z = 6$.",
])
def test_legal_linear_expressions_are_never_blocked(text):
    result = _detect(EXPRESSION_LESSON, text)
    assert result.status != detectors.STATUS_FAIL, text
    assert not result.blocking


def test_numeric_powers_and_units_are_outside_the_proof_boundary():
    """Mjeri se stepen PROMJENLJIVE; brojevni stepen i jedinica nisu to."""
    for text in ("Izračunaj $2^3 + 1$.", "Površina je $24$ cm$^2$."):
        assert _detect(EXPRESSION_LESSON, text).status != detectors.STATUS_FAIL, text


# ---------------------------------------------------------------------------
# C) GRANICA JE LEKCIJSKA, NE RAZREDNA
# ---------------------------------------------------------------------------

def test_a_same_family_lesson_about_powers_keeps_its_powers():
    """NAJVAŽNIJA KONTROLA: ista porodica, isti detektor, drugi ugovor.

    „Kvadrat zbira i razlike“ je polinomska lekcija kojoj je stepen 2 SAMA
    SUŠTINA. Prolazi kroz isti detektor kao pogođena lekcija, pa bi svaka
    razredna ili porodična (umjesto lekcijske) zabrana ovdje pukla — prvi
    oblik ove kontrole koristio je lekciju IZ DRUGE porodice i zato je
    preživio falsifikaciju F4."""
    contract = semantic_contracts.contract_for(SAME_FAMILY_POWER_LESSON)
    assert contract is not None
    assert contract.detector == "polynomial_basic"
    assert "max_variable_degree" not in dict(contract.parameters)
    for text in ("Izračunaj $(a + b)^2$.", "Zapiši $(x - 3)^2$ kao polinom.",
                 "Izračunaj $x^2 + 2xy + y^2$ za $x = 1$ i $y = 2$."):
        assert detectors.detect(contract, text).status != detectors.STATUS_FAIL, text


def test_a_lesson_about_powers_in_another_family_is_also_untouched():
    contract = semantic_contracts.contract_for(OTHER_FAMILY_POWER_LESSON)
    assert contract is not None
    for text in ("Izračunaj $x^5 \\cdot x^3$.", "Zapiši $2^{-3}$ kao razlomak."):
        assert detectors.detect(contract, text).status != detectors.STATUS_FAIL, text


def test_detector_is_inert_without_a_declared_degree_bound():
    """Bez deklarisane granice detektor NE nagađa — vraća UNSUPPORTED."""
    contract = semantic_contracts.contract_for(SAME_FAMILY_POWER_LESSON)
    result = detectors.detect(contract, "Izračunaj $x^9$.")
    assert result.status == detectors.STATUS_UNSUPPORTED
    assert not result.blocking


def test_only_lessons_that_declare_the_bound_are_constrained():
    """Zabrana ne smije biti razredna: pobrojane su lekcije koje je NOSE."""
    import json
    from pathlib import Path
    source = json.loads((Path(__file__).resolve().parent.parent / "data" /
                         "lesson_semantic_assignments.json").read_text(encoding="utf-8"))
    bounded = {row["lesson_id"] for row in source["assignments"]
               if str(row.get("parameters", {}).get("max_variable_degree", "")) == "1"}
    assert EXPRESSION_LESSON in bounded
    assert SAME_FAMILY_POWER_LESSON not in bounded
    assert OTHER_FAMILY_POWER_LESSON not in bounded
    # Ni jedna lekcija 8. razreda o stepenima ne smije nositi granicu 1.
    assert not any(lesson_id.startswith(("8-", "9-")) for lesson_id in bounded), bounded


# ---------------------------------------------------------------------------
# D) UGOVOR JE SADA IZVRŠIV, NE SAMO SAVJETODAVAN
# ---------------------------------------------------------------------------

def test_degree_bound_is_declared_as_an_enforced_parameter():
    contract = semantic_contracts.contract_for(EXPRESSION_LESSON)
    assert "max_variable_degree" in contract.enforced_parameters
    assert "max_variable_degree" not in contract.advisory_parameters


def test_detector_is_registered_for_the_family():
    contract = semantic_contracts.contract_for(EXPRESSION_LESSON)
    assert contract.detector in detectors.DETECTORS
