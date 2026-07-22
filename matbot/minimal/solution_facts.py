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
    """Every number needed to guide or solve a supported equation.

    Four lesson shapes, distinguished explicitly rather than by sign alone:

    ====================  ===============  ==========================
    shape                 flags            x equals
    ====================  ===============  ==========================
    ``x + a = b``         --               ``b - a``
    ``a + x = b``         leading          ``b - a``
    ``x - a = b``         --               ``b + a``
    ``a - x = b``         leading+subtr.   ``a - b``
    ====================  ===============  ==========================

    The last row is the trap: the unknown is the SUBTRAHEND, so it is not the
    ``x - a = b`` transformation with a different sign.
    """
    coefficient: Fraction           # a in a*x
    constant: Fraction              # b, WITH its sign as it appears on the left
    right: Fraction                 # c
    solution: Fraction              # x
    leading_constant: bool = False  # the "a + x = b" / "a - x = b" shape
    subtracted_unknown: bool = False    # the "a - x = b" shape specifically

    # ---- basic shape -----------------------------------------------------
    @property
    def removes_by_adding(self) -> bool:
        """A negative constant is removed by ADDING its absolute value.

        Meaningless for ``a - x = b``, where nothing is moved to isolate x.
        """
        return self.constant < 0 and not self.subtracted_unknown

    @property
    def move_amount(self) -> Fraction:
        return abs(self.constant)

    @property
    def needs_division(self) -> bool:
        return self.coefficient != 1

    @property
    def intermediate_right(self) -> Fraction:
        """c - b: the right side once the constant has moved."""
        return self.right - self.constant

    # ---- the one-step isolation, kept UNEVALUATED -------------------------
    @property
    def operands(self) -> tuple[Fraction, Fraction, str]:
        """(first, second, operator) of the expression x is equal to."""
        if self.subtracted_unknown:
            return abs(self.constant), self.right, "-"
        return self.right, self.move_amount, ("+" if self.constant < 0 else "-")

    @property
    def isolate_expression(self) -> str:
        first, second, operator = self.operands
        return (f"{_fraction_text(first)} {operator} "
                f"{_fraction_text(second)}")

    @property
    def common_denominator(self) -> int:
        """LCM of the two operands, or 0 when they already agree."""
        first, second, _ = self.operands
        if first.denominator == second.denominator:
            return 0
        return _lcm(first.denominator, second.denominator)

    @property
    def common_expression(self) -> str:
        """"6/10 - 5/10" â empty when the denominators already agree."""
        common = self.common_denominator
        if not common:
            return ""
        first, second, operator = self.operands
        left = first.numerator * (common // first.denominator)
        right = second.numerator * (common // second.denominator)
        return f"{left}/{common} {operator} {right}/{common}"

    def equalised_pairs(self) -> list[tuple[str, str]]:
        """[("3/5", "6/10"), ("1/2", "5/10")] for the equalisation rung.

        A fraction already standing on the common denominator is omitted —
        showing "5/6 = 5/6" reads as a mistake to a child.
        """
        common = self.common_denominator
        if not common:
            return []
        pairs = []
        for value in self.operands[:2]:
            if value.denominator == common:
                continue
            scaled = value.numerator * (common // value.denominator)
            pairs.append((_fraction_text(value), f"{scaled}/{common}"))
        return pairs

    # ---- rendered equations (PLAIN text; mathfmt adds the LaTeX) ----------
    def _text(self, value: Fraction) -> str:
        return _fraction_text(value)

    @property
    def coefficient_text(self) -> str:
        return "" if self.coefficient == 1 else self._text(self.coefficient)

    @property
    def original_equation(self) -> str:
        sign = "-" if self.constant < 0 else "+"
        if self.leading_constant:
            return (f"{self._text(abs(self.constant))} {sign} x = "
                    f"{self._text(self.right)}")
        return (f"{self.coefficient_text}x {sign} "
                f"{self._text(abs(self.constant))} = {self._text(self.right)}")

    @property
    def intermediate_equation(self) -> str:
        """``2x = 12`` for a real coefficient; the UNEVALUATED isolation
        otherwise, so it never leaks the answer."""
        if self.needs_division:
            return (f"{self.coefficient_text}x = "
                    f"{self._text(self.intermediate_right)}")
        return f"x = {self.isolate_expression}"

    @property
    def solution_equation(self) -> str:
        return f"x = {self._text(self.solution)}"

    def to_dict(self) -> dict:
        return {
            "coefficient": self._text(self.coefficient),
            "constant": self._text(self.constant),
            "right": self._text(self.right),
            "shape": self.shape,
            "operation": self.operation,
            "move_amount": self._text(self.move_amount),
            "isolate": self.isolate_expression,
            "common": self.common_expression,
            "intermediate": self.intermediate_equation,
            "needs_division": self.needs_division,
            "solution": self._text(self.solution),
        }

    @property
    def shape(self) -> str:
        if self.subtracted_unknown:
            return "a-x=b"
        if self.leading_constant:
            return "a+x=b"
        return "x-a=b" if self.constant < 0 else "x+a=b"

    @property
    def operation(self) -> str:
        if self.subtracted_unknown:
            return "isolate_subtrahend"
        return "add" if self.removes_by_adding else "subtract"


def resolve_equation_facts(question: Any) -> EquationFacts | None:
    """Facts for a supported linear equation, or None.

    Handles ``a*x +/- b = c`` (a may be absent, meaning 1) and the fraction
    lesson shapes ``x +/- a = b`` and ``a +/- x = b``. Every value is a
    ``Fraction``, so the integer and fraction cases share one implementation.
    """
    text = str(question or "")

    lead = _EQ_LEAD_RE.search(text)
    if lead is not None:
        constant = _as_fraction(lead.group(1))
        right = _as_fraction(lead.group(3))
        if constant is None or right is None:
            return None
        if lead.group(2) in "-−":
            # a - x = c  ->  x = a - c. The unknown is the SUBTRAHEND: this is
            # NOT the "x - a = b" move with a flipped sign.
            return EquationFacts(coefficient=Fraction(1), constant=-constant,
                                 right=right, solution=constant - right,
                                 leading_constant=True, subtracted_unknown=True)
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


#: Said once the ladder runs out, instead of dressing up a repeat as new help.
EQUATION_LADDER_EXHAUSTED = (
    "Isti korak kao maloprije. Ako želiš cijeli postupak, napiši "
    "„uradi i objasni postupak”.")


def equation_rungs(facts: EquationFacts) -> list[str]:
    """The genuinely distinct rungs for THIS equation, in order.

    The length varies by shape: an integer equation needs a division rung, a
    fraction equation needs an equalisation rung only when the denominators
    actually differ. No rung states the solution.
    """
    amount = _fraction_text(facts.move_amount)

    if facts.needs_division:
        verb = "Dodaj" if facts.removes_by_adding else "Oduzmi"
        tail = "na obje strane" if facts.removes_by_adding else "s obje strane"
        signed = ("-" if facts.constant < 0 else "+") + amount
        return [
            f"{verb} {amount} {tail} da ukloniš {signed}.",
            f"Tada dobijaš {mathfmt.inline(facts.intermediate_equation)}.",
            f"Podijeli obje strane sa {_fraction_text(facts.coefficient)} "
            "da dobiješ x.",
        ]

    if facts.subtracted_unknown:
        first = mathfmt.inline(_fraction_text(abs(facts.constant)))
        opening = (f"Pazi: ovdje se x oduzima od {first}, pa se ne prebacuje "
                   "isto kao kad je x umanjenik.")
    elif facts.removes_by_adding:
        opening = f"Dodaj {mathfmt.inline(amount)} na obje strane."
    else:
        opening = f"Oduzmi {mathfmt.inline(amount)} s obje strane."

    rungs = [opening,
             f"Tada dobijaš {mathfmt.inline(facts.intermediate_equation)}."]

    pairs = facts.equalised_pairs()
    if pairs:
        shown = " i ".join(mathfmt.inline(f"{plain}={scaled}")
                           for plain, scaled in pairs)
        rungs.append(f"Izjednači nazivnike: {shown}.")
        rungs.append("Sada izračunaj "
                     + mathfmt.inline(facts.common_expression) + ".")
    else:
        # Denominators already agree, so repeating the same expression would
        # not be a new rung — name the operation on the numerators instead.
        verb = "saberi" if facts.operands[2] == "+" else "oduzmi"
        rungs.append(f"Sada {verb} brojnike, a nazivnik ostaje "
                     f"{facts.operands[0].denominator}.")
    return rungs


def equation_hint(facts: EquationFacts, level: int) -> str:
    """One rung. Never reveals the solution, at any level."""
    rungs = equation_rungs(facts)
    step = max(1, int(level or 1))
    if step > len(rungs):
        return EQUATION_LADDER_EXHAUSTED
    return rungs[step - 1]


def equation_solution_steps(facts: EquationFacts) -> list[str]:
    """The worked steps as PLAIN equations; ``mathfmt`` adds the formatting."""
    steps = [facts.original_equation, facts.intermediate_equation]
    common = facts.common_expression
    if common and not facts.needs_division:
        steps.append(f"x = {common}")
    if steps[-1] != facts.solution_equation:
        steps.append(facts.solution_equation)
    # A one-step integer equation can produce "x = 6" twice.
    deduped = [steps[0]]
    for step in steps[1:]:
        if step != deduped[-1]:
            deduped.append(step)
    return deduped
