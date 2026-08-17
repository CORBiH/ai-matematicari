# -*- coding: utf-8 -*-
"""ŽIVI RELEASE GATE (`release_gate_grade7_rotating`, lekcija o podudarnosti
trouglova — kriterij SSU): server je DOKAZIVAO duplikat tamo gdje ga nema.

    reviewer_final_mcq_integrity_rejection:
    decision=correct in_tutor_preflight=True unchanged=True
    semantically_duplicate_options: option IDs b and d (symbolic_commutative)

Recenzent je bio u pravu što nije „popravio“ nalaz: opcije `SUS` i `SSU` su
DVA RAZLIČITA kriterija podudarnosti i uklanjanje jedne od njih uništava
zadatak. Krivo je bilo mjerenje: `option_equivalence._tokenize` je svako slovo
pretvarao u zasebnu promjenljivu, pa je `SUS` postajao proizvod `S·U·S`, a
`SSU` proizvod `S·S·U` — komutativno JEDNAKI. Zbog toga cijela lekcija nije
mogla objaviti ispravan MCQ, i to na svakom pokušaju.

Ista greška je pogađala i oznake tačaka: `ABC`, `ACB`, `BAC` i `CAB` su svi
međusobno bili „dokazani duplikati“.

U ovom projektu se množenje UVIJEK piše sa `\\cdot` (matbot/rules.py), a niz
velikih slova je oznaka objekta (`AB`, `ABC`, `SUS`) — vidi
`mathsafe.prose_words_in_expression`, koja ih iz istog razloga izuzima. Zato je
niz od dva ili više VELIKIH slova jedan atomski simbol, a ne proizvod.
"""
import pytest

from matbot import option_equivalence as oe


def _pairs(options):
    return sorted((i, j) for i, j, _kind in
                  oe.find_equivalent_option_pairs_with_types(options))


# ---------------------------------------------------------------------------
# 1) ŽIVI SLUČAJ — kriteriji podudarnosti nisu duplikati
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("options", [
    ["SUS", "SSU", "USU", "SSS"],
    ["$SUS$", "$SSU$", "$USU$", "$SSS$"],
    ["SUS kriterij", "SSU kriterij", "USU kriterij", "SSS kriterij"],
    ["$SSS$", "$SUS$", "$USU$", "$SSU$"],
])
def test_congruence_criteria_are_not_duplicates(options):
    assert _pairs(options) == []


@pytest.mark.parametrize("a,b", [
    ("SUS", "SSU"),
    ("USU", "UUS"),
    ("ABC", "ACB"),
    ("ABC", "BAC"),
    ("AB", "BA"),
    ("$\\triangle ABC$", "$\\triangle ACB$"),
])
def test_ordered_uppercase_labels_are_not_proven_equal(a, b):
    assert not oe.options_are_equivalent(a, b)


def test_vertex_labelled_options_are_all_distinct():
    assert _pairs(["$ABC$", "$ACB$", "$BAC$", "$CAB$"]) == []


# ---------------------------------------------------------------------------
# 2) DOKAZANI DUPLIKATI I DALJE PADAJU (adversarijalno)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("options,expected", [
    (["SUS", "SUS", "USU", "SSS"], [(0, 1)]),                       # doslovno isti
    (["$ab$", "$ba$", "$a+b$", "$a-b$"], [(0, 1)]),                 # mala slova
    (["$a \\cdot b$", "$b \\cdot a$", "$a+b$", "$2a$"], [(0, 1)]),  # eksplicitno
    (["$AB \\cdot CD$", "$CD \\cdot AB$", "$AB$", "$CD$"], [(0, 1)]),
    (["$\\frac{1}{2}$", "$\\frac{2}{4}$", "$\\frac{1}{3}$", "$\\frac{3}{4}$"], [(0, 1)]),
    (["$12$", "$12$", "$13$", "$14$"], [(0, 1)]),
    (["$0,5$", "$\\frac{1}{2}$", "$0,25$", "$2$"], [(0, 1)]),
    (["$2 \\cdot AB$", "$AB \\cdot 2$", "$3 \\cdot AB$", "$AB$"], [(0, 1)]),
])
def test_real_duplicates_are_still_proven(options, expected):
    assert _pairs(options) == expected


@pytest.mark.parametrize("a,b", [
    ("$x + y$", "$y + x$"),
    ("$2 \\cdot x$", "$x \\cdot 2$"),
    ("$a\\sqrt{2}$", "$\\sqrt{2}a$"),
])
def test_commutativity_over_ordinary_variables_is_unchanged(a, b):
    assert oe.options_are_equivalent(a, b)


def test_uppercase_run_is_one_atom_in_the_canonical_tree():
    assert oe.canonicalize_expression("SUS") != oe.canonicalize_expression("SSU")
    assert oe.canonicalize_expression("SUS") == oe.canonicalize_expression("SUS")
    assert oe.canonicalize_expression("ab") == oe.canonicalize_expression("ba")
