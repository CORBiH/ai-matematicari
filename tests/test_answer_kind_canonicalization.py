"""Kanonizacija VRSTE ODGOVORA (`matbot/answer_kind.py`).

Ovaj fajl je preživio povlačenje starog jednopozivnog motora (2026-08-14).
Ranije je uz ova pravila vozio i cio integracijski dio kroz
`validate_task_family` i legacy tabelu uvoda — i jedno i drugo je obrisano
zajedno s motorom, pa je ostalo ono što i dalje ima predmet.

ZAŠTO OVO MORA OSTATI POKRIVENO: `matbot/answer_kind.py` ne zove aktivni
server, nego ZVANIČNA KAPIJA IZDANJA (`scratchpad/run_difficulty_canary.py`),
koja njime NEZAVISNO provjerava objavljen odgovor. Da ovi testovi nestanu,
tiha greška u čitanju odgovora bi oslijepila kapiju, a kapija je posljednje
što stoji između modela i učenika.
"""
import pytest

from matbot.answer_kind import (canonical_answer_kind, detected_answer_kind,
                                is_bare_ordered_pair, is_fraction_option,
                                is_integer_option, parse_ordered_pair)


# ---------------------------------------------------------------------------
# 1) ČISTO PRAVILO — canonical_answer_kind
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("declared,correct_option,expected_canonical,expected_normalized", [
    # Živi canary slučaj: deklarisana oznaka opcije, stvarno cijeli broj.
    ("option_label", "138", "integer", True),
    ("option_label", "$138$", "integer", True),
    # Već dosljedna deklaracija se ne dira.
    ("integer", "138", "integer", False),
    # Deklaracija izostane — server je ionako izvodi sam.
    (None, "138", "integer", False),
    ("", "138", "integer", False),
    # Ostali mehanički prepoznatljivi tipovi.
    ("integer", "$0,5$", "decimal", True),
    ("fraction", "(2,3)", "ordered_pair", True),
    ("integer", "$\\frac{3}{4}$", "fraction", True),
    # NIJE mehanički prepoznatljivo → deklaracija ostaje, bez kanonizacije.
    ("integer", "A", "integer", False),
    ("option_label", "Nije djeljiv sa 9", "option_label", False),
    ("short_text", "Da", "short_text", False),
])
def test_canonical_answer_kind_rule(declared, correct_option, expected_canonical,
                                    expected_normalized):
    canonical, normalized = canonical_answer_kind(declared, correct_option)
    assert canonical == expected_canonical
    assert normalized is expected_normalized


def test_canonicalization_is_family_agnostic():
    """Isto pravilo za SVAKU porodicu koja može imati opcije — nema grananja
    po porodici, lekciji ni domenu."""
    for family in ("direct_computation", "solve_system", "fraction_operation",
                   "compare_or_order", "word_problem", "find_missing_value"):
        canonical, normalized = canonical_answer_kind("option_label", "138")
        assert (canonical, normalized) == ("integer", True), family


# ---------------------------------------------------------------------------
# 2) ČITANJE ZAPISA — ono na šta se kapija oslanja
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("$5$", "integer"),
    ("$-7$", "integer"),
    ("$2,5$", "decimal"),
    ("$\\frac{1}{2}$", "fraction"),
    ("$2\\frac{1}{3}$", "fraction"),
    ("$(3,2)$", "ordered_pair"),
    ("$x=3,\\ y=2$", "ordered_pair"),
    ("opcija a", None),
])
def test_detected_answer_kind_reads_only_the_notation(text, expected):
    assert detected_answer_kind(text) == expected


def test_prose_that_mentions_a_pair_is_not_an_ordered_pair():
    """Živi nalaz koji je ovaj predikat i uveo: „Par $(2,1)$ zadovoljava obje
    jednačine.“ je TVRDNJA, ne uređeni par. Labavija varijanta je jednom lažno
    odbila ispravan `verify_pair` zadatak."""
    assert is_bare_ordered_pair("$(3,2)$") is True
    assert is_bare_ordered_pair("Par $(2,1)$ zadovoljava obje jednačine.") is False


@pytest.mark.parametrize("text,expected", [
    ("$(3,2)$", (3, 2)),
    ("$(-3,2)$", (-3, 2)),
    ("$(0,5;-1,25)$", (0.5, -1.25)),
    ("$x=3,\\ y=2$", (3, 2)),
])
def test_ordered_pair_is_read_exactly_or_not_at_all(text, expected):
    pair = parse_ordered_pair(text)
    assert pair is not None
    assert (float(pair[0]), float(pair[1])) == expected


def test_ambiguous_pair_is_never_guessed():
    """Nula ili više parova → dvosmisleno; nikad se ne pogađa."""
    assert parse_ordered_pair("Rješenja su $(1,2)$ i $(3,4)$.") is None
    assert parse_ordered_pair("") is None


def test_option_shape_predicates():
    assert is_fraction_option("$\\frac{3}{4}$") is True
    assert is_fraction_option("$12$") is False
    assert is_integer_option("$12$") is True
    assert is_integer_option("$\\frac{3}{4}$") is False
