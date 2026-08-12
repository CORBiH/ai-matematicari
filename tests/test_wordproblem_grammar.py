"""RODNO SLAGANJE u determinističkim tekstualnim zadacima.

ŽIVI NALAZ (finalna ciljana kampanja, dva puta u jednoj sesiji):

    „Amina je potrošio $\\frac{1}{5}$ od svojih $15$ jabuka.
     Koliko jabuka mu je OSTALO?“

Pool imena je mješovit po rodu, a rečenica je bila tvrdo u muškom rodu. Rod je
zato PODATAK uz ime; ovaj modul mjeri da nijedan šablon više ne slaže rod
pogrešno, i da matematika pritom nije pomjerena.
"""
import random
from collections import Counter

import pytest

from matbot.deterministic import wordproblems as wp

LESSON, TITLE = "6-04-015", "Tekstualni zadaci s razlomcima"
SEEDS = 3400            # ×3 nivoa ≈ 10 200 paketa

FEMALE = {name for name, gender in wp._PEOPLE if gender == "f"}
MALE = {name for name, gender in wp._PEOPLE if gender == "m"}

# Zatvorena tabela oblika koji se rodno slažu sa SUBJEKTOM. Ne generativna
# morfologija — samo ono što šabloni ovog modula stvarno pišu.
MASCULINE_FORMS = ("potrošio", " mu ")
FEMININE_FORMS = ("potrošila", " joj ")

ALL_TYPES = ("equal_sharing", "sharing_remainder", "fraction_of_quantity",
             "fraction_remainder", "fraction_of_fraction",
             "multi_fraction_remainder", "money_total", "money_change")


def _packages(problem_type, seeds=SEEDS):
    for seed in range(seeds):
        for level in (1, 2, 3):
            try:
                yield wp.generate_package(
                    LESSON, TITLE, {"problem_types": [problem_type]}, level,
                    rng=random.Random((seed << 4) | level))
            except Exception:                              # noqa: BLE001
                continue


def _subject(question):
    """Ime osobe koja je subjekat rečenice, ili None."""
    first = question.split(" ", 1)[0].strip()
    return first if first in FEMALE or first in MALE else None


# ---------------------------------------------------------------------------
# §20 — korpus preko SVIH šablona ovog modula
# ---------------------------------------------------------------------------

def test_no_gender_disagreement_across_the_whole_module():
    checked = Counter()
    violations = []
    for problem_type in ALL_TYPES:
        for package in _packages(problem_type, seeds=420):
            question = package.question
            name = _subject(question)
            if name is None:
                continue
            checked[problem_type] += 1
            padded = f" {question} "
            if name in FEMALE:
                bad = [form for form in MASCULINE_FORMS if form in padded]
            else:
                bad = [form for form in FEMININE_FORMS if form in padded]
            if bad:
                violations.append((problem_type, name, bad, question))
    assert violations == [], violations[:3]
    # Provjera mora stvarno nešto vidjeti, inače je zeleno bez pokrića.
    assert checked["fraction_remainder"] > 0
    assert sum(checked.values()) > 2000, checked


@pytest.mark.parametrize("gender,expected,forbidden", [
    ("f", "potrošila", "potrošio"),
    ("m", "potrošio", "potrošila"),
])
def test_remainder_template_agrees_with_the_subject(gender, expected, forbidden):
    names = FEMALE if gender == "f" else MALE
    seen = 0
    for package in _packages("fraction_remainder", seeds=3400):
        name = _subject(package.question)
        if name not in names:
            continue
        seen += 1
        assert expected in package.question, package.question
        assert forbidden not in package.question, package.question
        pronoun = " joj " if gender == "f" else " mu "
        assert pronoun in f" {package.question} ", package.question
    assert seen > 1000, seen


def test_every_person_in_the_pool_is_reachable_and_typed():
    genders = {gender for _, gender in wp._PEOPLE}
    assert genders == {"m", "f"}
    assert len(wp._PEOPLE) == len(wp._NAMES)
    assert wp._NAMES == tuple(name for name, _ in wp._PEOPLE)
    # Obje grupe moraju biti neprazne, inače test rodnog slaganja ne dokazuje ništa.
    assert FEMALE and MALE


# ---------------------------------------------------------------------------
# MATEMATIKA SE NIJE POMJERILA
# ---------------------------------------------------------------------------

def test_remainder_mathematics_is_unchanged_by_the_grammar_fix():
    from fractions import Fraction
    from matbot.mathkernel import wordfacts

    checked = 0
    for package in _packages("fraction_remainder", seeds=1200):
        parameters = {p[0]: p[1] for p in package.signature_parameters}
        truth = wordfacts.solve_from_parameters(
            "fraction_remainder",
            {k: v for k, v in parameters.items() if k not in ("type", "level")})
        assert Fraction(package.display_answer.strip("$")) == truth
        checked += 1
    assert checked > 3000, checked


def test_archetype_distribution_is_untouched():
    """Ista raspodjela tipova po nivou kao prije popravke gramatike."""
    counts = Counter()
    parameters = {"problem_types": ["fraction_of_quantity", "fraction_remainder"]}
    for seed in range(3000):
        package = wp.generate_package(LESSON, TITLE, parameters, 2,
                                      rng=random.Random(seed))
        counts[dict(package.signature_parameters)["type"]] += 1
    assert set(counts) == {"fraction_of_quantity", "fraction_remainder"}
    for value in counts.values():
        assert value > 1000, counts
