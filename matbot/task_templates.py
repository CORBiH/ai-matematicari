"""Engine V2 — Phase 5: shared deterministic task-generation layer.

ONE generation layer that BOTH the Practice Step Engine and the Exam Engine
consume. Each supported skill is a parameterized template that:

    skill -> pick params -> COMPUTE the expected answer in code -> render the
    question -> VALIDATE (self-grade with answer_checker) -> activate.

No task is ever emitted without passing validation (the checker must accept the
code-computed answer). Generation is deterministic per seed, and duplicates are
avoided within a batch. Only grade-6/7 deterministically-checkable skills are
covered; anything else has NO template and the callers fall back EXPLICITLY (they
never silently substitute an unrelated task).

Pure/serializable; imports only ``answer_checker`` (a leaf) — no import cycle.
"""
from __future__ import annotations

import math
import random
import re
import unicodedata
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Callable

from matbot.answer_checker import check_practice_answer
from matbot.grading_guard import authoritative_verdict

# Skills that also have a guided SolutionPlan (Practice Step Engine can guide them).
GUIDABLE_SKILLS = frozenset({
    "divisibility_by_6", "prime_factorization", "linear_equation", "fraction_add_sub",
})

_POSITIVE = {
    "correct", "correct_equivalent_form", "correct_missing_notation",
    "correct_missing_unit",
}


def _fold(text: Any) -> str:
    s = unicodedata.normalize("NFKD", str(text or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


# --------------------------------------------------------------------------- #
# Parameterized generators: (rng) -> (question, canonical_answer)              #
# --------------------------------------------------------------------------- #
def _prime_factors(n: int) -> list[int]:
    out, d, m = [], 2, n
    while d * d <= m:
        while m % d == 0:
            out.append(d); m //= d
        d += 1
    if m > 1:
        out.append(m)
    return out


def _frac_str(v: Fraction) -> str:
    return str(v.numerator) if v.denominator == 1 else f"{v.numerator}/{v.denominator}"


_UNLIKE_DENOMS = [(2, 3), (3, 4), (2, 5), (3, 5), (4, 5), (2, 7), (3, 7), (5, 6), (3, 8), (4, 6)]


def _g_divisibility6(rng: random.Random) -> tuple[str, str]:
    n = 6 * rng.randint(3, 45) if rng.random() < 0.65 else rng.randint(20, 260)
    return (f"Provjeri da li je broj {n} djeljiv sa 6. Obrazloži svoj odgovor.",
            "da" if n % 6 == 0 else "ne")


def _g_prime_factorization(rng: random.Random) -> tuple[str, str]:
    n = rng.choice([12, 18, 20, 24, 28, 30, 36, 40, 42, 45, 48, 50, 54, 56, 60, 72, 84, 90, 100])
    return f"Rastavi {n} na proste faktore.", "*".join(str(p) for p in _prime_factors(n))


def _g_gcd(rng: random.Random) -> tuple[str, str]:
    a, b = rng.randint(6, 60), rng.randint(6, 60)
    return f"Odredi NZD({a}, {b}).", str(math.gcd(a, b))


def _g_lcm(rng: random.Random) -> tuple[str, str]:
    a, b = rng.randint(2, 15), rng.randint(2, 15)
    return f"Odredi NZS({a}, {b}).", str(a * b // math.gcd(a, b))


def _g_fraction_add_sub(rng: random.Random) -> tuple[str, str]:
    b, d = rng.choice(_UNLIKE_DENOMS)              # unlike denominators → guidable
    a, c = rng.randint(1, b - 1), rng.randint(1, d - 1)
    op = rng.choice(["+", "-"])
    val = Fraction(a, b) + Fraction(c, d) if op == "+" else Fraction(a, b) - Fraction(c, d)
    if val <= 0:                                    # keep it positive
        op, val = "+", Fraction(a, b) + Fraction(c, d)
    return f"Izračunaj: {a}/{b} {op} {c}/{d}.", _frac_str(val)


def _g_fraction_mul(rng: random.Random) -> tuple[str, str]:
    b, d = rng.randint(2, 9), rng.randint(2, 9)
    a, c = rng.randint(1, b), rng.randint(1, d)
    return f"Izračunaj: {a}/{b} · {c}/{d}.", _frac_str(Fraction(a, b) * Fraction(c, d))


def _g_percent_of(rng: random.Random) -> tuple[str, str]:
    for _ in range(20):
        p = rng.choice([5, 10, 20, 25, 40, 50, 75])
        n = rng.choice([20, 40, 50, 60, 80, 100, 120, 160, 200, 240])
        if (n * p) % 100 == 0:
            return f"Koliko je {p}% od {n}?", str(n * p // 100)
    return "Koliko je 20% od 50?", "10"


def _g_unit_conversion(rng: random.Random) -> tuple[str, str]:
    x = rng.randint(1, 9)
    frm, to, fac = rng.choice([("m", "cm", 100), ("cm", "mm", 10), ("kg", "g", 1000),
                               ("km", "m", 1000), ("h", "min", 60)])
    return f"Pretvori {x} {frm} u {to}.", f"{x * fac} {to}"


def _g_linear_equation(rng: random.Random) -> tuple[str, str]:
    a, x = rng.randint(2, 6), rng.randint(1, 9)
    b = rng.randint(1, 9) * rng.choice([1, -1])
    c = a * x + b
    op = "+" if b >= 0 else "-"
    return f"Riješi jednačinu: {a}x {op} {abs(b)} = {c}.", f"x={x}"


def _g_triangle_angle(rng: random.Random) -> tuple[str, str]:
    for _ in range(20):
        a, b = rng.randint(20, 130), rng.randint(20, 130)
        if 0 < 180 - a - b < 180 and a + b < 180:
            return f"U trouglu su dva ugla {a}° i {b}°. Odredi treći ugao.", f"{180 - a - b}°"
    return "U trouglu su dva ugla 60° i 70°. Odredi treći ugao.", "50°"


def _g_set_union(rng: random.Random) -> tuple[str, str]:
    a = sorted(rng.sample(range(1, 9), rng.randint(2, 3)))
    b = sorted(rng.sample(range(1, 9), rng.randint(2, 3)))
    u = sorted(set(a) | set(b))
    fmt = lambda s: "{" + ",".join(map(str, s)) + "}"  # noqa: E731
    return f"Odredi A ∪ B ako je A={fmt(a)}, B={fmt(b)}.", fmt(u)


# --------------------------------------------------------------------------- #
# Template registry                                                            #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SkillTemplate:
    skill_id: str
    grades: tuple[int, ...]
    keywords: tuple[str, ...]           # matched against folded oblast AND tema
    generate: Callable[[random.Random], tuple[str, str]]

    @property
    def guidable(self) -> bool:
        return self.skill_id in GUIDABLE_SKILLS


_TEMPLATES: tuple[SkillTemplate, ...] = (
    SkillTemplate("divisibility_by_6", (6,), ("djeljiv", "djelji"), _g_divisibility6),
    SkillTemplate("prime_factorization", (6,), ("djeljiv", "prost", "faktor", "prirodn"), _g_prime_factorization),
    SkillTemplate("gcd", (6,), ("djeljiv", "nzd", "najveci zajednicki"), _g_gcd),
    SkillTemplate("lcm", (6,), ("djeljiv", "nzs", "najmanji zajednicki"), _g_lcm),
    SkillTemplate("fraction_add_sub", (6, 7), ("razlom", "racionaln"), _g_fraction_add_sub),
    SkillTemplate("fraction_mul", (6, 7), ("razlom", "racionaln"), _g_fraction_mul),
    SkillTemplate("percent_of", (6, 7), ("postotak", "procent", "razmjer"), _g_percent_of),
    SkillTemplate("unit_conversion", (6,), ("mjerenje", "mjerne", "jedinic"), _g_unit_conversion),
    SkillTemplate("linear_equation", (6, 7), ("jednacin", "jednacine", "izraz"), _g_linear_equation),
    SkillTemplate("triangle_angle", (6, 7), ("ugao", "ugl", "trougao", "trokut"), _g_triangle_angle),
    SkillTemplate("set_union", (6, 7), ("skup",), _g_set_union),
)

_BY_ID = {t.skill_id: t for t in _TEMPLATES}


def _grade_int(grade: Any) -> int | None:
    try:
        return int(grade)
    except (TypeError, ValueError):
        return None


def select_templates(grade: Any, oblast: Any = "", tema: Any = "") -> list[SkillTemplate]:
    """Templates matching the selected grade and (oblast/tema) keywords.

    When neither oblast nor tema is given, ALL grade-matching templates match
    (a generic exam). When a topic IS given but nothing matches, returns []."""
    g = _grade_int(grade)
    probe = f"{_fold(oblast)} {_fold(tema)}".strip()
    out = []
    for t in _TEMPLATES:
        if g is not None and g not in t.grades:
            continue
        if not probe:
            out.append(t)
        elif any(k in probe for k in t.keywords):
            out.append(t)
    return out


def has_coverage(grade: Any, oblast: Any = "", tema: Any = "") -> bool:
    return bool(select_templates(grade, oblast, tema))


# --------------------------------------------------------------------------- #
# Generated task + generation                                                  #
# --------------------------------------------------------------------------- #
@dataclass
class GeneratedTask:
    skill_id: str
    grade: int | None
    oblast_id: str
    tema_id: str
    question: str
    expected_display: str
    guidable: bool
    source: str = "template"
    validation_status: str = "validated"

    def to_dict(self) -> dict:
        return {
            "skill_id": self.skill_id, "grade": self.grade,
            "oblast_id": self.oblast_id, "tema_id": self.tema_id,
            "question": self.question, "expected_display": self.expected_display,
            "guidable": self.guidable, "source": self.source,
            "validation_status": self.validation_status,
        }


def _validates(question: str, answer: str) -> bool:
    """The code-computed answer MUST be accepted by the deterministic checker."""
    result = check_practice_answer(question, answer)
    if not result.checkable or not result.items:
        return False
    return authoritative_verdict(result) in _POSITIVE or all(
        it.verdict in _POSITIVE for it in result.items
    )


def _generate_from(template: SkillTemplate, rng: random.Random, *,
                   grade: Any, oblast: Any, tema: Any, tries: int = 10) -> GeneratedTask | None:
    for _ in range(tries):
        question, answer = template.generate(rng)
        if _validates(question, answer):
            return GeneratedTask(
                skill_id=template.skill_id, grade=_grade_int(grade),
                oblast_id=_fold(oblast)[:80], tema_id=_fold(tema)[:80],
                question=question, expected_display=answer,
                guidable=template.guidable,
            )
    return None


def generate_one(grade: Any, oblast: Any = "", tema: Any = "", *,
                 seed: Any = "", avoid: set[str] | None = None) -> GeneratedTask | None:
    """One validated task for the selected grade/oblast/tema, or None (no coverage)."""
    templates = select_templates(grade, oblast, tema)
    if not templates:
        return None
    avoid = avoid or set()
    rng = random.Random(hash(("matbot-tt-one", str(seed))))
    order = list(templates)
    rng.shuffle(order)
    for template in order:
        task = _generate_from(template, rng, grade=grade, oblast=oblast, tema=tema)
        if task is not None and task.question not in avoid:
            return task
    return None


def generate_batch(grade: Any, oblast: Any = "", tema: Any = "", *,
                   count: int, seed: Any = "") -> list[GeneratedTask]:
    """Up to ``count`` validated, DISTINCT tasks. Empty list = no coverage."""
    templates = select_templates(grade, oblast, tema)
    if not templates:
        return []
    tasks: list[GeneratedTask] = []
    seen: set[str] = set()
    # Cycle through matching skills for variety; draw fresh params per attempt.
    attempts = 0
    i = 0
    while len(tasks) < count and attempts < count * 12:
        template = templates[i % len(templates)]
        rng = random.Random(hash(("matbot-tt-batch", str(seed), attempts)))
        task = _generate_from(template, rng, grade=grade, oblast=oblast, tema=tema)
        attempts += 1
        i += 1
        if task is None or task.question in seen:
            continue
        seen.add(task.question)
        tasks.append(task)
    return tasks
