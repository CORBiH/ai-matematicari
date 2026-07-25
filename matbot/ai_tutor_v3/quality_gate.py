# -*- coding: utf-8 -*-
"""A bounded, deterministic student-facing quality gate.

Runs on model-generated text right before it is shown to the student (and
before any of it is baked into durable state, e.g. an active task's
question). Every check here is GENERIC — grounded in things any lesson's
output can violate — never a lesson-specific or long phrase-replacement
blacklist.

The gate does not call a model itself. ``dispatcher`` uses ``check()`` to
decide whether a bounded model repair is needed, then re-checks the repaired
text with the SAME function — see ``matbot.ai_tutor_v3.orchestrator.
repair_student_text`` for the one allowed repair call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from matbot.ai_tutor_v3.rendering import normalize_math_for_display

#: At most ONE model repair attempt per rejected text — never a retry loop.
MAX_REPAIR_ATTEMPTS = 1

#: A short, safe, concrete Bosnian fallback — used only if a text fails the
#: gate AND the one allowed repair also fails (or errors).
SAFE_FALLBACK_TEXT = "Nastavimo — reci mi svoj odgovor kad budeš spreman/na."

#: Internal implementation vocabulary that must never reach the student. This
#: grounds the "no internal terminology" rule generically — it names V3's OWN
#: architecture words, never a lesson-specific term.
_INTERNAL_TERMS = (
    "blueprint", "reducer", "verifikator", "verifier", "json", "confidence",
    "pouzdanost:", "authoritativeoutcome", "narrationresult",
    "taskspecification", "pydantic", "endpoint", "payload", "token",
    "schema_version", "model_only", "verification_status",
)

#: A SMALL, non-exhaustive safety net against clearly shaming language —
#: deliberately short (not a phrase-replacement blacklist); genuine tone
#: quality still needs human/live review (see final report).
_SHAMING_TERMS = ("glup", "glupo", "budalo", "budala", "nesposoban", "nesposobna")

_HTML_TAG_RE = re.compile(r"<(?!br\s*/?>)[a-zA-Z][^>]*>", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

#: Grade-aware maximum length for a TASK instruction (characters) — grade 6
#: must stay short and direct; later grades allow a little more room for
#: multi-step reasoning, per the grade policy.
_TASK_MAX_CHARS_BY_GRADE = {6: 220, 7: 260, 8: 320, 9: 360}
_TASK_MAX_SENTENCES = 3


@dataclass
class QualityResult:
    ok: bool
    failure_categories: list = field(default_factory=list)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s.strip()]


def _brace_balance_ok(text: str) -> bool:
    return text.count("{") == text.count("}") and text.count("(") == text.count(")")


def check(
    text, *, response_type: str, grade: int,
    previous_text: Optional[str] = None,
    forbidden_reveal: Optional[str] = None,
) -> QualityResult:
    """Evaluate ONE piece of already math-normalized student-facing text.

    ``text`` is expected to already have gone through
    ``normalize_math_for_display`` — this function does not repeat that
    transform, but it DOES detect a text that still contains a raw,
    undelimited LaTeX command (i.e. normalization was not run, or something
    reintroduced one afterward) via the same normalizer, so calling ``check``
    directly on un-normalized text is still safe.
    """
    failures: list[str] = []
    raw = str(text or "").strip()
    if not raw:
        return QualityResult(False, ["empty_text"])

    normalized = normalize_math_for_display(raw)
    if normalized != raw:
        failures.append("undelimited_latex")

    if not _brace_balance_ok(raw):
        failures.append("malformed_fraction_notation")

    lowered = raw.lower()
    if any(term in lowered for term in _INTERNAL_TERMS):
        failures.append("internal_terminology")

    if any(term in lowered for term in _SHAMING_TERMS):
        failures.append("inappropriate_language")

    if _HTML_TAG_RE.search(raw):
        failures.append("unsupported_html")

    sentences = _sentences(raw)
    if len(sentences) > 1 and len(sentences) != len({s.lower() for s in sentences}):
        failures.append("duplicated_sentence")

    if response_type == "task":
        if len(sentences) > _TASK_MAX_SENTENCES:
            failures.append("task_too_long")
        max_chars = _TASK_MAX_CHARS_BY_GRADE.get(int(grade), _TASK_MAX_CHARS_BY_GRADE[6])
        if len(raw) > max_chars:
            failures.append("task_too_long_for_grade")
        if previous_text and raw.strip() == previous_text.strip():
            failures.append("repeated_task_prefix")
        if forbidden_reveal:
            needle = str(forbidden_reveal).strip().lower()
            # A short token ("da", "ne", "6", "1/2", ...) is exactly the kind
            # of word/number a NORMAL task sentence legitimately contains on
            # its own (e.g. "Da li je 252 djeljiv sa 6?" is a normal boolean
            # question, not a leak) — checking those would false-positive on
            # ordinary phrasing far more often than it would catch a real
            # leak, so this generic safety net only fires for a longer,
            # genuinely distinctive answer string.
            if len(needle) >= 4 and re.search(
                    rf"(?<!\w){re.escape(needle)}(?!\w)", lowered):
                failures.append("answer_revealed_in_task")

    return QualityResult(ok=not failures, failure_categories=failures)
