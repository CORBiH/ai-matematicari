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
        return (f"Proširi razlomke: {facts.left} = {facts.over_common_a}, a "
                f"{facts.right} = {facts.over_common_b}.")
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


def add_solution(facts: AddFacts) -> str:
    """The full worked solution, shown only on an explicit request."""
    sign = "+" if facts.operator == "+" else "-"
    lines = [
        f"{facts.left} {sign} {facts.right}",
        f"= {facts.over_common_a} {sign} {facts.over_common_b}",
        f"= {facts.raw_result}",
    ]
    if facts.simplified != facts.raw_result:
        lines.append(f"= {facts.simplified}")
    if facts.mixed:
        lines.append(f"= {facts.mixed}")
    return "\n".join(lines)
