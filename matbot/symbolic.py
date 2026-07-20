# -*- coding: utf-8 -*-
"""Symbolic-numeric values of the form ``a·π + b``.

Production marked "4pi cm" incorrect for an arc length of 4π cm and told the
student π was missing — from text that plainly contained it. The cause was that
π had no representation at all: every expected value was a plain ``Fraction``, so
a π answer could only ever be compared as a bare number.

This module gives π one representation and one comparison rule, used by both the
expected value and the student's answer, so the two can never be judged by
different logic.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

#: How close a decimal must be to the exact symbolic value to count as correct.
#: 12.57 vs 4π = 12.5663… is 0.0037 relative, so 0.5% accepts a normally rounded
#: answer while still rejecting a genuinely different value.
DEFAULT_REL_TOLERANCE = Fraction(1, 200)

_PI_TOKEN = r"(?:π|pi\b|PI\b|Pi\b)"
_NUM = r"\d+(?:[.,]\d+)?(?:\s*/\s*\d+)?"


def _to_fraction(raw: str) -> Fraction | None:
    s = (raw or "").strip().replace(",", ".")
    if not s:
        return None
    try:
        if "/" in s:
            num, den = s.split("/", 1)
            return Fraction(num.strip()) / Fraction(den.strip())
        return Fraction(s)
    except (ValueError, ZeroDivisionError):
        return None


@dataclass(frozen=True)
class SymbolicValue:
    """``pi_coeff·π + rational``. A plain number is ``pi_coeff == 0``."""
    pi_coeff: Fraction = Fraction(0)
    rational: Fraction = Fraction(0)

    @property
    def has_pi(self) -> bool:
        return self.pi_coeff != 0

    @property
    def decimal(self) -> float:
        return float(self.pi_coeff) * math.pi + float(self.rational)

    def display(self) -> str:
        if not self.has_pi:
            return _fmt(self.rational)
        coeff = "" if self.pi_coeff == 1 else ("-" if self.pi_coeff == -1
                                               else _fmt(self.pi_coeff))
        base = f"{coeff}π"
        if self.rational == 0:
            return base
        sign = "+" if self.rational > 0 else "-"
        return f"{base} {sign} {_fmt(abs(self.rational))}"

    def equals(self, other: "SymbolicValue",
               rel_tolerance: Fraction | None = None) -> bool:
        """Exact when both sides are symbolic; tolerant when one side is decimal.

        A decimal can only ever be an APPROXIMATION of a π value, so comparing it
        exactly would reject every correctly rounded answer. Two exact values are
        compared exactly, so 8π is never "close enough" to 4π.
        """
        if self.pi_coeff == other.pi_coeff and self.rational == other.rational:
            return True
        if self.has_pi == other.has_pi:
            return False                    # both exact, or both plain: differ
        tol = DEFAULT_REL_TOLERANCE if rel_tolerance is None else rel_tolerance
        exact = self.decimal if self.has_pi else other.decimal
        approx = other.decimal if self.has_pi else self.decimal
        if exact == 0:
            return abs(approx) <= float(tol)
        return abs(approx - exact) / abs(exact) <= float(tol)


def _fmt(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    as_float = float(value)
    if abs(as_float - round(as_float, 2)) < 1e-9:
        return f"{as_float:.2f}".rstrip("0").rstrip(".")
    return f"{value.numerator}/{value.denominator}"


#: "4π", "4 pi", "4*pi", "4 · π", "π", "-π", "π/2", "4π/3"
_PI_TERM_RE = re.compile(
    rf"(?P<sign>[+-]?)\s*(?P<coeff>{_NUM})?\s*[*·x×]?\s*{_PI_TOKEN}"
    rf"(?:\s*/\s*(?P<div>\d+))?", re.IGNORECASE)
_PLAIN_RE = re.compile(rf"(?P<sign>[+-]?)\s*(?P<num>{_NUM})")


def parse(text: Any) -> SymbolicValue | None:
    """Parse the first ``a·π + b`` expression in ``text``.

    Returns ``None`` when there is no numeric content at all, so callers can tell
    "no answer" apart from "the answer zero".
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    # Drop unit words so "4pi cm" parses; units are checked separately by the
    # caller, which already owns unit policy.
    body = re.sub(r"[A-Za-zČĆŽŠĐčćžšđ]{1,4}\.?\s*$", "", raw).strip() or raw

    pi_total = Fraction(0)
    found_pi = False
    consumed: list[tuple[int, int]] = []
    for m in _PI_TERM_RE.finditer(body):
        coeff = _to_fraction(m.group("coeff")) if m.group("coeff") else Fraction(1)
        if coeff is None:
            coeff = Fraction(1)
        div = m.group("div")
        if div:
            try:
                coeff = coeff / Fraction(div)
            except ZeroDivisionError:
                continue
        if m.group("sign") == "-":
            coeff = -coeff
        pi_total += coeff
        found_pi = True
        consumed.append((m.start(), m.end()))

    remainder = body
    for start, end in reversed(consumed):
        remainder = remainder[:start] + " " + remainder[end:]

    rational = Fraction(0)
    found_plain = False
    for m in _PLAIN_RE.finditer(remainder):
        val = _to_fraction(m.group("num"))
        if val is None:
            continue
        rational += -val if m.group("sign") == "-" else val
        found_plain = True

    if not found_pi and not found_plain:
        return None
    return SymbolicValue(pi_coeff=pi_total, rational=rational)


def mentions_pi(text: Any) -> bool:
    """Did the student actually write π? Feedback must never claim otherwise."""
    return bool(re.search(_PI_TOKEN, str(text or ""), re.IGNORECASE))
