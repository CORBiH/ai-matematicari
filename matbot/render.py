# -*- coding: utf-8 -*-
"""Lifecycle wording for children in grades 6–9.

The deterministic engines were mathematically right and read like a machine:
"Netačno. Sada riješi 2. zadatak." three turns in a row. Wording was also
invented independently by the exam engine, the practice flow and the grading
guard, so tone drifted between them.

This layer receives an already-decided ``RenderContext`` and returns text. It is
structurally incapable of changing state: it takes no engine objects, returns a
string, and never sees or produces a verdict of its own. Correctness is decided
before it runs; this only chooses how to say it.

Variation is SEEDED, not random: the same turn always renders the same words, so
tests are deterministic and a retry cannot change what a child was told.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Sequence


def _pick(pool: Sequence[str], seed: Any, salt: str = "") -> str:
    """Deterministic choice: stable for a turn, varied across turns."""
    if not pool:
        return ""
    key = f"{salt}|{seed}".encode("utf-8", "replace")
    idx = int(hashlib.sha256(key).hexdigest(), 16) % len(pool)
    return pool[idx]


# --------------------------------------------------------------------------- #
# Phrase pools — warm, concise, never exaggerated, never shaming.              #
# --------------------------------------------------------------------------- #
_CORRECT = (
    "Tačno.",
    "Tako je.",
    "Tačno, dobro si to uradio.",
    "To je tačno.",
)
_CORRECT_THEN_NEXT = (
    "Tačno — prelazimo na sljedeći zadatak.",
    "Tako je. Idemo dalje.",
    "Tačno. Sljedeći zadatak:",
    "Dobro riješeno — nastavljamo.",
)
_INCORRECT_THEN_NEXT = (
    "Ovaj odgovor nije tačan. Idemo na sljedeći zadatak:",
    "Nije još dobro, ali nastavljamo. Sada riješi:",
    "Ovaj put nije tačno. Prelazimo na sljedeći:",
    "Nije tačno — idemo dalje.",
)
_PARTIAL = (
    "Dio je dobar, ali nije sve.",
    "Na dobrom si putu, još nije potpuno.",
    "Blizu si — nedostaje još jedan dio.",
)
_SKIPPED_THEN_NEXT = (
    "U redu, preskačemo ovaj. Sljedeći zadatak:",
    "Dobro, ostavljamo ga. Idemo na sljedeći:",
    "Nema problema, preskačemo. Sada riješi:",
)
_EXAM_DONE = (
    "Kontrolni je završen.",
    "To je kraj kontrolnog.",
)
_CONTINUE_HINT = (
    "Možeš odgovoriti, preskočiti zadatak ili predati kontrolni.",
    "Ako zapneš, možeš preskočiti ili predati.",
)
#: Progressive support: rule → where to look → narrow it down. Never the answer.
_HELP_TIERS = (
    "Sjeti se pravila koje ovdje vrijedi.",
    "Pogledaj samo prvi korak — šta ti je poznato, a šta tražiš?",
    "Suzi na jedan detalj i provjeri njega. Možeš i preskočiti ovaj zadatak.",
)
_EFFORT = (
    "Vidim da si probao.",
    "Dobro je što si pokušao.",
)


@dataclass(frozen=True)
class RenderContext:
    """Everything the renderer may know. All of it is already decided."""
    mode: str = "practice"              # practice | exam | explain
    verdict: str | None = None          # correct | incorrect | partial | skipped
    intent: str = ""                    # turn_intent.Intent value
    grade: Any = 6
    seed: Any = ""                      # stable per turn (session+index)
    next_question: str = ""             # "" when there is no next item
    next_index: int | None = None       # 1-based, for "2. zadatak"
    help_level: int = 0                 # 0 = none, 1..3 progressive
    help_text: str = ""                 # engine-supplied, topic-specific tier
    missing: str = ""                   # what still remains, student-facing
    may_reveal: bool = False            # exams never reveal while active
    exam_finished: bool = False
    summary: str = ""                   # engine-rendered score block


def verdict_phrase(ctx: RenderContext) -> str:
    """The verdict sentence alone, without any transition."""
    if ctx.verdict == "correct":
        return _pick(_CORRECT, ctx.seed, "correct")
    if ctx.verdict == "partial":
        return _pick(_PARTIAL, ctx.seed, "partial")
    if ctx.verdict == "incorrect":
        # Standalone incorrect keeps a forward-looking half so it never lands as
        # a bare judgement on the child.
        return _pick(("Nije još tačno.", "Ovaj odgovor nije tačan.",
                      "Nije tačno, probaj još jednom."), ctx.seed, "incorrect")
    return ""


def help_phrase(ctx: RenderContext) -> str:
    """Progressive, non-revealing support.

    ``help_text`` is the engine's topic-specific tier (e.g. the divisibility
    rule). The generic tier is only the fallback, so a child never receives
    "sjeti se pravila" when the engine knows which rule.
    """
    tier = max(1, min(int(ctx.help_level or 1), len(_HELP_TIERS)))
    body = (ctx.help_text or "").strip() or _HELP_TIERS[tier - 1]
    if tier >= 3 and "preskoč" not in body.lower():
        body = f"{body} Možeš i preskočiti ovaj zadatak."
    if tier == 1:
        return body
    return f"{_pick(_EFFORT, ctx.seed, f'effort{tier}')} {body}"


def exam_transition(ctx: RenderContext) -> str:
    """Verdict + the next exam item, as one natural sentence.

    Never reveals an expected answer: the caller decides ``may_reveal`` and this
    function has no access to expected values at all.
    """
    parts: list[str] = []
    if ctx.exam_finished:
        if ctx.verdict:
            parts.append(verdict_phrase(ctx))
        summary = (ctx.summary or "").strip()
        # The engine's summary already announces the end; adding the phrase too
        # printed "Kontrolni je završen." twice in a row.
        if "zavr" not in summary.lower():
            parts.append(_pick(_EXAM_DONE, ctx.seed, "done"))
        if summary:
            parts.append("\n" + summary)
        return " ".join(p for p in parts if p).strip()

    if ctx.verdict == "skipped":
        head = _pick(_SKIPPED_THEN_NEXT, ctx.seed, "skip")
    elif ctx.verdict == "correct":
        head = _pick(_CORRECT_THEN_NEXT, ctx.seed, "corrnext")
    elif ctx.verdict == "incorrect":
        head = _pick(_INCORRECT_THEN_NEXT, ctx.seed, "incnext")
    elif ctx.verdict == "partial":
        head = _pick(_PARTIAL, ctx.seed, "partial") + " Idemo dalje."
    else:
        head = ""

    if not ctx.next_question:
        return head.rstrip(" :")

    label = f"{ctx.next_index}. zadatak" if ctx.next_index else "sljedeći zadatak"
    if head.rstrip().endswith(":"):
        body = f"{head} {ctx.next_question}"
    else:
        body = f"{head.rstrip('.')}. {label.capitalize()}: {ctx.next_question}"
    return body.strip()


def practice_feedback(ctx: RenderContext) -> str:
    """Verdict plus what remains — never the whole solution when not revealable."""
    parts = [verdict_phrase(ctx)]
    if ctx.missing and not ctx.may_reveal:
        parts.append(ctx.missing.strip())
    return " ".join(p for p in parts if p).strip()
