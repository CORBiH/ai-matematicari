"""Strukturisani tekstualni zadaci — ČINJENICE PRIJE PROZE (Batch #4, P2).

TOK KOJI OVAJ MODUL PROPISUJE:

    WordProblemFacts (poznate veličine + relacije + tražena nepoznata)
        ↓  solve()  — egzaktan rješavač po zatvorenom semantičkom tipu
    kanonski odgovor
        ↓  (matbot/deterministic/wordproblems.py) — TEK TADA bosanska proza,
           nagovještaji, rješenje i distraktori

Proza se NIKAD ne parsira da bi se otkrio odgovor: odgovor postoji prije
prve riječi proze. Ovaj modul ne poznaje lekciju, MCQ ni Practice — drugi
predviđeni potrošač je budući „Daj mi rezultat" mod sa spolja parsiranim
problemom istog IR oblika.

MATEMATIČKI AUTORITET: egzaktan (`Fraction`); novac i mjere nose jedinicu uz
svaku veličinu, a rješavač odbija (fail-closed) svaki tip koji ne prepoznaje
ili činjenice koje mu nedostaju.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from math import isqrt


class WordProblemError(ValueError):
    """Činjenice su nepotpune ili semantički tip nije podržan."""


@dataclass(frozen=True)
class Quantity:
    """Jedna imenovana egzaktna veličina s jedinicom (jedinica smije biti '')."""

    name: str
    value: Fraction
    unit: str = ""


@dataclass(frozen=True)
class WordProblemFacts:
    """Kanonski IR jednog strukturisanog tekstualnog zadatka.

    ``semantic_type`` pripada ZATVORENOM skupu podržanih tipova; ``known`` su
    sve poznate veličine; ``unknown`` je ime tražene veličine;
    ``relationships`` su deklarativni opisi veza (za potpis/prozu, ne za
    izvođenje — izvodi ISKLJUČIVO rješavač tipa)."""

    semantic_type: str
    entities: tuple
    known: tuple
    unknown: str
    relationships: tuple = ()
    constraints: tuple = ()

    def value_of(self, name: str) -> Fraction:
        for quantity in self.known:
            if quantity.name == name:
                return quantity.value
        raise WordProblemError(f"nedostaje poznata veličina {name!r}")

    def unit_of(self, name: str) -> str:
        for quantity in self.known:
            if quantity.name == name:
                return quantity.unit
        raise WordProblemError(f"nedostaje poznata veličina {name!r}")


@dataclass(frozen=True)
class WordProblemSolution:
    """Egzaktan odgovor + plan operacija kojim je izveden (za rješenje)."""

    answer: Quantity
    operation_plan: tuple
    auxiliary: dict


# ---------------------------------------------------------------------------
# RJEŠAVAČI PO SEMANTIČKOM TIPU — čiste funkcije nad činjenicama
# ---------------------------------------------------------------------------

def _solve_equal_sharing(facts: WordProblemFacts) -> WordProblemSolution:
    total = facts.value_of("total")
    groups = facts.value_of("groups")
    if groups <= 0 or total % groups != 0:
        raise WordProblemError("podjela nije egzaktna")
    per_group = total / groups
    return WordProblemSolution(
        Quantity("per_group", per_group, facts.unit_of("total")),
        ("total : groups",), {"per_group": per_group})


def _solve_sharing_remainder(facts: WordProblemFacts) -> WordProblemSolution:
    total = facts.value_of("total")
    groups = facts.value_of("groups")
    if groups <= 0 or total.denominator != 1 or groups.denominator != 1:
        raise WordProblemError("dijeljenje s ostatkom traži cijele brojeve")
    quotient, remainder = divmod(int(total), int(groups))
    return WordProblemSolution(
        Quantity("remainder", Fraction(remainder), facts.unit_of("total")),
        ("total : groups → količnik i ostatak",),
        {"quotient": Fraction(quotient), "remainder": Fraction(remainder)})


def _solve_fraction_of_quantity(facts: WordProblemFacts) -> WordProblemSolution:
    total = facts.value_of("total")
    part = facts.value_of("fraction")
    value = part * total
    if value.denominator != 1:
        raise WordProblemError("dio cjeline nije cio broj")
    return WordProblemSolution(
        Quantity("part", value, facts.unit_of("total")),
        ("fraction · total",), {"part": value})


def _solve_fraction_remainder(facts: WordProblemFacts) -> WordProblemSolution:
    total = facts.value_of("total")
    part = facts.value_of("fraction")
    used = part * total
    remainder = total - used
    if used.denominator != 1 or remainder < 0:
        raise WordProblemError("ostatak nije egzaktan")
    return WordProblemSolution(
        Quantity("remainder", remainder, facts.unit_of("total")),
        ("fraction · total", "total - part"),
        {"part": used, "remainder": remainder})


def _solve_money_total(facts: WordProblemFacts) -> WordProblemSolution:
    price_a = facts.value_of("price_a")
    price_b = facts.value_of("price_b")
    count_a = facts.value_of("count_a")
    total = price_a * count_a + price_b
    return WordProblemSolution(
        Quantity("total", total, facts.unit_of("price_a")),
        ("price_a · count_a", "+ price_b"), {"subtotal": price_a * count_a})


def _solve_money_change(facts: WordProblemFacts) -> WordProblemSolution:
    paid = facts.value_of("paid")
    price_a = facts.value_of("price_a")
    price_b = facts.value_of("price_b")
    spent = price_a + price_b
    change = paid - spent
    if change < 0:
        raise WordProblemError("plaćeno manje od cijene")
    return WordProblemSolution(
        Quantity("change", change, facts.unit_of("paid")),
        ("price_a + price_b", "paid - spent"), {"spent": spent})


def _solve_signed_change(facts: WordProblemFacts) -> WordProblemSolution:
    start = facts.value_of("start")
    changes = [quantity.value for quantity in facts.known
               if quantity.name.startswith("change_")]
    if not changes:
        raise WordProblemError("nema promjena")
    final = start
    for change in changes:
        final += change
    return WordProblemSolution(
        Quantity("final", final, facts.unit_of("start")),
        tuple(f"+ ({change})" for change in changes), {"final": final})


def _solve_number_equation(facts: WordProblemFacts) -> WordProblemSolution:
    # a·x + b = c  →  x = (c - b)/a
    a = facts.value_of("a")
    b = facts.value_of("b")
    c = facts.value_of("c")
    if a == 0:
        raise WordProblemError("koeficijent je nula")
    x = (c - b) / a
    return WordProblemSolution(
        Quantity("x", x, ""), ("c - b", ": a"), {"x": x})


def _solve_sum_difference_system(facts: WordProblemFacts) -> WordProblemSolution:
    total = facts.value_of("sum")
    difference = facts.value_of("difference")
    larger = (total + difference) / 2
    smaller = (total - difference) / 2
    if smaller < 0 or larger.denominator != 1:
        raise WordProblemError("sistem nema školsko rješenje")
    return WordProblemSolution(
        Quantity("larger", larger, facts.unit_of("sum")),
        ("(sum + difference) : 2", "(sum - difference) : 2"),
        {"larger": larger, "smaller": smaller})


def _solve_sum_multiple_system(facts: WordProblemFacts) -> WordProblemSolution:
    total = facts.value_of("sum")
    factor = facts.value_of("factor")
    smaller = total / (factor + 1)
    larger = factor * smaller
    if smaller.denominator != 1:
        raise WordProblemError("sistem nema cjelobrojno rješenje")
    return WordProblemSolution(
        Quantity("smaller", smaller, facts.unit_of("sum")),
        ("sum : (factor + 1)", "factor · smaller"),
        {"larger": larger, "smaller": smaller})


def _solve_box_volume(facts: WordProblemFacts) -> WordProblemSolution:
    a = facts.value_of("a")
    b = facts.value_of("b")
    c = facts.value_of("c")
    volume = a * b * c
    return WordProblemSolution(
        Quantity("volume", volume, facts.unit_of("a") + "^3"),
        ("a · b", "· c"), {"base": a * b})


def _solve_cube_surface(facts: WordProblemFacts) -> WordProblemSolution:
    a = facts.value_of("a")
    surface = 6 * a * a
    return WordProblemSolution(
        Quantity("surface", surface, facts.unit_of("a") + "^2"),
        ("a · a", "· 6"), {"face": a * a})


def _solve_pythagoras_distance(facts: WordProblemFacts) -> WordProblemSolution:
    a = facts.value_of("leg_a")
    b = facts.value_of("leg_b")
    square = a * a + b * b
    if square.denominator != 1:
        raise WordProblemError("kvadrat hipotenuze nije cio")
    root = isqrt(int(square))
    if root * root != int(square):
        raise WordProblemError("hipotenuza nije cio broj")
    return WordProblemSolution(
        Quantity("hypotenuse", Fraction(root), facts.unit_of("leg_a")),
        ("a² + b²", "korijen zbira"), {"square": square})


def _solve_pythagoras_leg(facts: WordProblemFacts) -> WordProblemSolution:
    c = facts.value_of("hypotenuse")
    a = facts.value_of("leg_a")
    square = c * c - a * a
    if square <= 0 or square.denominator != 1:
        raise WordProblemError("kvadrat katete nije pozitivan cio broj")
    root = isqrt(int(square))
    if root * root != int(square):
        raise WordProblemError("kateta nije cio broj")
    return WordProblemSolution(
        Quantity("leg_b", Fraction(root), facts.unit_of("hypotenuse")),
        ("c² - a²", "korijen razlike"), {"square": square})


_SOLVERS = {
    "equal_sharing": _solve_equal_sharing,
    "sharing_remainder": _solve_sharing_remainder,
    "fraction_of_quantity": _solve_fraction_of_quantity,
    "fraction_remainder": _solve_fraction_remainder,
    "money_total": _solve_money_total,
    "money_change": _solve_money_change,
    "signed_change": _solve_signed_change,
    "number_equation": _solve_number_equation,
    "sum_difference_system": _solve_sum_difference_system,
    "sum_multiple_system": _solve_sum_multiple_system,
    "box_volume": _solve_box_volume,
    "cube_surface": _solve_cube_surface,
    "pythagoras_distance": _solve_pythagoras_distance,
    "pythagoras_leg": _solve_pythagoras_leg,
}

SUPPORTED_TYPES = frozenset(_SOLVERS)


def solve(facts: WordProblemFacts) -> WordProblemSolution:
    """Egzaktno rješenje strukturisanog problema, ili WordProblemError."""
    solver = _SOLVERS.get(facts.semantic_type)
    if solver is None:
        raise WordProblemError(
            f"nepodržan semantički tip {facts.semantic_type!r}")
    return solver(facts)
