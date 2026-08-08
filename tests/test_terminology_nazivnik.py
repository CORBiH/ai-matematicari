"""Odluka K — kanonska terminologija razlomaka: nazivnik, ne imenilac.

Audit ovlašćenja pravila našao je ~120 determinističkih instrukcijskih
stringova s „imenilac“ nasuprot prompt-obaveznom „nazivnik“, i degradiranu
ASCII prozu (bez dijakritike) u porodici polinoma. Rješenje je dvoslojno:
kanonski izvor (motori sada pišu „nazivnik“ i punu dijakritiku) + postojeći
normalizator kao odbrana u dubini na SVAKOJ proizvedenoj površini.

VAŽNO: par imenilac→nazivnik je NORMALIZE-ONLY — nije „zabranjen termin“ za
repo-sken, jer zvanični kurikularni naslovi lekcija (data/topics.json) sami
sadrže „imenilaca“ i ne smiju se prepravljati.
"""
import random

from matbot import terminology
from matbot.deterministic import (algfractions, arithmetic, fractionconcepts,
                                  fractions, polynomials)

FORMS = {
    "imenilac": "nazivnik",
    "imenilaca": "nazivnika",
    "imenioca": "nazivnika",
    "imeniocu": "nazivniku",
    "imeniocem": "nazivnikom",
    "imenioci": "nazivnici",
    "imenioce": "nazivnike",
    "imeniocima": "nazivnicima",
}


# ---------------------------------------------------------------------------
# 1) NORMALIZATOR — svi padeži, kapitalizacija, matematika netaknuta
# ---------------------------------------------------------------------------

def test_all_inflected_forms_are_normalized():
    for source, target in FORMS.items():
        assert terminology.normalize_terminology(
            f"Pogledaj {source} pažljivo.") == f"Pogledaj {target} pažljivo."


def test_capitalization_is_preserved():
    assert terminology.normalize_terminology(
        "Imenioci su različiti.") == "Nazivnici su različiti."


def test_curriculum_title_echo_is_normalized_in_produced_text():
    text = "Lekcija: Sabiranje i oduzimanje razlomaka jednakih imenilaca."
    assert terminology.normalize_terminology(text).endswith(
        "jednakih nazivnika.")


def test_math_segments_are_never_touched():
    text = r"Vrijedi $imenilac$ u zapisu, a imenilac u prozi."
    normalized = terminology.normalize_terminology(text)
    assert "$imenilac$" in normalized
    assert normalized.endswith("nazivnik u prozi.")


def test_imenilac_is_normalize_only_not_a_forbidden_term():
    """Kurikularni naslovi legitimno sadrže „imenilaca“ — repo-sken ga NE
    tretira kao zabranjen (za razliku od hrvatskih/ekavskih termina).

    Kontrolni zabranjeni oblik se IZVODI iz samog modula (nikad doslovno u
    ovom fajlu — repo-sken bi ga inače označio ovdje)."""
    assert not terminology.contains_forbidden_term("jednakih imenilaca")
    control = next(t for t in terminology._TRIGGER_SUBSTRINGS
                   if terminology.contains_forbidden_term(t))
    assert terminology.contains_forbidden_term(control)


# ---------------------------------------------------------------------------
# 2) KANONSKI IZVOR — motori razlomaka više NE pišu „imenilac“
# ---------------------------------------------------------------------------

def _student_surfaces(package):
    return [package.question, package.solution, *package.hints,
            *package.option_texts]


def test_fraction_engines_emit_nazivnik_at_the_source():
    cases = [
        (fractions, {"allowed_operations": ("add", "subtract"),
                     "denominator_relation": "unlike",
                     "operand_kinds": ("fraction",),
                     "allowed_answer_kinds": ("fraction", "mixed_number"),
                     "simplification_policy": "optional",
                     "forbidden_directives": ()}),
        (arithmetic, {"allowed_operations": ("add",),
                      "expression_shape": "single_operation",
                      "number_domain": "rational_nonneg"}),
    ]
    for module, params in cases:
        if not module.supports(params):
            continue
        for seed in range(15):
            package = module.generate_package(
                lesson_id="X", lesson_title="t", parameters=params, level=2,
                rng=random.Random(seed))
            blob = " ".join(_student_surfaces(package)).lower()
            assert "imenil" not in blob and "imenioc" not in blob, (
                module.__name__, seed)


def test_fraction_concept_engine_emits_nazivnik():
    params = {"concepts": ("fraction_meaning",)}
    if not fractionconcepts.supports(params):
        import pytest
        pytest.skip("koncept nije podržan u ovoj konfiguraciji")
    for seed in range(10):
        package = fractionconcepts.generate_package(
            lesson_id="X", lesson_title="t", parameters=params, level=1,
            rng=random.Random(seed))
        blob = " ".join(_student_surfaces(package)).lower()
        assert "imenil" not in blob and "imenioc" not in blob


# ---------------------------------------------------------------------------
# 3) POLINOMI — puna dijakritika u prozi (bez degradiranog ASCII bosanskog)
# ---------------------------------------------------------------------------

DEGRADED = ("izluci", "najveci", "zajednick", "clanov", "clana", "pomnozi",
            "nadji", "rijesi jednacinu", "jednacine", "izjednaci",
            "ponistavaju", "sadrzi", "zapisi ostatak")


def test_polynomial_prose_uses_full_diacritics():
    module_params = [
        {"concepts": ("square_of_binomial",)},
        {"concepts": ("factor_common",)},
        {"concepts": ("factor_difference_squares",)},
        {"concepts": ("factor_grouping",)},
        {"concepts": ("zero_product",)},
        {"concepts": ("combine_like_terms",)},
    ]
    checked = 0
    for params in module_params:
        if not polynomials.supports(params):
            continue
        for seed in range(15):
            package = polynomials.generate_package(
                lesson_id="X", lesson_title="t", parameters=params, level=2,
                rng=random.Random(seed))
            blob = " ".join(_student_surfaces(package)).lower()
            for degraded in DEGRADED:
                assert degraded not in blob, (params, seed, degraded)
            checked += 1
    assert checked, "nijedan polinomski parametar nije podržan — test je mrtav"


def test_polynomial_source_carries_no_degraded_ascii_instruction_words():
    import inspect
    source = inspect.getsource(polynomials)
    for degraded in ("Izluci ", "najveci", "zajednicki", "clanova",
                     "Grupisi", "Izjednaci", "ponistavaju",
                     "Rijesi jednacinu"):
        assert degraded not in source, degraded
