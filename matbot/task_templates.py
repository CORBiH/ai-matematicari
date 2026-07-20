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


# A canonical NPP tema id, e.g. "6-04-035". Its presence in a probe means the
# tema identity was RESOLVED, so keyword widening must not apply.
_CANONICAL_ID_RE = re.compile(r"\b\d-\d{2}-\d{3}\b")


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
    """Zadatak traži OBRAZLOŽENJE, pa kanonski odgovor mora sadržavati i pravilo
    (golo "da" je nepotpun odgovor — vidi multi-condition grading)."""
    n = 6 * rng.randint(3, 45) if rng.random() < 0.65 else rng.randint(20, 260)
    q = f"Provjeri da li je broj {n} djeljiv sa 6. Obrazloži svoj odgovor."
    if n % 6 == 0:
        return q, f"da, {n} je djeljiv sa 6 jer je paran i zbir cifara mu je djeljiv sa 3"
    return q, f"ne, {n} nije djeljiv sa 6 jer ne ispunjava oba uslova (2 i 3)"


def _g_prime_factorization(rng: random.Random) -> tuple[str, str]:
    n = rng.choice([12, 18, 20, 24, 28, 30, 36, 40, 42, 45, 48, 50, 54, 56, 60, 72, 84, 90, 100])
    return f"Rastavi {n} na proste faktore.", "*".join(str(p) for p in _prime_factors(n))


def _g_gcd(rng: random.Random) -> tuple[str, str]:
    a, b = rng.randint(6, 60), rng.randint(6, 60)
    while a == b:                                  # NZD(32, 32) je trivijalan
        b = rng.randint(6, 60)
    return f"Odredi NZD({a}, {b}).", str(math.gcd(a, b))


def _g_lcm(rng: random.Random) -> tuple[str, str]:
    a, b = rng.randint(2, 15), rng.randint(2, 15)
    while a == b:                                  # NZS(x, x) je trivijalan
        b = rng.randint(2, 15)
    return f"Odredi NZS({a}, {b}).", str(a * b // math.gcd(a, b))


def _g_fraction_add_sub(rng: random.Random) -> tuple[str, str]:
    b, d = rng.choice(_UNLIKE_DENOMS)              # unlike denominators → guidable
    a, c = rng.randint(1, b - 1), rng.randint(1, d - 1)
    op = rng.choice(["+", "-"])
    val = Fraction(a, b) + Fraction(c, d) if op == "+" else Fraction(a, b) - Fraction(c, d)
    if val <= 0:                                    # keep it positive
        op, val = "+", Fraction(a, b) + Fraction(c, d)
    return f"Izračunaj: {a}/{b} {op} {c}/{d}.", _frac_str(val)


def _g_fraction_expand(rng: random.Random) -> tuple[str, str]:
    """Proširivanje razlomaka: a/b → (a·k)/(b·k) na zadani nazivnik."""
    b = rng.choice([2, 3, 4, 5, 6, 8])
    a = rng.randint(1, b - 1)
    k = rng.choice([2, 3, 4, 5])
    return f"Proširi {a}/{b} na nazivnik {b * k}.", f"{a * k}/{b * k}"


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
    """A skill template.

    Selection is TEMA-FIRST: ``tema_ids`` (stable NPP identifiers) beat
    ``tema_keywords`` (tema-name match), which beat ``oblast_keywords``. A tema is
    never allowed to collapse into its broader oblast (see ``select_templates``).
    """
    skill_id: str
    grades: tuple[int, ...]
    oblast_keywords: tuple[str, ...]    # matched against the OBLAST only
    generate: Callable[[random.Random], tuple[str, str]]
    tema_ids: tuple[str, ...] = ()      # stable NPP tema ids (exact match)
    tema_keywords: tuple[str, ...] = () # tema-NAME keywords (specific, not oblast-wide)

    @property
    def guidable(self) -> bool:
        return self.skill_id in GUIDABLE_SKILLS


_TEMPLATES: tuple[SkillTemplate, ...] = (
    SkillTemplate("divisibility_by_6", (6,), ("djeljiv", "djelji"), _g_divisibility6,
                  tema_keywords=("djeljivost", "djeljiv")),
    SkillTemplate("prime_factorization", (6,), ("djeljiv", "prost", "faktor", "prirodn"),
                  _g_prime_factorization,
                  tema_keywords=("prost", "faktor", "rastavljanje")),
    SkillTemplate("gcd", (6,), ("djeljiv", "nzd", "najveci zajednicki"), _g_gcd,
                  tema_keywords=("nzd", "najveci zajednicki")),
    SkillTemplate("lcm", (6,), ("djeljiv", "nzs", "najmanji zajednicki"), _g_lcm,
                  tema_keywords=("nzs", "najmanji zajednicki")),
    # --- Razlomci: each tema maps to exactly ONE skill (no collapsing) ---------
    SkillTemplate("fraction_expand", (6,), ("razlom",), _g_fraction_expand,
                  tema_ids=("6-04-035",),
                  tema_keywords=("prosirivanje", "prosiri")),
    SkillTemplate("fraction_add_sub", (6, 7), ("razlom", "racionaln"), _g_fraction_add_sub,
                  tema_ids=("6-04-040",),
                  tema_keywords=("sabiranje", "oduzimanje", "razlicitih imenilaca",
                                 "razlicitih nazivnika")),
    SkillTemplate("fraction_mul", (6, 7), ("razlom", "racionaln"), _g_fraction_mul,
                  tema_ids=("6-04-041",),
                  tema_keywords=("mnozenje", "mnozenja")),
    SkillTemplate("percent_of", (6, 7), ("postotak", "procent", "razmjer"), _g_percent_of,
                  tema_keywords=("postotak", "procent")),
    SkillTemplate("unit_conversion", (6,), ("mjerenje", "mjerne", "jedinic"), _g_unit_conversion,
                  tema_keywords=("mjerne jedinic", "pretvaranje")),
    SkillTemplate("linear_equation", (6, 7), ("jednacin", "jednacine", "izraz"), _g_linear_equation,
                  tema_keywords=("jednacin",)),
    SkillTemplate("triangle_angle", (6, 7), ("ugao", "ugl", "trougao", "trokut"), _g_triangle_angle,
                  tema_keywords=("ugao", "ugl", "trougao", "trokut")),
    SkillTemplate("set_union", (6, 7), ("skup",), _g_set_union,
                  tema_keywords=("skup", "unija", "presjek")),
)

_BY_ID = {t.skill_id: t for t in _TEMPLATES}


def _grade_int(grade: Any) -> int | None:
    try:
        return int(grade)
    except (TypeError, ValueError):
        return None


def select_templates(grade: Any, oblast: Any = "", tema: Any = "") -> list[SkillTemplate]:
    """Templates for the selected grade, with TEMA-FIRST precedence.

    1. A selected tema is matched by stable ``tema_ids`` (exact NPP id) first,
       then by specific ``tema_keywords`` (tema NAME).
    2. If a tema IS selected but nothing matches it, the result is EMPTY — a tema
       must NEVER silently collapse into its broader oblast (that produced
       "Proširivanje razlomaka" → fraction multiplication). Callers fall back
       explicitly.
    3. Only when no tema is selected do we match ``oblast_keywords``.
    4. With neither, all grade-matching templates apply (generic).
    """
    g = _grade_int(grade)
    graded = [t for t in _TEMPLATES if g is None or g in t.grades]
    tema_probe = _fold(tema)

    if tema_probe:
        by_id = [t for t in graded if any(tid in tema_probe for tid in t.tema_ids)]
        if by_id:
            return by_id
        if _CANONICAL_ID_RE.search(tema_probe):
            # The tema resolved to a canonical NPP id, so its identity is KNOWN and
            # authoritative. Matching its title by keyword would silently widen the
            # tema ("Pojam skupa" → set-union templates), so an unlisted canonical
            # tema is simply uncovered.
            return []
        by_name = [t for t in graded if any(k in tema_probe for k in t.tema_keywords)]
        return by_name                  # possibly [] → explicit "no coverage"

    oblast_probe = _fold(oblast)
    if oblast_probe:
        return [t for t in graded if any(k in oblast_probe for k in t.oblast_keywords)]
    return graded


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


_TRIVIAL_PAIR_RE = re.compile(r"\((\d+),\s*(\d+)\)")


def quality_ok(skill_id: str, question: str, answer: str) -> bool:
    """Pedagoške granice — zadatak mora biti i SMISLEN, ne samo ocjenjiv.

    Odbacuje trivijalne instance (npr. "Odredi NZD(32, 32).") koje ne provjeravaju
    ništa. Poziva se uz ``_validates`` pri svakoj generaciji."""
    if skill_id in ("gcd", "lcm"):
        m = _TRIVIAL_PAIR_RE.search(question)
        if m and m.group(1) == m.group(2):
            return False                            # identični operandi
    if skill_id == "fraction_mul":
        # x/y · 1/1 i slično ne provjerava množenje
        if re.search(r"1\s*/\s*1", question):
            return False
    if skill_id == "fraction_expand":
        # "proširi a/b na nazivnik b" nije proširivanje
        m = re.search(r"(\d+)\s*/\s*(\d+)\s+na\s+nazivnik\s+(\d+)", question)
        if m and m.group(2) == m.group(3):
            return False
    if skill_id == "percent_of" and re.search(r"100\s*%", question):
        return False
    return True


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
        if _validates(question, answer) and quality_ok(template.skill_id, question, answer):
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


def _numbers(question: str) -> list[int]:
    return [int(n) for n in re.findall(r"\d+", question)][:6]


def _too_similar(candidate: GeneratedTask, accepted: list[GeneratedTask]) -> bool:
    """Near-duplicate guard for items of the SAME skill.

    An exam of "2x + 5 = 7", "6x + 4 = 28", "2x + 9 = 21" is three valid items
    that read as one. Exact-string dedup cannot see that, so compare the numeric
    fingerprint: sharing most parameters, or landing in the same narrow
    magnitude band, means the child is re-doing the same exercise.
    """
    cand_nums = _numbers(candidate.question)
    if not cand_nums:
        return False
    for other in accepted:
        if other.skill_id != candidate.skill_id:
            continue
        nums = _numbers(other.question)
        if not nums:
            continue
        shared = len(set(cand_nums) & set(nums))
        if shared >= max(1, min(len(cand_nums), len(nums)) - 1):
            return True                     # same parameters but for one
        # Same difficulty band on every parameter → mechanically identical feel.
        if len(nums) == len(cand_nums) and all(
                _band(a) == _band(b) for a, b in zip(sorted(nums), sorted(cand_nums))):
            return True
    return False


def _band(n: int) -> int:
    """Coarse magnitude band, so difficulty varies rather than just digits."""
    if n <= 5:
        return 0
    if n <= 12:
        return 1
    if n <= 30:
        return 2
    if n <= 100:
        return 3
    return 4


def generate_batch(grade: Any, oblast: Any = "", tema: Any = "", *,
                   count: int, seed: Any = "") -> list[GeneratedTask]:
    """Up to ``count`` validated, DISTINCT, non-near-duplicate tasks.

    Empty list = no coverage. Variety is only ever sought WITHIN the templates
    that match the selected topic — never by widening the topic.
    """
    templates = select_templates(grade, oblast, tema)
    if not templates:
        return []
    tasks: list[GeneratedTask] = []
    seen: set[str] = set()
    # Cycle through matching skills for variety; draw fresh params per attempt.
    attempts = 0
    i = 0
    budget = count * 40
    while len(tasks) < count and attempts < budget:
        template = templates[i % len(templates)]
        rng = random.Random(hash(("matbot-tt-batch", str(seed), attempts)))
        task = _generate_from(template, rng, grade=grade, oblast=oblast, tema=tema)
        attempts += 1
        i += 1
        if task is None or task.question in seen:
            continue
        # Relax the similarity guard once the budget is nearly spent: a valid
        # item the child can still solve beats returning a short exam.
        if attempts < budget * 0.75 and _too_similar(task, tasks):
            continue
        seen.add(task.question)
        tasks.append(task)
    return tasks
