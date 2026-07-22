# -*- coding: utf-8 -*-
"""Deterministic divisibility facts: rules, hints and worked solutions.

The selected lesson is "Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25" —
nine divisors, two of them COMPOUND (6 = divisible by 2 AND 3; 15 = divisible
by 3 AND 5). Production generated 19 consecutive tasks that were ALL divisor
6, because the legacy template this skill used (``task_templates._g_divisibility6``)
never varied the divisor at all — it is a fixed, hardcoded generator, not a
difficulty-aware one. This module supplies the missing rule facts so a real
generator (see ``skills._generate_divisibility``) can cover every promised
divisor, and so hints/solutions are computed from the ACTUAL number rather
than a static, skill-wide sentence.

Nothing here is graded — ``answer_checker.py`` (``_check_divisibility_explanation``,
``divisibility_coverage``) remains the sole grading owner. OpenAI never
computes any rule fact; every value here is plain arithmetic on ``n``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Compound divisors, decomposed into the two simpler rules they require.
COMPOUND_RULES: dict[int, tuple[int, int]] = {6: (2, 3), 15: (3, 5)}
#: Every divisor the selected lesson promises.
SUPPORTED_DIVISORS = (2, 3, 4, 5, 6, 9, 10, 15, 25)
#: Divisors whose rule needs the digit SUM.
DIGIT_SUM_DIVISORS = {3, 9}
#: Divisors whose rule needs the LAST TWO digits.
LAST_TWO_DIVISORS = {4, 25}


def digit_sum(n: int) -> int:
    return sum(int(d) for d in str(abs(n)))


def last_digit(n: int) -> int:
    return abs(n) % 10


def last_two_digits(n: int) -> int:
    return abs(n) % 100


def satisfies(n: int, k: int) -> bool:
    """The one arithmetic truth this whole module exists to gatekeep."""
    if k in COMPOUND_RULES:
        return all(satisfies(n, f) for f in COMPOUND_RULES[k])
    if k in DIGIT_SUM_DIVISORS:
        return digit_sum(n) % k == 0
    if k in LAST_TWO_DIVISORS:
        return last_two_digits(n) % k == 0
    if k == 5:
        return last_digit(n) in (0, 5)
    if k == 10:
        return last_digit(n) == 0
    return n % k == 0


def rule_statement(k: int) -> str:
    """The RULE in words, no specific number involved — hint level 1."""
    if k in COMPOUND_RULES:
        f1, f2 = COMPOUND_RULES[k]
        return f"Za djeljivost sa {k} provjeri djeljivost sa {f1} i sa {f2}."
    if k in DIGIT_SUM_DIVISORS:
        return f"Broj je djeljiv sa {k} ako mu je zbir cifara djeljiv sa {k}."
    if k in LAST_TWO_DIVISORS:
        return (f"Broj je djeljiv sa {k} ako su mu posljednje dvije cifre "
                f"djeljive sa {k}.")
    if k == 5:
        return "Broj je djeljiv sa 5 ako mu je posljednja cifra 0 ili 5."
    if k == 10:
        return "Broj je djeljiv sa 10 ako mu je posljednja cifra 0."
    return f"Broj je djeljiv sa {k} ako je ostatak pri dijeljenju sa {k} nula."


def factor_evidence(n: int, f: int) -> tuple[str, bool]:
    """(evidence sentence for THIS number, whether the condition holds).

    Used standalone for a simple divisor (2, 3, 4, 5, 9, 10, 25) and as one
    half of a compound divisor's explanation (6, 15).
    """
    if f in DIGIT_SUM_DIVISORS:
        ds = digit_sum(n)
        digits = "+".join(str(d) for d in str(abs(n)))
        ok = ds % f == 0
        return (f"zbir cifara je {digits}={ds}, {'što jeste' if ok else 'što nije'} "
               f"djeljivo sa {f}", ok)
    if f in LAST_TWO_DIVISORS:
        lt = last_two_digits(n)
        ok = lt % f == 0
        return (f"posljednje dvije cifre su {lt:02d}, "
               f"{'što jeste' if ok else 'što nije'} djeljivo sa {f}", ok)
    if f == 2:
        ld = last_digit(n)
        ok = ld % 2 == 0
        return (f"posljednja cifra je {ld}, pa je broj "
               f"{'paran' if ok else 'neparan'}", ok)
    if f == 5:
        ld = last_digit(n)
        ok = ld in (0, 5)
        return (f"posljednja cifra je {ld}, {'što jeste' if ok else 'što nije'} "
               "0 ili 5", ok)
    if f == 10:
        ld = last_digit(n)
        ok = ld == 0
        return (f"posljednja cifra je {ld}, {'što jeste' if ok else 'što nije'} 0", ok)
    ok = n % f == 0
    return (f"{n} {'jeste' if ok else 'nije'} djeljivo sa {f}", ok)


@dataclass(frozen=True)
class DivisibilityFacts:
    """Every fact needed to hint, solve, or explain ONE divisibility question."""
    n: int
    divisor: int

    @property
    def is_compound(self) -> bool:
        return self.divisor in COMPOUND_RULES

    @property
    def factors(self) -> tuple[int, ...]:
        return COMPOUND_RULES.get(self.divisor, (self.divisor,))

    @property
    def holds(self) -> bool:
        return satisfies(self.n, self.divisor)

    def factor_evidence(self, f: int) -> tuple[str, bool]:
        return factor_evidence(self.n, f)

    @property
    def failing_factor(self) -> int | None:
        """For a compound divisor that fails, WHICH factor identifies why."""
        if not self.is_compound:
            return None
        for f in self.factors:
            if not satisfies(self.n, f):
                return f
        return None

    def to_dict(self) -> dict:
        return {"n": self.n, "divisor": self.divisor, "holds": self.holds,
                "is_compound": self.is_compound, "factors": self.factors,
                "failing_factor": self.failing_factor}


#: "Provjeri da li je broj 156 djeljiv sa 6." — the exact phrasing
#: ``answer_checker._try_divisibility_with_explanation`` already parses, reused
#: here so the generator's own task text is gradeable with no new parser.
_ASK_RE = re.compile(r"broj\w*\s+(-?\d+)\s+djeljiv\w*\s+sa\s+(\d+)", re.IGNORECASE)


def resolve_divisibility_facts(question: Any) -> DivisibilityFacts | None:
    text = str(question or "")
    m = _ASK_RE.search(text)
    if not m:
        return None
    n, k = int(m.group(1)), int(m.group(2))
    if k <= 0 or k not in SUPPORTED_DIVISORS:
        return None
    return DivisibilityFacts(n=n, divisor=k)


def divisibility_rungs(facts: DivisibilityFacts) -> list[str]:
    """The genuinely distinct rungs for THIS number and divisor, in order.

    Rung 1 states the RULE (no number). Rung 2 (and 3, for a compound
    divisor) applies it to THIS number specifically — production repeated
    the exact same rule sentence for every hint; these differ every time.
    """
    k, n = facts.divisor, facts.n
    if not facts.is_compound:
        ev, _ok = factor_evidence(n, k)
        return [rule_statement(k), f"Za broj {n}: {ev}."]
    f1, f2 = facts.factors
    ev1, _ = factor_evidence(n, f1)
    ev2, _ = factor_evidence(n, f2)
    return [rule_statement(k), f"Za broj {n}: {ev1}.", f"Za broj {n}: {ev2}."]


#: Said once the ladder runs out, instead of dressing up a repeat as new help.
DIVISIBILITY_LADDER_EXHAUSTED = (
    "Isti korak kao maloprije. Ako želiš cijeli postupak, napiši "
    "„uradi i objasni postupak”.")


def divisibility_hint(facts: DivisibilityFacts, level: int) -> str:
    """One rung. Never states the final yes/no decision."""
    rungs = divisibility_rungs(facts)
    step = max(1, int(level or 1))
    if step > len(rungs):
        return DIVISIBILITY_LADDER_EXHAUSTED
    return rungs[step - 1]


def divisibility_solution(facts: DivisibilityFacts) -> str:
    """The full deterministic explanation, ending in the yes/no decision.

    For a failing compound divisor, names the SPECIFIC failing condition
    rather than a vague "doesn't meet both conditions".
    """
    k, n = facts.divisor, facts.n
    if not facts.is_compound:
        ev, _ok = factor_evidence(n, k)
        verdict = "DA" if facts.holds else "NE"
        return (f"{rule_statement(k)} Za broj {n}: {ev}. "
                f"Zaključak: {verdict} — broj {n} "
                f"{'jeste' if facts.holds else 'nije'} djeljiv sa {k}.")
    f1, f2 = facts.factors
    ev1, ok1 = factor_evidence(n, f1)
    ev2, ok2 = factor_evidence(n, f2)
    verdict = "DA" if facts.holds else "NE"
    lines = [rule_statement(k), f"Za broj {n}: {ev1}.", f"Za broj {n}: {ev2}."]
    lines.append(f"Zaključak: {verdict} — broj {n} "
                 f"{'jeste' if facts.holds else 'nije'} djeljiv sa {k}.")
    return " ".join(lines)


def canonical_explanation(n: int, k: int) -> str:
    """The generator's canonical ``expected_display`` — genuinely evidenced,
    so it self-validates against the SAME checker a student answer faces."""
    facts = DivisibilityFacts(n=n, divisor=k)
    if not facts.is_compound:
        ev, _ok = factor_evidence(n, k)
        return f"{'da' if facts.holds else 'ne'}, {n} {'je' if facts.holds else 'nije'} djeljiv sa {k} jer {ev}"
    f1, f2 = facts.factors
    ev1, _ = factor_evidence(n, f1)
    ev2, _ = factor_evidence(n, f2)
    return (f"{'da' if facts.holds else 'ne'}, {n} "
            f"{'je' if facts.holds else 'nije'} djeljiv sa {k} jer {ev1}, a {ev2}")
