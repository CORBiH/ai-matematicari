"""Engine V2 — Phase 3: SolutionPlan + StepCursor (the missing durable object).

A ``SolutionPlan`` is a machine-readable, deterministic decomposition of a
multi-step task into ordered ``Step``s. A ``StepCursor`` tracks progress and
advances DETERMINISTICALLY from a per-step deterministic check — independent of
the tutor's free prose. This lets a guided flow complete regardless of what the
model says (fixes the "240 ÷ 6 stops after 'divisible by 2'" trigger bug).

Phase 3 scope (limited + reversible): a plan is built ONLY for tasks that
genuinely benefit from multiple guided steps and are already deterministically
checkable. Seeded skills:
  * divisibility_by_6      (composite divisor 2·3, with explanation)
  * prime_factorization    (successive-prime ladder; composite numbers only)
  * linear_equation        (ax + b = c with a≠±1 and b≠0 → 2 genuine steps)
  * fraction_add_sub       (unlike denominators → common denominator then result)

Atomic tasks (x + b = c, a·x = c, same-denominator or multiplicative fractions,
prime numbers) get NO plan. The module is serializable and imports only
``answer_checker`` (a leaf) — no import cycle.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from fractions import Fraction
from math import gcd
from typing import Any

from matbot.answer_checker import check_practice_answer, split_numbered_items
from matbot import turn_intent

# Turn classifications produced by ``classify_turn``.
CORRECT_STEP = "correct_step"
WRONG_STEP = "wrong_step"
FINAL_CORRECT = "final_correct"
FINAL_WRONG = "final_wrong"
HELP = "help"
UNCLEAR = "unclear"

_FINAL_POSITIVE = {
    "correct", "correct_equivalent_form", "correct_missing_notation",
    "correct_missing_unit",
}
_FINAL_INCORRECT = {"incorrect", "wrong_unit"}


def _fold(text: Any) -> str:
    s = unicodedata.normalize("NFKD", str(text or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


_YES_RE = re.compile(r"\b(da|jest|jeste|jesu|tacn\w*|djeljiv\w*|dijeli\s+se|moze|moguce)\b")
_NO_RE = re.compile(r"\b(ne|nije|nisu|nedjeljiv\w*|ne\s+dijeli|nemoguce)\b")
# Help detection is NOT redefined here — the Practice Step Engine consumes the
# shared ``turn_intent`` classifier, so it cannot disagree with the exam or the
# explanation flow about what "ne znam" means.


def _is_help(message: Any) -> bool:
    """``include_follow_up=False`` preserves the pre-migration verdict for
    "zašto?" (UNCLEAR, not HELP) — flipping it is a pedagogical change, not part
    of this consolidation."""
    return turn_intent.wants_support(message, include_follow_up=False)


def _digit_sum(n: int) -> int:
    return sum(int(d) for d in str(abs(n)))


def _yes_no(message: str) -> bool | None:
    folded = _fold(message)
    if _NO_RE.search(folded):
        return False
    if _YES_RE.search(folded):
        return True
    return None


def _numbers_in(text: Any) -> set[Fraction]:
    """All integers and a/b fractions in the text, as Fractions."""
    s = _fold(text)
    out: set[Fraction] = set()
    for a, b in re.findall(r"(-?\d+)\s*/\s*(\d+)", s):
        if int(b) != 0:
            out.add(Fraction(int(a), int(b)))
    # bare integers (avoid double-counting fraction parts is fine — sets dedupe)
    for m in re.finditer(r"(?<![\d/])(-?\d+)(?![\d/])", s):
        out.add(Fraction(int(m.group(1))))
    return out


def _lcm(a: int, b: int) -> int:
    return abs(a * b) // gcd(a, b) if a and b else 0


@dataclass
class Step:
    id: str
    prompt: str                     # what to ask the student for this step
    kind: str                       # div_by | digit_sum_div | final_divisible_by
                                    # | number_equals | final_delegate
    params: dict = field(default_factory=dict)
    hint: str = ""                  # a hint for THIS step only (help handling)
    requires: tuple[str, ...] = ()

    def expected_bool(self) -> bool:
        n = int(self.params.get("n", 0))
        k = int(self.params.get("k", 1)) or 1
        return (n % k) == 0


@dataclass
class SolutionPlan:
    skill_id: str
    steps: list[Step]

    def step(self, step_id: str | None) -> Step | None:
        for s in self.steps:
            if s.id == step_id:
                return s
        return None

    def first_step_id(self) -> str | None:
        return self.steps[0].id if self.steps else None

    def next_step_id(self, step_id: str | None) -> str | None:
        ids = [s.id for s in self.steps]
        if step_id not in ids:
            return None
        i = ids.index(step_id)
        return ids[i + 1] if i + 1 < len(ids) else None

    def is_final(self, step_id: str | None) -> bool:
        return bool(self.steps) and step_id == self.steps[-1].id


@dataclass
class StepCursor:
    skill_id: str
    active_step_id: str | None
    completed_step_ids: list[str] = field(default_factory=list)
    is_complete: bool = False

    def refers_to(self, plan: SolutionPlan) -> str:
        return "whole_task" if plan.is_final(self.active_step_id) or self.is_complete else "substep"

    def to_dict(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "active_step_id": self.active_step_id,
            "completed_step_ids": list(self.completed_step_ids),
            "is_complete": bool(self.is_complete),
        }


# --------------------------------------------------------------------------- #
# Plan builders — ONLY deterministically supported, genuinely multi-step tasks #
# --------------------------------------------------------------------------- #
def _divisibility_by_6_plan(n: int) -> SolutionPlan:
    ds = _digit_sum(n)
    return SolutionPlan(
        skill_id="divisibility_by_6",
        steps=[
            Step(id="div2", kind="div_by", params={"n": n, "k": 2},
                 prompt=f"Da bi broj bio djeljiv sa 6, mora biti djeljiv i sa 2 i sa 3. "
                        f"Prvo: je li {n} djeljiv sa 2?",
                 hint="Broj je djeljiv sa 2 ako mu je posljednja cifra parna (0,2,4,6,8)."),
            Step(id="div3", kind="digit_sum_div", params={"n": n, "k": 3},
                 prompt=f"Sada djeljivost sa 3: saberi cifre broja {n} (zbir je {ds}) — "
                        f"je li taj zbir djeljiv sa 3?",
                 hint=f"Saberi cifre: {'+'.join(str(n).lstrip('-'))} = {ds}. Je li {ds} djeljivo sa 3?"),
            Step(id="final", kind="final_divisible_by", params={"n": n, "k": 6},
                 requires=("div2", "div3"),
                 prompt=f"Zaključi: je li {n} djeljiv sa 6? Napiši puno obrazloženje "
                        f"(djeljiv sa 2 i sa 3, pa je djeljiv sa 6).",
                 hint="Ako je broj djeljiv i sa 2 i sa 3, onda je djeljiv i sa 6."),
        ],
    )


def _prime_factors(n: int) -> list[int]:
    factors: list[int] = []
    d, m = 2, n
    while d * d <= m:
        while m % d == 0:
            factors.append(d)
            m //= d
        d += 1
    if m > 1:
        factors.append(m)
    return factors


def _prime_factorization_plan(n: int) -> SolutionPlan | None:
    factors = _prime_factors(n)
    if len(factors) < 2:                 # prime or 1 → atomic, no plan
        return None
    steps: list[Step] = []
    current = n
    for i, p in enumerate(factors, start=1):
        q = current // p
        steps.append(Step(
            id=f"p{i}", kind="number_equals",
            params={"values": [p, q]},   # accept the prime OR the quotient
            prompt=f"Rastavljamo korak po korak. Koji je najmanji prosti broj "
                   f"kojim možeš podijeliti {current}? (i koliko je {current} : taj broj)",
            hint=f"Provjeri redom: dijeli li se {current} sa 2, pa sa 3, pa sa 5, sa 7…",
        ))
        current = q
    steps.append(Step(
        id="final", kind="final_delegate", params={"task": f"Rastavi {n} na proste faktore."},
        prompt=f"Sada napiši rastavljanje broja {n} kao proizvod prostih faktora "
               f"(npr. u obliku 2·2·3 ili 2²·3).",
        hint="Pomnoži sve proste faktore koje si redom dobio.",
    ))
    return SolutionPlan(skill_id="prime_factorization", steps=steps)


def _linear_equation_plan(a: int, b: int, c: int, var: str = "x") -> SolutionPlan | None:
    # ax + b = c ; a genuine 2-step task only when a≠±1 and b≠0.
    if a in (0, 1, -1) or b == 0:
        return None
    rhs = c - b                          # ax = c - b
    op = "oduzmi" if b > 0 else "dodaj"
    return SolutionPlan(
        skill_id="linear_equation",
        steps=[
            Step(id="isolate", kind="number_equals", params={"values": [rhs]},
                 prompt=f"Prvo osamostali član sa {var}: {op} {abs(b)} s obje strane. "
                        f"Koliko je onda {a}{var}?",
                 hint=f"{op.capitalize()} {abs(b)} s obje strane: {a}{var} = {c} {'-' if b>0 else '+'} {abs(b)}."),
            Step(id="final", kind="final_delegate",
                 params={"task": f"Riješi jednačinu {a}{var} {'+' if b>=0 else '-'} {abs(b)} = {c}."},
                 prompt=f"Sada podijeli obje strane sa {a}. Koliko je {var}?",
                 hint=f"{var} = {rhs} : {a}."),
        ],
    )


def _fraction_add_sub_plan(a: Fraction, b: Fraction, op: str, raw_task: str) -> SolutionPlan | None:
    # a/b ± c/d with UNLIKE denominators → common denominator, then result.
    if a.denominator == b.denominator:
        return None                      # same denominator → atomic
    lcd = _lcm(a.denominator, b.denominator)
    sign = "+" if op == "+" else "-"
    return SolutionPlan(
        skill_id="fraction_add_sub",
        steps=[
            Step(id="common_denom", kind="number_equals", params={"values": [lcd]},
                 prompt=f"Nazivnici su različiti. Koji je zajednički nazivnik "
                        f"(najmanji zajednički sadržalac nazivnika)?",
                 hint=f"Traži najmanji broj djeljiv i sa {a.denominator} i sa {b.denominator}."),
            Step(id="final", kind="final_delegate", params={"task": raw_task},
                 prompt=f"Svedi oba razlomka na nazivnik {lcd}, pa ih "
                        f"{'saberi' if sign=='+' else 'oduzmi'} i napiši rezultat.",
                 hint=f"Proširi svaki razlomak na nazivnik {lcd}, pa {sign} brojnike."),
        ],
    )


# --- task-text detectors -----------------------------------------------------
_DIV6_ASK_RE = re.compile(r"djeljiv\w*\s+sa\s+6\b|djeljiv\w*\s+sa\s+sest\b")
_EXPLAIN_RE = re.compile(r"obrazlo\w*|objasn\w*|zasto|zbog\s+cega")
_PRIMEFACT_RE = re.compile(
    r"rastavi\w*.{0,30}proste?\s+(faktor\w*|cinioc\w*|cinilac\w*)"
    r"|proste?\s+(faktor\w*|cinioc\w*).{0,30}rastav\w*"
    r"|faktorizuj\w*|faktorizir\w*"
)
_FRAC_TERM_RE = re.compile(r"(-?\d+)\s*/\s*(\d+)")


def _try_divisibility_by_6(folded: str, raw: str) -> SolutionPlan | None:
    if not _DIV6_ASK_RE.search(folded) or not _EXPLAIN_RE.search(folded):
        return None
    m = re.search(r"\b(\d{1,7})\b", folded)
    return _divisibility_by_6_plan(int(m.group(1))) if m else None


def _try_prime_factorization(folded: str, raw: str) -> SolutionPlan | None:
    if not _PRIMEFACT_RE.search(folded):
        return None
    m = re.search(r"\b(\d{2,7})\b", folded)
    return _prime_factorization_plan(int(m.group(1))) if m else None


def _try_linear_equation(folded: str, raw: str) -> SolutionPlan | None:
    # match: a x + b = c   (a,b,c integers, spaces optional, var x)
    m = re.search(r"(-?\d+)\s*([a-z])\s*([+-])\s*(\d+)\s*=\s*(-?\d+)", folded)
    if not m:
        return None
    a = int(m.group(1)); var = m.group(2)
    b = int(m.group(4)) * (1 if m.group(3) == "+" else -1)
    c = int(m.group(5))
    return _linear_equation_plan(a, b, c, var)


def _try_fraction_add_sub(folded: str, raw: str) -> SolutionPlan | None:
    m = re.search(r"(-?\d+)\s*/\s*(\d+)\s*([+-])\s*(-?\d+)\s*/\s*(\d+)", folded)
    if not m:
        return None
    a = Fraction(int(m.group(1)), int(m.group(2)))
    b = Fraction(int(m.group(4)), int(m.group(5)))
    return _fraction_add_sub_plan(a, b, m.group(3), raw)


_DETECTORS = (
    _try_divisibility_by_6,
    _try_prime_factorization,
    _try_linear_equation,
    _try_fraction_add_sub,
)


def build_plan_for_task(task_text: Any) -> SolutionPlan | None:
    """Return a SolutionPlan for a supported, genuinely multi-step task, else
    ``None`` (atomic or unsupported)."""
    raw = str(task_text or "")
    folded = _fold(raw)
    if not folded:
        return None
    # Multi-item (numbered) tasks are graded item-by-item, not guided step-by-step.
    if len(split_numbered_items(raw)) >= 2:
        return None
    for detector in _DETECTORS:
        plan = detector(folded, raw)
        if plan is not None:
            return plan
    return None


def cursor_for_task(task_text: Any, prior: Any = None) -> tuple[SolutionPlan, StepCursor] | None:
    plan = build_plan_for_task(task_text)
    if plan is None:
        return None
    cursor = normalize_cursor(prior)
    valid_ids = [s.id for s in plan.steps] + [None]
    if cursor is None or cursor.skill_id != plan.skill_id or cursor.active_step_id not in valid_ids:
        cursor = StepCursor(skill_id=plan.skill_id, active_step_id=plan.first_step_id())
    return plan, cursor


# --------------------------------------------------------------------------- #
# Per-step deterministic checks + turn classification                          #
# --------------------------------------------------------------------------- #
def check_step(step: Step, message: Any) -> str:
    """Deterministic per-step verdict: correct_step | wrong_step | help | unclear.
    NEVER consults tutor prose — only the student's message and step params."""
    folded = _fold(message)
    if _is_help(message):
        return HELP
    if step.kind in ("div_by", "final_divisible_by"):
        ans = _yes_no(folded)
        if ans is None:
            return UNCLEAR
        return CORRECT_STEP if ans == step.expected_bool() else WRONG_STEP
    if step.kind == "digit_sum_div":
        n = int(step.params.get("n", 0)); k = int(step.params.get("k", 1)) or 1
        ds = _digit_sum(n)
        ans = _yes_no(folded)
        if ans is not None:
            return CORRECT_STEP if ans == ((ds % k) == 0) else WRONG_STEP
        if Fraction(ds) in _numbers_in(folded):
            return CORRECT_STEP if (ds % k) == 0 else WRONG_STEP
        return UNCLEAR
    if step.kind == "number_equals":
        values = {Fraction(v) for v in step.params.get("values", [])}
        nums = _numbers_in(folded)
        if not nums:
            return UNCLEAR
        return CORRECT_STEP if (nums & values) else WRONG_STEP
    if step.kind == "final_delegate":
        task = str(step.params.get("task") or "")
        result = check_practice_answer(task, str(message or ""))
        if not result.checkable or not result.items:
            return UNCLEAR
        v = result.items[0].verdict
        if v in _FINAL_POSITIVE:
            return CORRECT_STEP
        if v in _FINAL_INCORRECT:
            return WRONG_STEP
        return UNCLEAR
    return UNCLEAR


def classify_turn(plan: SolutionPlan, cursor: StepCursor, message: Any) -> str:
    step = plan.step(cursor.active_step_id)
    if step is None:
        return UNCLEAR
    # A COMPLETE correct final answer given at any step solves the whole task —
    # a student who already knows the result should not be forced to step through.
    # Only for value-based (final_delegate) finals: for yes/no finals the
    # intermediate answers are indistinguishable from the conclusion.
    final = plan.steps[-1] if plan.steps else None
    if (final is not None and final.kind == "final_delegate" and step.id != final.id
            and not _is_help(message)
            and check_step(final, message) == CORRECT_STEP):
        return FINAL_CORRECT
    verdict = check_step(step, message)
    if plan.is_final(step.id):
        if verdict == CORRECT_STEP:
            return FINAL_CORRECT
        if verdict == WRONG_STEP:
            return FINAL_WRONG
        return verdict
    return verdict


def advance(plan: SolutionPlan, cursor: StepCursor, classification: str) -> StepCursor:
    """Return a NEW cursor advanced per the classification (deterministic):

      * correct_step  -> complete current step, move to next (task NOT complete)
      * final_correct -> complete final step, cursor.is_complete = True
      * wrong / help / unclear -> stay on the current step (no reveal, no complete)
    """
    completed = list(cursor.completed_step_ids)
    active = cursor.active_step_id
    is_complete = cursor.is_complete
    if classification == CORRECT_STEP:
        if active and active not in completed:
            completed.append(active)
        active = plan.next_step_id(active)
    elif classification == FINAL_CORRECT:
        if active and active not in completed:
            completed.append(active)
        active = None
        is_complete = True
    return StepCursor(
        skill_id=cursor.skill_id,
        active_step_id=active,
        completed_step_ids=completed,
        is_complete=is_complete,
    )


def active_prompt(plan: SolutionPlan, cursor: StepCursor) -> str:
    step = plan.step(cursor.active_step_id)
    return step.prompt if step else ""


def active_hint(plan: SolutionPlan, cursor: StepCursor) -> str:
    step = plan.step(cursor.active_step_id)
    return step.hint if step else ""


def normalize_cursor(raw: Any) -> StepCursor | None:
    if not isinstance(raw, dict):
        return None
    skill = str(raw.get("skill_id") or "").strip()[:80]
    if not skill:
        return None
    active = raw.get("active_step_id")
    active = str(active).strip()[:40] if active is not None else None
    completed = []
    for x in raw.get("completed_step_ids") or []:
        s = str(x).strip()[:40]
        if s and s not in completed:
            completed.append(s)
    return StepCursor(
        skill_id=skill,
        active_step_id=active or None,
        completed_step_ids=completed,
        is_complete=bool(raw.get("is_complete")),
    )
