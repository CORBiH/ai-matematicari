"""Mašinerija `identify_error` kategorija: server-owned tekst i strukturno
izvođenje (zahtjevi 16–22).

STATUS FAZE 1 (poslije Live96): arhetip `identify_error` je ODGOĐEN — nijedan
uključen ugovor ga ne nudi dok server ne dobije K4 generator koji pogrešan
lanac KONSTRUIŠE sam. Mašinerija ispod (strukturno izvođenje kategorije,
projektni bosanski tekstovi, smjer greške) NIJE obrisana: to su podaci i
deterministi koje K4 koristi unaprijed — zato se i dalje testiraju direktno
nad verifiers.py, bez modela i bez pipeline-a.
"""
import pytest

from matbot.contracts import (archetypes, evidence as ev, generator, registry,
                              schema, verifiers)
from matbot.terminology import normalize_terminology

EQUAL = "6-04-009"
CONTRACTS = registry.load_all()

CATEGORIES = ["combined_denominators", "wrong_numerator", "wrong_operation", "wrong_product"]


def _steps(raw_steps):
    return tuple(ev.parse_node(step) for step in raw_steps)


# Lanac: 7/12 + 3/12 = 10/24 (sabrao i imenioce) = 5/12 (skratio pogrešan zapis).
CHAIN = _steps([
    {"op": "add", "args": [{"num": 7, "den": 12}, {"num": 3, "den": 12}]},
    {"num": 10, "den": 24},
    {"num": 5, "den": 12},
])


def _verify(steps, categories, correct_index, topic=EQUAL):
    return verifiers.verify_error_category(
        steps, list(categories), correct_index, CONTRACTS[topic].error_category_set
    )


# --- 16, 22: tekst dolazi iz projektnih predložaka --------------------------

def test_16_public_option_text_is_derived_from_validated_categories():
    rendered = verifiers.render_error_options(CATEGORIES)
    assert rendered == tuple(
        verifiers.ERROR_CATEGORY_LABELS[category] for category in CATEGORIES
    )


def test_16_every_derivable_category_has_a_project_label():
    missing = sorted(
        verifiers.DERIVABLE_ERROR_CATEGORIES - set(verifiers.ERROR_CATEGORY_LABELS)
    )
    assert not missing, f"kategorija bez projektnog teksta: {missing}"


def test_16_unlabelled_category_renders_nothing_instead_of_guessing():
    assert verifiers.render_error_options(["nepostojeca_kategorija"]) is None


def test_22_labels_are_project_owned_bosnian_and_terminology_clean():
    for category, label in verifiers.ERROR_CATEGORY_LABELS.items():
        assert label.strip() == label and label.endswith("."), category
        # Normalizator terminologije ne smije imati šta da mijenja — tekst je
        # već pisan projektnim rječnikom.
        assert normalize_terminology(label) == label, category
        assert "$" not in label, category


def test_22_labels_are_mutually_distinct():
    labels = list(verifiers.ERROR_CATEGORY_LABELS.values())
    assert len(set(labels)) == len(labels)


# --- 18, 19, 20, 21: semantika kategorija -----------------------------------

def test_18_duplicate_categories_reject():
    result = _verify(CHAIN, ["combined_denominators", "combined_denominators",
                             "wrong_operation", "wrong_product"], 0)
    assert result.code == "ambiguous_error_options"


def test_19_unknown_categories_reject():
    result = _verify(CHAIN, ["combined_denominators", "izmisljena_kategorija",
                             "wrong_operation", "wrong_product"], 0)
    assert result.code == "unknown_error_category"


def test_20_exactly_one_deterministically_correct_category_is_required():
    """Lanac bez ijedne greške pada — nema šta da se pronađe."""
    clean = _steps([
        {"op": "add", "args": [{"num": 7, "den": 12}, {"num": 3, "den": 12}]},
        {"num": 10, "den": 12},
    ])
    assert _verify(clean, CATEGORIES, 0).code == "no_demonstrated_error"


def test_20_two_inconsistent_steps_reject():
    double = _steps([
        {"op": "add", "args": [{"num": 7, "den": 12}, {"num": 3, "den": 12}]},
        {"num": 10, "den": 24},
        {"num": 9, "den": 24},
    ])
    assert _verify(double, CATEGORIES, 0).code == "multiple_defensible_errors"


def test_21_marked_index_is_server_derived():
    reordered = ["wrong_numerator", "combined_denominators",
                 "wrong_operation", "wrong_product"]
    assert _verify(CHAIN, reordered, 0).code == "marked_error_option_mismatch"

    right = _verify(CHAIN, reordered, 1)
    assert right.ok
    assert right.details["derived_category"] == "combined_denominators"


def test_20_category_outside_the_contract_rejects():
    """Ugovor lekcije i dalje ograničava KOJE greške smiju biti predmet.

    Lanac je greška pri SKRAĆIVANJU (izvedena kategorija `wrong_reduction`), pa
    ga lekcija o PROŠIRIVANJU mora odbiti — bez ijedne grane po lekciji."""
    reduction_error = _steps([{"num": 8, "den": 20}, {"num": 2, "den": 6}])
    result = verifiers.verify_error_category(
        reduction_error,
        ["wrong_reduction", "unequal_scaling", "wrong_operation", "wrong_product"],
        0,
        CONTRACTS["6-04-005"].error_category_set,
    )
    assert result.code == "error_category_outside_contract"


# --- Faza 1: identify_error je odgođen, ali podaci lekcija ostaju ------------

def test_identify_error_is_not_selectable_in_phase_1():
    """Bez K4 generatora identify_error ne smije biti ni u rotaciji ni u
    intent tabeli izvodljiv — sužen je PODACIMA, a assert_supported bi odbio
    svako širenje bez generatora."""
    assert "identify_error" not in generator.IMPLEMENTED_ARCHETYPES
    for topic_id, contract in CONTRACTS.items():
        assert "identify_error" not in contract.effective_archetypes, topic_id


def test_error_category_data_survives_for_k4():
    """error_category_set OSTAJE u podacima lekcija — suženje arhetipa ne smije
    tiho obrisati podatke koje će K4 generator koristiti."""
    assert CONTRACTS[EQUAL].error_category_set == (
        "combined_denominators", "wrong_numerator", "wrong_operation",
    )
    assert CONTRACTS["6-04-005"].error_category_set == (
        "unequal_scaling", "wrong_operation",
    )


def test_contract_with_unlabelled_category_fails_at_load(monkeypatch):
    monkeypatch.setitem(verifiers.ERROR_CATEGORY_LABELS, "wrong_product", "")
    contract = schema.replace(
        CONTRACTS[EQUAL], error_category_set=("wrong_product",))
    with pytest.raises(schema.ContractSchemaError, match="projektni tekst"):
        archetypes.assert_supported(contract)


def test_word_problem_is_not_selectable_for_an_enabled_pilot():
    """Arhetip bez determinističkog generatora ne smije biti izabran…"""
    for contract in CONTRACTS.values():
        assert "word_problem" not in contract.effective_archetypes
        assert "word_problem" not in contract.allowed_task_archetypes
        # …a njegovo odsustvo ne šalje lekciju na legacy.
        assert registry.practice_state(contract) == registry.STATE_ENGINE
        assert contract.effective_archetypes, "ugovor mora ostati upotrebljiv"
