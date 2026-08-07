"""Kapacitetna ekspanzija — motori determinističkih porodica.

Svaki motor se ispituje SINTETIČKIM parametrima ugovora (bez ijednog ID-ja
lekcije): svaki generisan paket mora proći ISTE serverske validatore kao
model-paket — strukturu, preflight (opcije, ekvivalencija, orakli,
dokaz težine), mathsafe i mathcheck. Masovna validacija nad STVARNIM
kompajliranim lekcijama živi u tests/test_deterministic_bulk_properties.py;
ovdje se dokazuje sam motor, nezavisno od podataka.
"""
import random

import pytest

from matbot.deterministic import (arithmetic, core, equations, numbertheory,
                                  ordering, powers, quantities)
from matbot.mathcheck import find_numeric_inconsistencies
from matbot.mathsafe import sanitize_and_validate_math_text
from matbot.tutor import package_preflight
from matbot.tutor.schema import difficulty_evidence_errors, validate_task
from matbot import lesson_fidelity

CONFIGS = [
    (arithmetic, "nat-add", {"allowed_operations": ["add", "subtract"],
                             "number_domain": "natural"}),
    (arithmetic, "nat-mul", {"allowed_operations": ["multiply", "divide"],
                             "number_domain": "natural"}),
    (arithmetic, "nat-ord", {"allowed_operations": ["add", "subtract", "multiply", "divide"],
                             "number_domain": "natural",
                             "expression_shape": "order_of_operations"}),
    (arithmetic, "int-add-same", {"allowed_operations": ["add"],
                                  "number_domain": "integer",
                                  "sign_scope": "same_signs"}),
    (arithmetic, "int-add-diff", {"allowed_operations": ["add"],
                                  "number_domain": "integer",
                                  "sign_scope": "different_signs"}),
    (arithmetic, "int-sub", {"allowed_operations": ["subtract"],
                             "number_domain": "integer"}),
    (arithmetic, "int-mul", {"allowed_operations": ["multiply"],
                             "number_domain": "integer"}),
    (arithmetic, "int-div", {"allowed_operations": ["divide"],
                             "number_domain": "integer"}),
    (arithmetic, "int-multi", {"allowed_operations": ["multiply"],
                               "number_domain": "integer",
                               "expression_shape": "multi_factor"}),
    (arithmetic, "dec-add", {"allowed_operations": ["add", "subtract"],
                             "number_domain": "decimal"}),
    (arithmetic, "dec-mul", {"allowed_operations": ["multiply"],
                             "number_domain": "decimal"}),
    (arithmetic, "dec-div", {"allowed_operations": ["divide"],
                             "number_domain": "decimal"}),
    (arithmetic, "rat-add", {"allowed_operations": ["add"],
                             "number_domain": "rational_signed"}),
    (arithmetic, "rat-div", {"allowed_operations": ["divide"],
                             "number_domain": "rational_signed"}),
    (numbertheory, "div-rules", {"divisors": [2, 3, 4, 5, 6, 9, 10, 15, 25]}),
    (numbertheory, "membership", {"concepts": ["divisor_membership",
                                               "multiple_membership"]}),
    (numbertheory, "gcd", {"concepts": ["gcd"]}),
    (numbertheory, "lcm", {"concepts": ["lcm"]}),
    (numbertheory, "prime", {"concepts": ["prime_classification"]}),
    (numbertheory, "coprime", {"concepts": ["coprime_pairs"]}),
    (numbertheory, "factorize", {"concepts": ["prime_factorization"]}),
    (ordering, "cmp-nat", {"number_domain": "natural"}),
    (ordering, "cmp-frac", {"number_domain": "fraction"}),
    (ordering, "cmp-dec", {"number_domain": "decimal"}),
    (ordering, "cmp-int", {"number_domain": "integer"}),
    (ordering, "cmp-rat", {"number_domain": "rational"}),
    (ordering, "abs-int", {"concepts": ["absolute_value"],
                           "number_domain": "integer"}),
    (ordering, "abs-rat", {"concepts": ["absolute_value"],
                           "number_domain": "rational"}),
    (ordering, "opp-int", {"concepts": ["opposite"], "number_domain": "integer"}),
    (ordering, "opp-rat", {"concepts": ["opposite"], "number_domain": "rational"}),
    (powers, "square", {"concepts": ["square_value"]}),
    (powers, "power", {"concepts": ["power_value"]}),
    (powers, "zeroneg", {"concepts": ["zero_negative_exponent"]}),
    (powers, "samebase", {"concepts": ["same_base_product_quotient"]}),
    (powers, "powlaw", {"concepts": ["power_of_power_product"]}),
    (powers, "root", {"concepts": ["square_root_value"]}),
    (powers, "psq", {"concepts": ["perfect_square_recognition"]}),
    (quantities, "pct", {"concepts": ["percent_of_number", "fraction_to_percent"]}),
    (quantities, "mean", {"concepts": ["mean"]}),
    (quantities, "prob", {"concepts": ["classical_probability"]}),
    (equations, "eq-add", {"shapes": ["one_step_additive"],
                           "number_domain": "integer"}),
    (equations, "eq-mul-q", {"shapes": ["one_step_multiplicative"],
                             "number_domain": "rational"}),
    (equations, "eq-paren", {"shapes": ["parentheses"],
                             "number_domain": "integer"}),
    (equations, "eq-check", {"shapes": ["check_solution", "check_inequality"],
                             "number_domain": "integer"}),
]

# Sintetički naslov s obećanjem pravila djeljivosti dokazuje da paket porodice
# djeljivosti zadovoljava i naslovom nametnut semantički zahtjev.
_TITLES = {"div-rules": "Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25"}

CASES = [(engine, key, params, level, seed)
         for engine, key, params in CONFIGS
         for level in (1, 2, 3) for seed in range(4)]


def _package(engine, key, params, level, seed):
    return engine.generate_package(
        lesson_id="X-XX-XXX",
        lesson_title=_TITLES.get(key, "Sintetička lekcija"),
        parameters=params, level=level, rng=random.Random(seed * 977 + level))


@pytest.mark.parametrize("engine,key,params,level,seed", CASES,
                         ids=[f"{key}-L{level}-s{seed}"
                              for _e, key, _p, level, seed in CASES])
def test_every_engine_package_passes_every_server_validator(
        engine, key, params, level, seed):
    assert engine.supports(params), key
    package = _package(engine, key, params, level, seed)
    payload = package.task_payload()

    validate_task(payload)
    issues = package_preflight.collect_package_issues(payload, contract=None)
    assert issues == (), (key, level, seed,
                          package_preflight.describe_issues(issues))
    assert difficulty_evidence_errors(payload.difficulty_evidence, level) == ()
    assert payload.target_difficulty_level == level

    title = _TITLES.get(key)
    if title:
        requirement = lesson_fidelity.semantic_task_requirement(title)
        assert requirement is not None
        assert requirement.failure_for(payload.text) is None

    for text in (package.solution, *package.hints):
        cleaned, safe = sanitize_and_validate_math_text(text)
        assert safe, (key, text)
        assert find_numeric_inconsistencies(cleaned) == [], (key, text)
    # Prvi nagovještaj nikad ne otkriva kanonski odgovor.
    assert package.display_answer not in package.hints[0], (key, level, seed)


@pytest.mark.parametrize("engine,key,params", CONFIGS,
                         ids=[key for _e, key, _p in CONFIGS])
def test_same_seed_reproduces_the_same_package(engine, key, params):
    first = _package(engine, key, params, 2, 7)
    second = _package(engine, key, params, 2, 7)
    assert first.question == second.question
    assert first.option_texts == second.option_texts
    assert first.solution == second.solution


@pytest.mark.parametrize("engine", [arithmetic, numbertheory, ordering,
                                    powers, quantities, equations])
def test_unsupported_parameters_are_refused(engine):
    assert not engine.supports(None)
    assert not engine.supports({})
    assert not engine.supports({"allowed_operations": ["teleport"],
                                "number_domain": "natural"})
    with pytest.raises(core.DeterministicGenerationError):
        engine.generate_package("X-XX-XXX", "Sintetička lekcija", {}, 1)


def test_registry_exposes_every_family_exactly_once():
    from matbot import deterministic as registry
    families = sorted(registry.GENERATORS)
    assert len(families) == len(set(families))
    for family_id, module in registry.GENERATORS.items():
        declared = getattr(module, "FAMILY_IDS", None) or (module.FAMILY_ID,)
        assert family_id in declared
        assert callable(module.supports)
        assert callable(module.generate_package)
