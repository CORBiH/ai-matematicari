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

from matbot.minimal import mathfmt
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
    #: Telemetry-grade classification of WHICH concept question this was:
    #: explicit_factor | target_denominator | target_not_multiple |
    #: same_numerator_denominator | why_same_factor
    kind: str = "expand"

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


#: "zašto množimo i brojnik i nazivnik (istim brojem)" — a question about the
#: RULE rather than about specific numbers. It has a verified answer that needs
#: no arithmetic, so it must not fall through to free-form model text.
_WHY_SAME_RE = re.compile(
    r"(za[sš]to|zbog\s+[cč]ega).{0,40}"
    r"(mno[zž]|pro[sš]ir|dijel).{0,40}(brojnik|nazivnik|oba|obje|isti)"
    r"|(za[sš]to).{0,30}isti[m]?\s+broj")


def _why_same_factor_facts() -> ExpandFacts:
    return ExpandFacts(possible=True, kind="why_same_factor")


def resolve_expand_question(raw_question: Any) -> ExpandFacts | None:
    """Facts for a fraction-expansion question, or ``None`` when unparseable.

    ``None`` means "do not let anything calculate" — the caller must fall back
    to a non-numeric explanation or a clarification.
    """
    text = fold(raw_question)
    if not text:
        return None

    # A rule question ("zašto množimo i brojnik i nazivnik?") has a verified
    # answer that involves no arithmetic at all.
    if _WHY_SAME_RE.search(text) and _FRACTION_RE.search(text) is None:
        return _why_same_factor_facts()

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
                kind="target_not_multiple",
                original_numerator=num, original_denominator=den,
                target_denominator=target,
            )
        k = target // den
        return ExpandFacts(
            possible=True, kind="target_denominator",
            original_numerator=num, original_denominator=den,
            target_denominator=target, factor=k,
            expanded_numerator=num * k, expanded_denominator=target,
            value_is_one=(num == den),
        )

    # An explicit factor was named instead.
    if factor is not None and factor > 0:
        return ExpandFacts(
            possible=True, kind="explicit_factor",
            original_numerator=num, original_denominator=den,
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

    if facts.kind == "why_same_factor":
        return (
            "Razlomak pokazuje odnos brojnika i nazivnika. Ako oba pomnožiš "
            "istim brojem, taj odnos ostaje isti, pa se vrijednost razlomka ne "
            "mijenja — dobiješ isti dio, samo zapisan sitnijim dijelovima. "
            "Ako se pomnoži samo brojnik, dobije se veći dio, a to više nije "
            "isti razlomak."
        )

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


# --------------------------------------------------------------------------- #
# Equation concepts, and skill-aware dispatch                                  #
# --------------------------------------------------------------------------- #
# Concept questions used to be answered from EXPANSION facts whenever the skill
# was one of the two fraction skills, and from an expansion-flavoured fallback
# otherwise. An equation skill therefore had no verified concept facts at all,
# and the "no numbers" fallback still talked about expanding fractions. Facts
# are now selected by the RESOLVED SKILL first, and each family has its own
# fallback.

#: "zasto ... obje strane", "zasto istu operaciju", "obje strane jednacine"
_SAME_OPERATION_RE = re.compile(
    # Matched against fold()ed text, so diacritics are already stripped.
    r"obje\s+strane|ista?\s+operacij\w*|istu\s+operacij\w*|ravnotez\w*")

#: A question about the parts of a fraction, asked inside an equation lesson.
_PARTS_RE = re.compile(r"brojnik\w*|nazivnik\w*")


@dataclass(frozen=True)
class EquationConceptFacts:
    """A verified PRINCIPLE. Nothing here is computed from the student's text.

    ``equation`` and ``move`` are filled from the active task when there is
    one, so the explanation can point at the equation in front of the student
    without ever naming its solution.
    """
    kind: str
    equation: str = ""
    move: str = ""

    def to_dict(self) -> dict:
        return {"kind": self.kind, "equation": self.equation, "move": self.move}


def resolve_equation_question(raw_question: Any,
                              equation_facts: Any = None
                              ) -> EquationConceptFacts | None:
    """Concept facts for an EQUATION skill, or None when unrecognised."""
    probe = fold(raw_question)
    if not probe:
        return None
    if not _SAME_OPERATION_RE.search(probe):
        return None

    equation, move = "", ""
    if equation_facts is not None:
        equation = equation_facts.original_equation
        amount = equation_facts.move_amount
        if not equation_facts.subtracted_unknown and equation_facts.coefficient == 1:
            verb = "dodajemo" if equation_facts.removes_by_adding else "oduzimamo"
            move = f"{verb} {amount}"
    return EquationConceptFacts(kind="why_same_operation",
                                equation=equation, move=move)


#: The verified principle, stated without arithmetic.
_BALANCE = (
    "Istu operaciju radimo na obje strane zato što jednačina predstavlja "
    "jednakost. Ako na obje strane primijenimo istu dozvoljenu operaciju, "
    "jednakost ostaje tačna. To je kao vaga: da bi ostala u ravnoteži, "
    "istu promjenu moramo napraviti na obje strane.")


def explain_equation(facts: EquationConceptFacts) -> str:
    """The equation is wrapped here, as ONE span.

    ``format_math_tokens`` wraps each numeric token separately, which would
    render "1/2 + x = 3/5" as three fragments with the operators outside the
    math. Because that helper leaves text containing LaTeX alone, every
    mathematical token in this sentence must be wrapped here.
    """
    text = _BALANCE
    if facts.equation and facts.move:
        verb, _, amount = facts.move.partition(" ")
        text += (f" U jednačini {mathfmt.inline(facts.equation)} {verb} "
                 f"{mathfmt.inline(amount)} s obje strane kako bi jednakost "
                 "ostala sačuvana.")
    elif facts.equation:
        text += (" Isto vrijedi i za tvoju jednačinu "
                 f"{mathfmt.inline(facts.equation)}.")
    return text


#: Which concept family serves which resolved skill. Membership is explicit:
#: two skills sharing the word "fraction" is NOT a reason to share facts.
EXPANSION_SKILLS = frozenset({"fraction_expand", "fraction_add_unlike"})
EQUATION_SKILLS = frozenset({"linear_equation", "fraction_equation_additive"})


def concept_family(skill_id: Any) -> str:
    """"expansion" | "equation" | "" â decided by the resolved skill alone."""
    skill = str(skill_id or "")
    if skill in EQUATION_SKILLS:
        return "equation"
    if skill in EXPANSION_SKILLS:
        return "expansion"
    return ""


def resolve_for_skill(skill_id: Any, raw_question: Any,
                      equation_facts: Any = None):
    """The ONE entry point: facts for this question UNDER this skill.

    Returns ``None`` when the family has no verified facts for the question,
    which callers must render as a safe, skill-relevant clarification rather
    than by borrowing another skill's explanation.
    """
    family = concept_family(skill_id)
    if family == "equation":
        return resolve_equation_question(raw_question, equation_facts)
    if family == "expansion":
        return resolve_expand_question(raw_question)
    return None


def explain_for(facts: Any) -> str:
    """Render whichever fact family was resolved."""
    if isinstance(facts, EquationConceptFacts):
        return explain_equation(facts)
    return explain(facts)


#: Said when an EQUATION skill has no verified facts for the question. It must
#: not mention expanding fractions; that fallback is what reached production.
EQUATION_CONCEPT_FALLBACK = (
    "Dobro pitanje. U ovoj lekciji rješavamo jednačine s razlomcima, pa "
    "mi napiši tačno šta te zanima u vezi s jednačinom i "
    "objasniću korak po korak.")


def is_parts_question(raw_question: Any) -> bool:
    """True for "brojnik/nazivnik" questions, which are relevant to a fraction
    equation but must NOT pull in the generic expansion explanation."""
    return bool(_PARTS_RE.search(fold(raw_question)))
