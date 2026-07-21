# -*- coding: utf-8 -*-
"""Deterministic facts about the ACTIVE task: a real hint ladder and a solution.

Production sent "ne znam" five times for ``1/3 + 4/5`` and got the same generic
sentence every time, because the hint pool had two entries and the level was
clamped to the last one. It also ignored "NE ZNAM URADI I OBJASNI POSTUPAK" — an
explicit request for the worked solution.

Facts here are computed from the task text, never from the model. Skill-local
and small on purpose: this is not an adaptive-hint system.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction
from math import gcd
from typing import Any

from matbot.minimal import mathfmt

#: "Izračunaj: 1/3 + 4/5." — the only shape ``fraction_add_sub`` generates.
_ADD_SUB_RE = re.compile(
    r"(\d+)\s*/\s*(\d+)\s*([+\-−])\s*(\d+)\s*/\s*(\d+)")


@dataclass(frozen=True)
class AddFacts:
    """Every number needed to guide or solve a/b ± c/d."""
    num_a: int
    den_a: int
    num_b: int
    den_b: int
    operator: str                   # "+" | "-"
    common: int                     # least common denominator
    expanded_a: int                 # numerator of a/b over `common`
    expanded_b: int
    result_numerator: int           # over `common`, before simplifying
    result: Fraction

    @property
    def left(self) -> str:
        return f"{self.num_a}/{self.den_a}"

    @property
    def right(self) -> str:
        return f"{self.num_b}/{self.den_b}"

    @property
    def over_common_a(self) -> str:
        return f"{self.expanded_a}/{self.common}"

    @property
    def over_common_b(self) -> str:
        return f"{self.expanded_b}/{self.common}"

    @property
    def raw_result(self) -> str:
        return f"{self.result_numerator}/{self.common}"

    @property
    def simplified(self) -> str:
        return _fraction_text(self.result)

    @property
    def mixed(self) -> str:
        """"1 2/15" when the result is improper, else ""."""
        value = self.result
        if value.denominator == 1 or abs(value) < 1:
            return ""
        whole = value.numerator // value.denominator
        rest = value - whole
        return f"{whole} {rest.numerator}/{rest.denominator}"

    def to_dict(self) -> dict:
        return {
            "left": self.left, "right": self.right, "operator": self.operator,
            "common_denominator": self.common,
            "expanded_left": self.over_common_a,
            "expanded_right": self.over_common_b,
            "result_numerator": self.result_numerator,
            "result": self.simplified, "mixed": self.mixed,
        }


def _fraction_text(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def _lcm(a: int, b: int) -> int:
    return abs(a * b) // gcd(a, b) if a and b else 0


def resolve_add_facts(question: Any) -> AddFacts | None:
    """Facts for an unlike-denominator addition/subtraction task, or None."""
    match = _ADD_SUB_RE.search(str(question or ""))
    if match is None:
        return None
    num_a, den_a = int(match.group(1)), int(match.group(2))
    operator = "-" if match.group(3) in "-−" else "+"
    num_b, den_b = int(match.group(4)), int(match.group(5))
    if den_a == 0 or den_b == 0:
        return None
    common = _lcm(den_a, den_b)
    if not common:
        return None
    expanded_a = num_a * (common // den_a)
    expanded_b = num_b * (common // den_b)
    result_numerator = (expanded_a + expanded_b if operator == "+"
                        else expanded_a - expanded_b)
    return AddFacts(
        num_a=num_a, den_a=den_a, num_b=num_b, den_b=den_b, operator=operator,
        common=common, expanded_a=expanded_a, expanded_b=expanded_b,
        result_numerator=result_numerator,
        result=Fraction(result_numerator, common),
    )


#: How many genuinely NEW hints the ladder has. Beyond this the last hint is
#: repeated, and the wording says so rather than pretending to add something.
ADD_HINT_LEVELS = 3


def add_hint(facts: AddFacts, level: int) -> str:
    """One rung of the ladder. Each level reveals strictly more than the last."""
    step = max(1, int(level or 1))
    if step == 1:
        return (f"Zajednički nazivnik za {facts.den_a} i {facts.den_b} je "
                f"{facts.common}.")
    if step == 2:
        # Each equality is ONE inline formula, so the student sees
        # \frac{1}{3}=\frac{5}{15} rather than two spans around a bare "=".
        left = mathfmt.inline(f"{facts.left} = {facts.over_common_a}")
        right = mathfmt.inline(f"{facts.right} = {facts.over_common_b}")
        return f"Proširi razlomke: {left}, a {right}."
    verb = "saberi" if facts.operator == "+" else "oduzmi"
    if step == 3:
        return (f"Sada {verb} brojnike {facts.expanded_a} "
                f"{'+' if facts.operator == '+' else '-'} {facts.expanded_b}, "
                f"a nazivnik ostaje {facts.common}.")
    # Past the ladder: repeat the last rung, honestly labelled.
    return (f"Isti korak kao maloprije: {verb} brojnike {facts.expanded_a} "
            f"{'+' if facts.operator == '+' else '-'} {facts.expanded_b}, "
            f"nazivnik ostaje {facts.common}. Ako želiš cijeli postupak, "
            "napiši „uradi i objasni postupak”.")


def solution_steps(facts: AddFacts) -> list[str]:
    """The worked steps as PLAIN structured strings (no LaTeX).

    Kept plain so the same facts can feed both the rendered formula and any
    machine-readable use; ``mathfmt`` adds the formatting.
    """
    sign = "+" if facts.operator == "+" else "-"
    steps = [
        f"{facts.left} {sign} {facts.right}",
        f"= {facts.over_common_a} {sign} {facts.over_common_b}",
        f"= {facts.raw_result}",
    ]
    if facts.simplified != facts.raw_result:
        steps.append(f"= {facts.simplified}")
    if facts.mixed:
        steps.append(f"= {facts.mixed}")
    return steps


def add_solution(facts: AddFacts) -> str:
    """The full worked solution, shown only on an explicit request."""
    return "\n".join(solution_steps(facts))


# --------------------------------------------------------------------------- #
# Linear equations: a·x ± b = c, and the fraction forms x ± a = b / a ± x = b  #
# --------------------------------------------------------------------------- #
#: "2x - 3 = 9", "x + 1/3 = 5/6"  → coefficient, constant on the left, right side.
_EQ_COEF_RE = re.compile(
    r"(?:^|:)\s*(\d+(?:\s*/\s*\d+)?)?\s*\*?\s*x\s*([+\-−])\s*"
    r"(\d+(?:\s*/\s*\d+)?)\s*=\s*(\d+(?:\s*/\s*\d+)?)")
#: "2/5 + x = 3/4"  → the constant comes first.
_EQ_LEAD_RE = re.compile(
    r"(?:^|:)\s*(\d+(?:\s*/\s*\d+)?)\s*([+\-−])\s*x\s*=\s*(\d+(?:\s*/\s*\d+)?)")


def _as_fraction(token: Any) -> Fraction | None:
    text = str(token or "").replace(" ", "")
    if not text:
        return None
    try:
        return Fraction(text)
    except (ValueError, ZeroDivisionError):
        return None


@dataclass(frozen=True)
class EquationFacts:
    """Every number needed to guide or solve a one-step / two-step equation."""
    coefficient: Fraction           # a in a·x
    constant: Fraction              # b, WITH its sign as it appears on the left
    right: Fraction                 # c
    solution: Fraction              # x
    leading_constant: bool = False  # the "a + x = b" shape

    @property
    def removes_by_adding(self) -> bool:
        """A negative constant is removed by ADDING its absolute value."""
        return self.constant < 0

    @property
    def move_amount(self) -> Fraction:
        return abs(self.constant)

    @property
    def intermediate_right(self) -> Fraction:
        """c - b: the right side once the constant has moved."""
        return self.right - self.constant

    @property
    def needs_division(self) -> bool:
        return self.coefficient != 1

    def _text(self, value: Fraction) -> str:
        return _fraction_text(value)

    @property
    def coefficient_text(self) -> str:
        return "" if self.coefficient == 1 else self._text(self.coefficient)

    @property
    def original_equation(self) -> str:
        sign = "-" if self.constant < 0 else "+"
        if self.leading_constant:
            return f"{self._text(abs(self.constant))} {sign} x = {self._text(self.right)}"
        return (f"{self.coefficient_text}x {sign} "
                f"{self._text(abs(self.constant))} = {self._text(self.right)}")

    @property
    def intermediate_equation(self) -> str:
        return f"{self.coefficient_text}x = {self._text(self.intermediate_right)}"

    @property
    def solution_equation(self) -> str:
        return f"x = {self._text(self.solution)}"

    def to_dict(self) -> dict:
        return {
            "coefficient": self._text(self.coefficient),
            "constant": self._text(self.constant),
            "right": self._text(self.right),
            "operation": ("add" if self.removes_by_adding else "subtract"),
            "move_amount": self._text(self.move_amount),
            "intermediate": self.intermediate_equation,
            "needs_division": self.needs_division,
            "solution": self._text(self.solution),
        }


def resolve_equation_facts(question: Any) -> EquationFacts | None:
    """Facts for a supported linear equation, or None.

    Handles ``a·x ± b = c`` (a may be absent, meaning 1) and the fraction lesson
    shapes ``x ± a = b`` and ``a ± x = b``. Every value is a ``Fraction``, so the
    integer and fraction cases share one implementation.
    """
    text = str(question or "")

    lead = _EQ_LEAD_RE.search(text)
    if lead is not None:
        constant = _as_fraction(lead.group(1))
        right = _as_fraction(lead.group(3))
        if constant is None or right is None:
            return None
        if lead.group(2) in "-−":
            # a - x = c  →  x = a - c
            return EquationFacts(coefficient=Fraction(1), constant=-constant,
                                 right=right, solution=constant - right,
                                 leading_constant=True)
        return EquationFacts(coefficient=Fraction(1), constant=constant,
                             right=right, solution=right - constant,
                             leading_constant=True)

    match = _EQ_COEF_RE.search(text)
    if match is None:
        return None
    coefficient = _as_fraction(match.group(1)) if match.group(1) else Fraction(1)
    magnitude = _as_fraction(match.group(3))
    right = _as_fraction(match.group(4))
    if not coefficient or magnitude is None or right is None:
        return None
    constant = -magnitude if match.group(2) in "-−" else magnitude
    return EquationFacts(coefficient=coefficient, constant=constant, right=right,
                         solution=(right - constant) / coefficient)


#: Genuinely new rungs before the ladder starts repeating.
EQUATION_HINT_LEVELS = 3


def equation_hint(facts: EquationFacts, level: int) -> str:
    """One rung. Never reveals the solution before the last rung."""
    step = max(1, int(level or 1))
    amount = _fraction_text(facts.move_amount)
    signed = ("-" if facts.constant < 0 else "+") + amount

    if step == 1:
        verb = "Dodaj" if facts.removes_by_adding else "Oduzmi"
        tail = "na obje strane" if facts.removes_by_adding else "s obje strane"
        return f"{verb} {amount} {tail} da ukloniš {signed}."
    if step == 2:
        if facts.needs_division:
            return f"Tada dobijaš {mathfmt.inline(facts.intermediate_equation)}."
        # a == 1: naming the intermediate WOULD be the answer, so name the
        # calculation instead.
        return ("Sada izračunaj "
                + mathfmt.inline(f"{_fraction_text(facts.right)} "
                                 f"{'+' if facts.removes_by_adding else '-'} "
                                 f"{amount}") + ".")
    if step == 3:
        if facts.needs_division:
            coefficient = _fraction_text(facts.coefficient)
            return f"Podijeli obje strane sa {coefficient} da dobiješ x."
        return ("Ako su nazivnici različiti, prvo ih izjednači, pa tek onda "
                "oduzmi brojnike.")
    return ("Isti korak kao maloprije. Ako želiš cijeli postupak, napiši "
            "„uradi i objasni postupak”.")


def equation_solution_steps(facts: EquationFacts) -> list[str]:
    """The worked steps as PLAIN equations; ``mathfmt`` adds the formatting."""
    steps = [facts.original_equation]
    if facts.needs_division or facts.intermediate_equation != facts.solution_equation:
        steps.append(facts.intermediate_equation)
    if steps[-1] != facts.solution_equation:
        steps.append(facts.solution_equation)
    return steps
