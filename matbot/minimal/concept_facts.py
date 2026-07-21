# -*- coding: utf-8 -*-
"""Verified arithmetic facts for conceptual questions about fraction expansion.

Production asked "a šta ako imamo 2/13 i treba proširiti na nazivnik 24" and the
model answered that the factor is 24/13, so 2·(24/13) = 48/24. Every step of
that is false: 24 is not a multiple of 13, so by the grade-6 definition the
expansion is impossible.

The model must never do arithmetic. This module computes the facts; the renderer
turns them into a sentence, and the model may at most rephrase text that already
contains the right numbers.

Nothing here grades anything — ``answer_checker`` remains the only grading owner.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from matbot.minimal.intent import fold

#: "2/13", "2 / 13"
_FRACTION_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
#: "na nazivnik 24", "nazivnik 24", "do nazivnika 24"
_TARGET_RE = re.compile(r"nazivnik\w*\s+(\d+)")
#: "proširiti sa 7", "proširimo sa 10", "brojem 7", "puta 3"
_FACTOR_RE = re.compile(
    r"(?:pro[sš]ir\w*|pomno[zž]\w*|mno[zž]\w*)?\s*(?:sa|s|brojem|puta)\s+(\d+)")
#: "isti brojnik i nazivnik", "isti brojnik i isti nazivnik".
#: ``naz\w*`` rather than ``naziv\w*`` so the production typo "nazvivnik" — and
#: similar slips — still match; students type fast.
_SAME_RE = re.compile(r"isti\s+brojnik\w*\s+i\s+(?:isti\s+)?naz\w*")

#: A neutral, correctly-spelled example for the "numerator == denominator" case.
SAME_EXAMPLE_NUMERATOR = 5


@dataclass(frozen=True)
class ExpandFacts:
    """Structured, verified facts. ``possible`` is the only verdict here."""
    possible: bool
    reason: str = ""
    original_numerator: int | None = None
    original_denominator: int | None = None
    target_denominator: int | None = None
    factor: int | None = None
    expanded_numerator: int | None = None
    expanded_denominator: int | None = None
    value_is_one: bool = False
    kind: str = "expand"            # expand | same_numerator_denominator

    @property
    def original(self) -> str:
        if self.original_numerator is None or self.original_denominator is None:
            return ""
        return f"{self.original_numerator}/{self.original_denominator}"

    @property
    def expanded(self) -> str:
        if self.expanded_numerator is None or self.expanded_denominator is None:
            return ""
        return f"{self.expanded_numerator}/{self.expanded_denominator}"

    def to_dict(self) -> dict:
        return {
            "possible": self.possible, "reason": self.reason, "kind": self.kind,
            "original": self.original,
            "target_denominator": self.target_denominator,
            "factor": self.factor, "expanded": self.expanded,
            "value_is_one": self.value_is_one,
        }


def resolve_expand_question(raw_question: Any) -> ExpandFacts | None:
    """Facts for a fraction-expansion question, or ``None`` when unparseable.

    ``None`` means "do not let anything calculate" — the caller must fall back
    to a non-numeric explanation or a clarification.
    """
    text = fold(raw_question)
    if not text:
        return None

    fraction = _FRACTION_RE.search(text)
    target_match = _TARGET_RE.search(text)
    factor_match = _FACTOR_RE.search(text)
    target = int(target_match.group(1)) if target_match else None
    factor = int(factor_match.group(1)) if factor_match else None

    # "isti brojnik i nazivnik" — the value is 1 whatever the numbers are.
    if _SAME_RE.search(text) and fraction is None:
        if factor is None or factor <= 0:
            return None
        base = SAME_EXAMPLE_NUMERATOR
        return ExpandFacts(
            possible=True, kind="same_numerator_denominator", factor=factor,
            original_numerator=base, original_denominator=base,
            expanded_numerator=base * factor, expanded_denominator=base * factor,
            value_is_one=True,
        )

    if fraction is None:
        return None
    num, den = int(fraction.group(1)), int(fraction.group(2))
    if den == 0:
        return None

    # A target denominator was named: expansion needs an INTEGER factor.
    if target is not None:
        if target <= 0 or target % den != 0:
            return ExpandFacts(
                possible=False, reason="target_denominator_not_multiple",
                original_numerator=num, original_denominator=den,
                target_denominator=target,
            )
        k = target // den
        return ExpandFacts(
            possible=True, original_numerator=num, original_denominator=den,
            target_denominator=target, factor=k,
            expanded_numerator=num * k, expanded_denominator=target,
            value_is_one=(num == den),
        )

    # An explicit factor was named instead.
    if factor is not None and factor > 0:
        return ExpandFacts(
            possible=True, original_numerator=num, original_denominator=den,
            factor=factor, target_denominator=den * factor,
            expanded_numerator=num * factor, expanded_denominator=den * factor,
            value_is_one=(num == den),
        )
    return None


def explain(facts: ExpandFacts) -> str:
    """A correct, neutral Bosnian explanation built ONLY from verified facts.

    Deterministic on purpose: production produced "brojemnikom" and invented
    arithmetic when the wording was left to the model.
    """
    if not facts.possible:
        if facts.reason == "target_denominator_not_multiple":
            return (
                f"Razlomak {facts.original} ne može se proširiti na nazivnik "
                f"{facts.target_denominator}, jer {facts.target_denominator} "
                f"nije djeljiv sa {facts.original_denominator}. "
                "Proširivanje znači množenje brojnika i nazivnika ISTIM cijelim "
                f"brojem, pa se iz {facts.original_denominator} mogu dobiti samo "
                f"nazivnici {_multiples(facts.original_denominator)} i tako dalje."
            )
        return ("Ovo pitanje ne mogu pouzdano izračunati, ali mogu ti objasniti "
                "pravilo proširivanja.")

    if facts.kind == "same_numerator_denominator":
        return (
            "Kada su brojnik i nazivnik jednaki, razlomak je jednak 1. "
            f"Ako proširiš sa {facts.factor}, množiš i brojnik i nazivnik sa "
            f"{facts.factor}: {facts.original} postaje {facts.expanded}. "
            "Vrijednost i dalje ostaje 1."
        )

    value_note = " Vrijednost ostaje 1." if facts.value_is_one else \
        " Vrijednost razlomka se ne mijenja."
    return (
        f"Nazivnik {facts.original_denominator} množiš sa {facts.factor} da "
        f"dobiješ {facts.expanded_denominator}, pa istim brojem množiš i brojnik: "
        f"{facts.original_numerator} · {facts.factor} = {facts.expanded_numerator}. "
        f"Dakle {facts.original} = {facts.expanded}.{value_note}"
    )


def _multiples(denominator: int, count: int = 4) -> str:
    return ", ".join(str(denominator * i) for i in range(2, 2 + count))
