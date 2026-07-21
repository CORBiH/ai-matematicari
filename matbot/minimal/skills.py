# -*- coding: utf-8 -*-
"""The supported scope, and the ONE owner of topic identity.

A skill is supported only when the engine can BOTH generate a task for it and
check an answer to it deterministically. That is the whole admission rule — no
"probably gradeable", no model fallback inside the core.

Reused from the existing system (proven, deterministic, no contract chain):
  * ``task_templates``  — validated generators
  * ``answer_checker``  — deterministic expected values and checking
  * ``topic_resolver``  — runtime id → canonical NPP tema, built from the
                          curriculum loader

Everything else about those modules (activation gates, V2 flags, prose
handling) is deliberately not imported.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Any

from matbot import task_templates, topic_resolver
from matbot.answer_checker import derive_expected


@dataclass(frozen=True)
class Skill:
    """One deterministically supported skill."""
    skill_id: str
    title: str                      # child-facing name, ijekavica
    template_id: str                # generator in task_templates
    tema_ids: tuple[str, ...]       # canonical NPP temas this skill serves
    keywords: tuple[str, ...]       # tema-title match, diacritic-free


#: The ENTIRE supported scope of this engine. Adding a row is the only way to
#: widen it, and a row is only valid if generation and checking both work
#: (enforced by ``selftest`` and by the test-suite sweep).
SKILLS: tuple[Skill, ...] = (
    Skill("fraction_expand", "Proširivanje razlomaka", "fraction_expand",
          ("6-04-035",), ("prosirivanje razlomaka", "prosiri")),
    Skill("fraction_add_unlike", "Sabiranje razlomaka različitih nazivnika",
          "fraction_add_sub", ("6-04-040",),
          ("sabiranje razlomaka", "oduzimanje razlomaka", "razlicitih nazivnika")),
    Skill("linear_equation", "Jednostavne linearne jednačine", "linear_equation",
          (), ("linearn", "jednacin")),
    Skill("divisibility", "Djeljivost brojeva", "divisibility_by_6",
          (), ("djeljivost", "djeljiv")),
    Skill("prime_factorization", "Rastavljanje na proste faktore",
          "prime_factorization", (), ("proste faktore", "rastavljanje")),
)

_BY_ID = {s.skill_id: s for s in SKILLS}
SUPPORTED_GRADES = (6,)


def _fold(text: Any) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", str(text or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


@dataclass(frozen=True)
class Topic:
    """Concept: topic identity. ONE owner, one object.

    ``runtime_id`` is exactly what the client sent; ``npp_id``/``title`` are
    canonical. Keeping them separate is what stops an unresolved runtime id from
    looking like "no topic selected".
    """
    grade: Any
    runtime_id: str = ""
    npp_id: str = ""
    title: str = ""
    skill_id: str = ""              # "" = not supported by this engine

    @property
    def supported(self) -> bool:
        return bool(self.skill_id) and self.grade in SUPPORTED_GRADES

    @property
    def skill(self) -> Skill | None:
        return _BY_ID.get(self.skill_id)

    def to_dict(self) -> dict:
        return {"grade": self.grade, "runtime_id": self.runtime_id,
                "npp_id": self.npp_id, "title": self.title,
                "skill_id": self.skill_id}

    @classmethod
    def from_dict(cls, raw: Any) -> "Topic":
        raw = raw if isinstance(raw, dict) else {}
        return cls(grade=raw.get("grade"),
                   runtime_id=str(raw.get("runtime_id") or "")[:80],
                   npp_id=str(raw.get("npp_id") or "")[:40],
                   title=str(raw.get("title") or "")[:120],
                   skill_id=str(raw.get("skill_id") or "")[:40])


def resolve_topic(grade: Any, selected_topic: Any, selected_oblast: Any = "") -> Topic:
    """The selected topic is AUTHORITATIVE.

    It is resolved through the curriculum loader, never inferred from the
    student's words, and never widened: an unsupported tema yields a Topic with
    ``skill_id == ""`` rather than a nearby skill.
    """
    runtime = str(selected_topic or "").strip()
    canonical = topic_resolver.resolve_topic(grade, runtime) if runtime else None
    npp = canonical.npp_id if canonical else ""
    title = canonical.tema if canonical else ""

    skill_id = ""
    for skill in SKILLS:
        if npp and npp in skill.tema_ids:
            skill_id = skill.skill_id
            break
    if not skill_id and title:
        probe = _fold(title)
        for skill in SKILLS:
            if any(k in probe for k in skill.keywords):
                skill_id = skill.skill_id
                break
    # An OBLAST-only selection is honoured only when it names one of our skills
    # exactly; it is never used to stand in for an unresolved tema.
    if not skill_id and not runtime and selected_oblast:
        probe = _fold(selected_oblast)
        for skill in SKILLS:
            if any(k in probe for k in skill.keywords):
                skill_id = skill.skill_id
                break
    return Topic(grade=grade, runtime_id=runtime, npp_id=npp, title=title,
                 skill_id=skill_id)


MIN_DIFFICULTY = 1
MAX_DIFFICULTY = 3
DEFAULT_DIFFICULTY = 1


def clamp_difficulty(level: Any) -> int:
    try:
        value = int(level)
    except (TypeError, ValueError):
        return DEFAULT_DIFFICULTY
    return max(MIN_DIFFICULTY, min(value, MAX_DIFFICULTY))


#: Objective parameter bands per level: (denominators, expansion factors).
#: Difficulty is a property of the NUMBERS, not of the wording — "teži" only
#: means anything if the generated task is measurably harder.
_EXPAND_BANDS: dict[int, tuple[tuple[int, ...], tuple[int, int]]] = {
    1: ((2, 3, 4), (2, 4)),
    2: ((4, 5, 6, 8), (4, 7)),
    3: ((6, 8, 9, 10, 12), (6, 12)),
}


def expand_params(question: str) -> tuple[int, int, int] | None:
    """``(numerator, denominator, factor)`` parsed back out of a task.

    Lets tests assert that a generated task really belongs to its band, rather
    than trusting the generator's own claim.
    """
    m = re.search(r"prosiri\s+(\d+)\s*/\s*(\d+)\s+na\s+nazivnik\s+(\d+)",
                  _fold(question))
    if not m:
        return None
    a, b, target = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if b == 0 or target % b != 0:
        return None
    return a, b, target // b


def band_for(level: int) -> tuple[tuple[int, ...], tuple[int, int]]:
    return _EXPAND_BANDS[clamp_difficulty(level)]


def _generate_expand(rng: random.Random, level: int) -> tuple[str, str]:
    """Fraction expansion at an explicit difficulty band."""
    denominators, (k_lo, k_hi) = band_for(level)
    b = rng.choice(denominators)
    a = rng.randint(1, b - 1)
    k = rng.randint(k_lo, k_hi)
    return f"Proširi {a}/{b} na nazivnik {b * k}.", f"{a * k}/{b * k}"


#: Skills with a real difficulty model. Others accept and store a level but
#: generate from the shared template — so nothing is ever CLAIMED to be harder
#: than it is.
_DIFFICULTY_AWARE = {"fraction_expand": _generate_expand}


def supports_difficulty(skill_id: str) -> bool:
    return skill_id in _DIFFICULTY_AWARE


def generate_question(skill_id: str, seed: Any, avoid: Any = (),
                      difficulty: Any = DEFAULT_DIFFICULTY) -> tuple[str, str] | None:
    """A validated (question, expected_display) pair, or None.

    Every candidate must derive an expected value — a task this engine cannot
    check is a task it will not ask.
    """
    skill = _BY_ID.get(skill_id)
    if skill is None:
        return None
    level = clamp_difficulty(difficulty)
    generator = _DIFFICULTY_AWARE.get(skill_id)
    template = task_templates._BY_ID.get(skill.template_id)
    if generator is None and template is None:
        return None
    avoid_folded = {_fold(a) for a in (avoid or ()) if str(a or "").strip()}
    for attempt in range(60):
        rng = random.Random(f"minimal|{skill_id}|{level}|{seed}|{attempt}")
        if generator is not None:
            question, expected = generator(rng, level)
        else:
            question, expected = template.generate(rng)
        if not question or _fold(question) in avoid_folded:
            continue
        if derive_expected(question) is None:
            continue                    # not checkable → never asked
        if not task_templates.quality_ok(skill.template_id, question, expected):
            continue                    # pedagogically trivial
        return question, expected
    return None


def selftest() -> list[str]:
    """Problems with the declared scope. Empty list = the scope is honest."""
    problems: list[str] = []
    for skill in SKILLS:
        made = generate_question(skill.skill_id, seed="selftest")
        if made is None:
            problems.append(f"{skill.skill_id}: cannot generate")
            continue
        question, _expected = made
        if derive_expected(question) is None:
            problems.append(f"{skill.skill_id}: not checkable")
    return problems
