# -*- coding: utf-8 -*-
"""A bounded, deterministic anti-echo + pedagogical-value gate for ANSWER
FEEDBACK specifically (verdict-bearing narration: correct/incorrect/partial) —
never hints, concept explanations, or off-topic replies, which have different
pragmatics (a hint MAY legitimately reference the task again).

Detects the "feedback echo" problem confirmed in this audit: the model nearly
repeats the student's own sentence back to them instead of confirming,
correcting, or adding something new. Every signal here is GENERIC — Unicode/
diacritic normalization, token overlap, longest shared run, and a small
CLOSED lexicon of verdict/next-step/praise MARKER WORDS (not a phrase-
replacement blacklist for one screenshot).

Mirrors ``quality_gate.py``'s shape deliberately: a pure ``check_...``
function, a typed result, and a caller (``dispatcher``) that owns the ONE
bounded repair call + generic verdict-specific fallback.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

#: At most ONE model repair attempt per rejected feedback text.
MAX_REPAIR_ATTEMPTS = 1

_DIACRITIC_FOLD = str.maketrans({
    "č": "c", "ć": "c", "đ": "d", "š": "s", "ž": "z",
    "Č": "c", "Ć": "c", "Đ": "d", "Š": "s", "Ž": "z",
})
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_for_comparison(text: str) -> str:
    """Lowercase, diacritic-fold, strip punctuation, collapse whitespace —
    deterministic and reversible-enough for token comparison, never used to
    change what is actually shown to the student."""
    folded = str(text or "").translate(_DIACRITIC_FOLD).lower()
    stripped = _PUNCT_RE.sub(" ", folded)
    return _WS_RE.sub(" ", stripped).strip()


def _tokens(text: str) -> list:
    normalized = normalize_for_comparison(text)
    return normalized.split(" ") if normalized else []


def jaccard_token_overlap(a: str, b: str) -> float:
    ta, tb = set(_tokens(a)), set(_tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def longest_common_token_run(a: str, b: str) -> int:
    """Longest CONTIGUOUS shared token sequence — catches "feedback is
    student's sentence plus one word" even when Jaccard (a set measure) is
    diluted by that one extra token."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0
    best = 0
    for i in range(len(ta)):
        for j in range(len(tb)):
            k = 0
            while (i + k < len(ta) and j + k < len(tb)
                   and ta[i + k] == tb[j + k]):
                k += 1
            best = max(best, k)
    return best


#: Small, CLOSED lexicons — generic Bosnian markers, never a lesson-specific
#: phrase list. A verdict/next-step/added-content signal must be found via
#: one of these categories, or actual NEW tokens absent from the student's
#: message (numbers/symbols/terms), for feedback to count as adding value.
_VERDICT_MARKERS = (
    "tacno", "netacno", "djelimicno", "ispravno", "pogresno", "greska",
    "problem", "nazalost", "bravo", "odlicno",
)
_NEXT_STEP_MARKERS = ("probaj", "sljedeci", "korak", "pokusaj", "nastavi")
_GENERIC_PRAISE_ONLY = (
    "bravo", "odlicno", "super", "sjajno", "izvrsno", "tako je",
)

#: Words that plainly assert a POSITIVE outcome vs. a NEGATIVE one — used only
#: to catch a feedback text whose stated valence contradicts the authoritative
#: verdict it is meant to narrate (category: verdict_inconsistent_feedback).
_POSITIVE_VALENCE = ("tacno", "ispravno", "bravo", "odlicno", "super", "tako je")
_NEGATIVE_VALENCE = ("netacno", "pogresno", "greska", "nazalost", "nije tacno")

_EXACT_ECHO_THRESHOLD = 0.92
_NEAR_ECHO_JACCARD = 0.75
_NEAR_ECHO_RUN_FRACTION = 0.6


def _contains_marker(normalized_text: str, markers: tuple) -> bool:
    """Word-boundary containment — a substring check alone would wrongly
    match "tacno" inside "netacno" (its own negation)."""
    tokens = set(normalized_text.split(" "))
    for marker in markers:
        if " " in marker:
            if marker in normalized_text:
                return True
        elif marker in tokens:
            return True
    return False


@dataclass
class FeedbackValueResult:
    passed: bool
    categories: list = field(default_factory=list)
    similarity_metrics: dict = field(default_factory=dict)
    repair_seed: Optional[str] = None


def _has_added_numeric_or_symbolic_content(feedback: str, student_message: str) -> bool:
    """True iff the feedback contains a number/operator token the student's
    own message did not — a cheap, generic signal of genuinely new content."""
    feedback_tokens = set(re.findall(r"\d+(?:[.,]\d+)?|[+\-*/=]", feedback))
    student_tokens = set(re.findall(r"\d+(?:[.,]\d+)?|[+\-*/=]", student_message))
    return bool(feedback_tokens - student_tokens)


def check_feedback_value(
    feedback_text: str, *, student_message: str, verdict: str,
) -> FeedbackValueResult:
    """Evaluate ONE piece of answer-feedback text for anti-echo + pedagogical
    value. ``verdict`` is the reducer's AUTHORITATIVE verdict for this turn —
    never re-decided here, only used to check the text is CONSISTENT with it.
    """
    categories: list = []
    metrics: dict = {}
    feedback = str(feedback_text or "").strip()
    student = str(student_message or "").strip()

    if not feedback:
        return FeedbackValueResult(False, ["empty_feedback"], {}, "empty_feedback")

    normalized_feedback = normalize_for_comparison(feedback)
    normalized_student = normalize_for_comparison(student)

    jaccard = jaccard_token_overlap(feedback, student) if student else 0.0
    run = longest_common_token_run(feedback, student) if student else 0
    feedback_len = len(_tokens(feedback)) or 1
    run_fraction = run / feedback_len
    metrics["jaccard"] = round(jaccard, 3)
    metrics["longest_common_run"] = run
    metrics["run_fraction"] = round(run_fraction, 3)

    is_exact_echo = bool(normalized_student) and (
        normalized_feedback == normalized_student or jaccard >= _EXACT_ECHO_THRESHOLD)
    if is_exact_echo:
        categories.append("exact_echo")
    elif student and jaccard >= _NEAR_ECHO_JACCARD and run_fraction >= _NEAR_ECHO_RUN_FRACTION:
        categories.append("near_echo")

    lowered = normalized_feedback
    has_verdict_marker = _contains_marker(lowered, _VERDICT_MARKERS)
    has_next_step_marker = _contains_marker(lowered, _NEXT_STEP_MARKERS) or "?" in feedback
    has_new_content = _has_added_numeric_or_symbolic_content(feedback, student)
    metrics["has_verdict_marker"] = has_verdict_marker
    metrics["has_next_step_marker"] = has_next_step_marker
    metrics["has_new_content"] = has_new_content

    # A CORRECT verdict's own policy (see orchestrator._FEEDBACK_STANDARD_
    # BY_VERDICT) explicitly wants a short, concise confirmation — a bare
    # verdict marker ("Tačno!") IS the expected value there, not a symptom of
    # emptiness. incorrect/partial feedback is policy-required to explain the
    # problem and give a next step, so a bare verdict word alone is NOT
    # enough for those two.
    if verdict == "correct":
        has_added_value = has_verdict_marker or has_next_step_marker or has_new_content
    else:
        has_added_value = has_next_step_marker or has_new_content
    if not has_added_value:
        categories.append("no_added_value")

    is_generic_praise_only = (
        verdict != "correct"
        and _contains_marker(lowered, _GENERIC_PRAISE_ONLY)
        and not has_next_step_marker and not has_new_content
        and len(_tokens(feedback)) <= 4)
    if is_generic_praise_only:
        categories.append("unsupported_generic_praise")

    has_positive = _contains_marker(lowered, _POSITIVE_VALENCE)
    has_negative = _contains_marker(lowered, _NEGATIVE_VALENCE)
    inconsistent = (
        (verdict == "incorrect" and has_positive and not has_negative)
        or (verdict == "correct" and has_negative and not has_positive))
    if inconsistent:
        categories.append("verdict_inconsistent_feedback")

    repair_seed = ",".join(categories) if categories else None
    return FeedbackValueResult(
        passed=not categories, categories=categories,
        similarity_metrics=metrics, repair_seed=repair_seed)


#: Verdict-specific, generic, non-lesson-specific deterministic fallback —
#: used ONLY if the one bounded repair call ALSO fails this gate (or the
#: quality gate). Mirrors ``quality_gate.SAFE_FALLBACK_TEXT``'s role but is
#: verdict-aware since a truly generic single fallback cannot be right for
#: both a "correct" and an "incorrect" turn.
FEEDBACK_VALUE_SAFE_FALLBACK_BY_VERDICT = {
    "correct": "Tačno.",
    "incorrect": "To nije tačno. Pokušaj ponovo, korak po korak.",
    "partial": "Djelimično tačno — provjeri ponovo dio koji nedostaje.",
}
FEEDBACK_VALUE_SAFE_FALLBACK_DEFAULT = "Nastavimo s tvojim odgovorom."


def safe_fallback_text(verdict: str) -> str:
    return FEEDBACK_VALUE_SAFE_FALLBACK_BY_VERDICT.get(
        verdict, FEEDBACK_VALUE_SAFE_FALLBACK_DEFAULT)
