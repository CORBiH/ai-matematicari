"""Fuzz metode nepoznatog člana (PP-1) — 6. razred, sve dostupne uloge.

STEP 19 (unified-policy migracija): za visokorizične porodice se dokazuje nad
mnogo sjemena da (1) proza uči ISPRAVNU ulogu, (2) transpoziciona/balansna
proza ne postoji, (3) nijedna vidljiva vrijednost nije negativna (ni u
opcijama), (4) matematika je tačna (lanac rješenja prolazi mathcheck, uloga
zaista računa rješenje), (5) provenijencija metode je stabilna.
"""
import random
import re
from fractions import Fraction

import pytest

from matbot import practice_policy as pp
from matbot.deterministic import equations
from matbot.mathcheck import find_numeric_inconsistencies
from matbot.tutor.package_preflight import safe_visible_text

CASES = {
    "6-07-002": {"shapes": ("one_step_additive", "subtract_from"),
                 "number_domain": "rational_nonneg"},
    "6-07-003": {"shapes": ("solve_inequality_additive",),
                 "number_domain": "rational_nonneg"},
    "6-07-004": {"shapes": ("one_step_multiplicative",),
                 "number_domain": "rational_nonneg"},
    "6-07-005": {"shapes": ("solve_inequality_multiplicative",),
                 "number_domain": "rational_nonneg"},
    "6-07-006": {"shapes": ("one_step_additive", "one_step_multiplicative"),
                 "number_domain": "decimal"},
}

# „faktor“, ne „činilac“: KS_2018 koristi isključivo „faktor“ (vidi
# tests/test_student_terminology_policy.py i matbot/terminology.py, pravilo 11).
ROLE_WORDS = ("sabirak", "umanjenik", "umanjilac", "faktor", "djeljenik",
              "djelilac")

# Riječi kojih u prozi 6. razreda NE SMIJE biti (metodske i predznačene).
FORBIDDEN_WORDS = ("prebac", "obje strane", "suprotnim predznakom",
                   "suprotan broj", "negativn")


def policy_for(lesson_id):
    return pp.resolve(6, lesson_id, "linear_equation_direct",
                      CASES[lesson_id], "naslov",
                      "Jednačine, nejednačine i izrazi u Q+")


def surfaces_of(package):
    return [package.question, package.solution, *package.hints,
            *package.option_texts]


@pytest.mark.parametrize("lesson_id", sorted(CASES))
@pytest.mark.parametrize("level", (1, 2, 3))
def test_unknown_member_packages_are_clean_across_many_seeds(lesson_id, level):
    params = CASES[lesson_id]
    policy = policy_for(lesson_id)
    for seed in range(40):
        package = equations.generate_package(
            lesson_id=lesson_id, lesson_title="naslov", parameters=params,
            level=level, rng=random.Random(seed), policy=policy)

        # 1) provenijencija i politika nad CIJELIM paketom
        assert package.method_id == "unknown_member", (lesson_id, seed)
        assert pp.package_policy_failures(
            policy, package.question, package.option_texts, package.hints,
            package.solution, package.method_id) == (), (lesson_id, seed)

        # 2) proza uči ulogu, nikad prebacivanje
        prose = " ".join([package.solution, *package.hints]).lower()
        assert any(word in prose for word in ROLE_WORDS), (lesson_id, seed)
        for word in FORBIDDEN_WORDS:
            assert word not in prose, (lesson_id, seed, word)

        # 3) nijedan vidljivi negativan literal, nigdje
        for surface in surfaces_of(package):
            assert not pp.find_visible_negative_literals(surface), (
                lesson_id, seed, surface)

        # 4) lanac rješenja je numerički dosljedan i mathsafe-siguran
        cleaned, safe = safe_visible_text(package.solution)
        assert safe, (lesson_id, seed)
        assert not find_numeric_inconsistencies(cleaned), (
            lesson_id, seed, package.solution)
        for hint in package.hints:
            _, hint_safe = safe_visible_text(hint)
            assert hint_safe, (lesson_id, seed, hint)


@pytest.mark.parametrize("level", (1, 2, 3))
def test_role_inequality_direction_is_mathematically_correct(level):
    """Smjer: uz pozitivne koeficijente simbol iz zadatka OSTAJE u odgovoru."""
    for lesson_id in ("6-07-003", "6-07-005"):
        params = CASES[lesson_id]
        policy = policy_for(lesson_id)
        for seed in range(40):
            package = equations.generate_package(
                lesson_id=lesson_id, lesson_title="n", parameters=params,
                level=level, rng=random.Random(seed), policy=policy)
            question_symbol = re.search(r"[<>]", package.question).group(0)
            answer_symbol = re.search(r"[<>]", package.display_answer).group(0)
            assert question_symbol == answer_symbol, (lesson_id, seed)


def test_role_relations_actually_compute_the_solution():
    """Uloga računa rješenje: uvrštavanje rješenja zadovoljava jednačinu."""
    number = r"(?:\d+(?:,\d+)?|\\frac\{\d+\}\{\d+\}|\d+\\frac\{\d+\}\{\d+\})"

    def parse(display):
        display = display.strip()
        mixed = re.fullmatch(r"(\d+)\\frac\{(\d+)\}\{(\d+)\}", display)
        if mixed:
            whole, p, q = (int(g) for g in mixed.groups())
            return Fraction(whole) + Fraction(p, q)
        frac = re.fullmatch(r"\\frac\{(\d+)\}\{(\d+)\}", display)
        if frac:
            return Fraction(int(frac.group(1)), int(frac.group(2)))
        return Fraction(display.replace(",", ".").replace(" ", ""))

    checked = 0
    for lesson_id in ("6-07-002", "6-07-004", "6-07-006"):
        params = CASES[lesson_id]
        policy = policy_for(lesson_id)
        for seed in range(60):
            package = equations.generate_package(
                lesson_id=lesson_id, lesson_title="n", parameters=params,
                level=1, rng=random.Random(seed), policy=policy)
            x = parse(package.display_answer)
            equation = re.search(r"\$(.+)\$", package.question).group(1)
            left, right = equation.split("=")
            match = re.fullmatch(
                rf"\s*(?:x \+ ({number})|({number}) \+ x|x - ({number})"
                rf"|({number}) - x|({number})x|x : \(?({number})\)?"
                rf"|({number}) : x|({number}) \\cdot x)\s*", left)
            assert match, equation
            rhs = parse(right)
            groups = match.groups()
            if groups[0] is not None:
                value = x + parse(groups[0])
            elif groups[1] is not None:
                value = parse(groups[1]) + x
            elif groups[2] is not None:
                value = x - parse(groups[2])
            elif groups[3] is not None:
                value = parse(groups[3]) - x
            elif groups[4] is not None:
                value = parse(groups[4]) * x
            elif groups[5] is not None:
                value = x / parse(groups[5])
            elif groups[6] is not None:
                value = parse(groups[6]) / x
            else:
                value = parse(groups[7]) * x
            assert value == rhs, (lesson_id, seed, equation,
                                  package.display_answer)
            checked += 1
    assert checked >= 150


# ---------------------------------------------------------------------------
# KONTROLE 7. RAZREDA — transpozicija OSTAJE školska metoda gdje je dozvoljena
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("shape,domain,stems", [
    ("one_step_additive", "integer", ("prebac", "obje strane")),
    ("one_step_multiplicative", "integer", ("prebac", "obje strane")),
    # Predznačena pedagogija smjera: pravilo o negativnom množiocu OSTAJE.
    ("solve_inequality_sign_flip", "integer", ("negativnim", "obrće")),
])
def test_grade7_keeps_transposition_pedagogy(shape, domain, stems):
    params = {"shapes": (shape,), "number_domain": domain}
    seen_prose = False
    for seed in range(30):
        package = equations.generate_package(
            lesson_id="7-02-0XX", lesson_title="n", parameters=params,
            level=2, rng=random.Random(seed))
        assert package.method_id == "transposition"
        prose = " ".join([package.solution, *package.hints]).lower()
        seen_prose = seen_prose or any(stem in prose for stem in stems)
    assert seen_prose, "predznačena pedagogija mora ostati u 7-9. razredu"


def test_sign_flip_still_flips_only_on_negative_multiplier():
    """Matematički autoritet: smjer se mijenja SAMO uz negativan množilac."""
    params = {"shapes": ("solve_inequality_sign_flip",),
              "number_domain": "integer"}
    flipped = 0
    for seed in range(40):
        package = equations.generate_package(
            lesson_id="9-04-0XX", lesson_title="n", parameters=params,
            level=2, rng=random.Random(seed))
        question = re.search(r"\$(.+)\$", package.question).group(1)
        coefficient = int(re.match(r"(-?\d+)x", question).group(1))
        q_symbol = re.search(r"[<>]", question).group(0)
        a_symbol = re.search(r"[<>]", package.display_answer).group(0)
        if coefficient < 0:
            assert a_symbol != q_symbol, (seed, question)
            flipped += 1
        else:
            assert a_symbol == q_symbol, (seed, question)
    assert flipped > 0


def test_unsupported_shape_fails_closed_under_unknown_member_policy():
    """DET-G6: oblik bez varijante nepoznatog člana NIKAD ne isporučuje
    transpozicionu prozu pod politikom 6. razreda — pada zatvoreno."""
    params = {"shapes": ("fraction_form",), "number_domain": "rational_nonneg"}
    policy = pp.resolve(6, "6-XX-XXX", "linear_equation_direct", params,
                        "n", "Jednačine, nejednačine i izrazi u Q+")
    with pytest.raises(equations.DeterministicGenerationError):
        equations.generate_package(
            lesson_id="6-XX-XXX", lesson_title="n", parameters=params,
            level=1, rng=random.Random(1), policy=policy)
