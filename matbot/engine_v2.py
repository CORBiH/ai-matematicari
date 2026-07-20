"""Engine V2 — Phase 0 read-only *shadow* grading reducer.

This module runs BESIDE the legacy grading flow. It consumes structured evidence
already produced upstream (the deterministic ``answer_checker`` result and the
structured GPT grader JSON), reduces it to ONE normalized grading decision, and
exposes it for comparison logging against the legacy outcome.

Hard guarantees for this phase:
  * NEVER parses the tutor's final prose to decide correctness.
  * NEVER mutates the active task, counters, or the student-facing response.
  * NEVER emits hidden reasoning, prompts, credentials, or conversation history.

Feature flag ``MATBOT_ENGINE_V2`` = ``off`` | ``shadow`` (default ``off``).
Authoritative V2 mode is intentionally NOT implemented yet.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Any

ENGINE_VERSION = "v2-shadow"

# Normalized shadow verdicts (the only values the reducer may emit).
VERDICT_CORRECT = "correct"
VERDICT_PARTIAL = "partial"
VERDICT_INCORRECT = "incorrect"
VERDICT_AMBIGUOUS = "ambiguous"
VERDICT_NOT_CHECKABLE = "not_checkable"

_COARSE_VERDICTS = {
    VERDICT_CORRECT,
    VERDICT_PARTIAL,
    VERDICT_INCORRECT,
    VERDICT_AMBIGUOUS,
    VERDICT_NOT_CHECKABLE,
}

# ``authoritative_verdict`` (grading_guard) output -> normalized shadow verdict.
# "unknown" maps to None = the deterministic grader ABSTAINS (fall through to GPT).
_DETERMINISTIC_MAP = {
    "correct": VERDICT_CORRECT,
    "partial": VERDICT_PARTIAL,
    "step": VERDICT_PARTIAL,        # correct intermediate step -> not complete
    "incomplete": VERDICT_PARTIAL,
    "incorrect": VERDICT_INCORRECT,
    "mixed": VERDICT_PARTIAL,
    "unknown": None,
}

# Structured GPT verdict -> normalized shadow verdict (1:1).
_STRUCTURED_MAP = {
    "correct": VERDICT_CORRECT,
    "partial": VERDICT_PARTIAL,
    "incorrect": VERDICT_INCORRECT,
    "ambiguous": VERDICT_AMBIGUOUS,
}


def engine_v2_mode() -> str:
    """Return the current Engine V2 mode: ``"off"`` (default) or ``"shadow"``.

    Any unrecognized value is treated as ``"off"`` (fail-safe). Authoritative
    mode is not supported in this phase and also resolves to ``"off"``.
    """
    raw = (os.getenv("MATBOT_ENGINE_V2") or "off").strip().lower()
    return "shadow" if raw == "shadow" else "off"


def shadow_enabled() -> bool:
    return engine_v2_mode() == "shadow"


def grading_mode() -> str:
    """Phase 2 sub-flag ``MATBOT_ENGINE_V2_GRADING``: ``"off"`` (default) or
    ``"on"``. When ``on``, the deterministic checker is authoritative over the
    structured GPT grader and tutor prose is never a grader. Independent of the
    shadow flag; unrecognized values resolve to ``"off"`` (fail-safe)."""
    raw = (os.getenv("MATBOT_ENGINE_V2_GRADING") or "off").strip().lower()
    return "on" if raw == "on" else "off"


def grading_authoritative() -> bool:
    return grading_mode() == "on"


def practice_mode() -> str:
    """Phase 3 sub-flag ``MATBOT_ENGINE_V2_PRACTICE``: ``"off"`` (default) or
    ``"on"``. When ``on``, the deterministic Practice Step Engine drives guided
    multi-step tasks that have a SolutionPlan (currently divisibility_by_6).
    Rollback = ``off`` → legacy prose-timed hints. Unknown values → ``"off"``."""
    raw = (os.getenv("MATBOT_ENGINE_V2_PRACTICE") or "off").strip().lower()
    return "on" if raw == "on" else "off"


def practice_engine_enabled() -> bool:
    return practice_mode() == "on"


def canary_enabled() -> bool:
    """``ENGINE_CANARY=1`` marks this process as the canary cohort.

    PURELY a telemetry label: it never affects grading, task generation, state,
    counters, or anything the student sees — it only makes canary rows and
    diagnostic aggregates queryable."""
    raw = (os.getenv("ENGINE_CANARY") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def canary_marker() -> str:
    """Sanitized cohort marker for telemetry: ``"1"`` or ``"0"`` (never PII)."""
    return "1" if canary_enabled() else "0"


@dataclass
class GradingEvidence:
    """Structured evidence collected upstream. No prose, ever."""

    deterministic_verdict: str | None = None   # authoritative_verdict() output
    deterministic_checkable: bool = False      # checker produced real verdicts
    deterministic_step: bool = False           # any item was a correct_step
    structured_gpt_verdict: str | None = None  # ONLY from parsed GPT JSON
    structured_gpt_confidence: float | None = None
    structured_attempted: bool = False         # the turn ROUTED to structured grading
    task_status: str | None = None             # legacy next_state (context only)
    answer_type: str | None = None


@dataclass
class ShadowGradingResult:
    engine_version: str = ENGINE_VERSION
    verdict: str = VERDICT_NOT_CHECKABLE
    detail: str = ""
    grader_source: str = "none"                # deterministic | structured_gpt | none
    confidence: float | None = None
    step_completed: bool | None = None
    task_completed: bool | None = None
    attempt_delta: int = 0
    wrong_attempt_delta: int = 0
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "engine_version": self.engine_version,
            "verdict": self.verdict,
            "detail": self.detail,
            "grader_source": self.grader_source,
            "confidence": self.confidence,
            "step_completed": self.step_completed,
            "task_completed": self.task_completed,
            "attempt_delta": self.attempt_delta,
            "wrong_attempt_delta": self.wrong_attempt_delta,
            "evidence": dict(self.evidence),
        }


def _map_deterministic(verdict: str | None) -> str | None:
    if not verdict:
        return None
    return _DETERMINISTIC_MAP.get(str(verdict).strip().lower())


def _map_structured(verdict: str | None) -> str | None:
    if not verdict:
        return None
    return _STRUCTURED_MAP.get(str(verdict).strip().lower())


def _derive_completion(verdict: str, step: bool) -> tuple[bool | None, bool | None]:
    """Return (step_completed, task_completed) inferred conservatively.

    We do NOT have a solution-plan/step engine yet (Phase 3), so completion is
    a best-effort inference used only for shadow comparison, never for behavior.
    """
    if verdict == VERDICT_CORRECT and not step:
        return (None, True)
    if step:
        return (True, False)
    if verdict in (VERDICT_PARTIAL, VERDICT_INCORRECT):
        return (False, False)
    return (None, None)


def route_grader(evidence: GradingEvidence) -> str:
    """Explicit grader-routing policy — the authority for THIS turn's verdict.

    Mirrors the refined Phase 2 grading precedence (and the routing already done
    upstream by ``_should_run_contextual_gpt_grade``):

      * ``"structured_gpt"`` — a structured GPT verdict exists. The grader only
        runs for procedural / intermediate / conceptual answers, where it is the
        right authority (the deterministic checker can misread an intermediate
        number as a wrong final answer), so a present structured verdict wins.
      * ``"deterministic"`` — no structured verdict, but the checker decided.
        This is the clean-final-answer path.
      * ``"none"`` — neither grader produced a verdict (malformed / unavailable
        structured result, checker abstained) → ungraded.

    Tutor prose is never an input here, so it can never be grading evidence.
    """
    if _map_structured(evidence.structured_gpt_verdict) is not None:
        return "structured_gpt"
    if evidence.deterministic_checkable and _map_deterministic(evidence.deterministic_verdict) is not None:
        return "deterministic"
    return "none"


def reduce_shadow(evidence: GradingEvidence) -> ShadowGradingResult:
    """Reduce structured evidence to ONE normalized decision, matching the
    authoritative Phase 2 grading precedence so shadow telemetry predicts it.

    Precedence (see ``route_grader``):
      1. A structured GPT verdict (present ⇒ the turn routed to it) is authoritative.
      2. Else a decisive deterministic checker result is authoritative.
      3. Tutor prose is never evidence (not accepted by this function at all).
      4. Neither decides → ``ambiguous`` if structured grading was attempted but
         yielded nothing (malformed/unavailable), else ``not_checkable``.
    """
    det = (
        _map_deterministic(evidence.deterministic_verdict)
        if evidence.deterministic_checkable
        else None
    )
    gpt = _map_structured(evidence.structured_gpt_verdict)
    conflict = bool(det is not None and gpt is not None and det != gpt)

    source = route_grader(evidence)
    if source == "structured_gpt":
        verdict = gpt
        confidence = evidence.structured_gpt_confidence
    elif source == "deterministic":
        verdict = det
        confidence = None
    else:
        # No grader decided. Distinguish "routed to structured grading but it
        # failed" (ambiguous) from "nothing to grade" (not_checkable).
        verdict = VERDICT_AMBIGUOUS if evidence.structured_attempted else VERDICT_NOT_CHECKABLE
        confidence = None

    step_completed, task_completed = _derive_completion(
        verdict, evidence.deterministic_step and source == "deterministic"
    )

    result = ShadowGradingResult(
        verdict=verdict,
        detail=f"{source}:{verdict}" if source != "none" else (
            "structured_unavailable" if evidence.structured_attempted else "no_grader"
        ),
        grader_source=source,
        confidence=confidence,
        step_completed=step_completed,
        task_completed=task_completed,
        attempt_delta=1 if verdict in (VERDICT_CORRECT, VERDICT_PARTIAL, VERDICT_INCORRECT) else 0,
        wrong_attempt_delta=1 if verdict == VERDICT_INCORRECT else 0,
    )
    result.evidence = {
        "deterministic_verdict": evidence.deterministic_verdict if evidence.deterministic_checkable else None,
        "gpt_structured_verdict": evidence.structured_gpt_verdict,
        "structured_attempted": bool(evidence.structured_attempted),
        "task_status": evidence.task_status,
        "answer_type": evidence.answer_type,
        "deterministic_gpt_conflict": conflict,
    }
    return result


def _coarse(value: Any) -> str:
    """Normalize a legacy coarse verdict (correct|incorrect|partial|None) for
    comparison. None / unrecognized -> not_checkable."""
    text = (str(value).strip().lower() if value is not None else "")
    return text if text in _COARSE_VERDICTS else VERDICT_NOT_CHECKABLE


def compare_with_legacy(
    shadow: ShadowGradingResult,
    *,
    legacy_verdict: Any,
    legacy_verdict_detail: Any,
    legacy_task_completed: bool | None,
    legacy_correct_streak: int | None,
    prose_derived_legacy: bool,
) -> dict:
    """Compare the shadow (authoritative-predicted) decision with the final
    legacy outcome. Returns a flat, sanitized dict suitable for telemetry.

    Conflict taxonomy is aligned with the refined Phase 2 precedence, so a
    disagreement predicts what flipping ``MATBOT_ENGINE_V2_GRADING=on`` would do:

      * ``legacy_prose_verdict`` — legacy derived the verdict from tutor prose;
        the authoritative reducer does not (prose is never a grader). This is the
        PRIMARY Phase 2 behavior change.
      * ``uncheckable`` — the reducer could not decide (ungraded/ambiguous).
      * ``legacy_correct_shadow_incorrect`` / ``legacy_incorrect_shadow_correct``.
      * ``legacy_completed_shadow_not_completed``.
      * ``verdict_mismatch`` — any other coarse disagreement.
    """
    legacy_coarse = _coarse(legacy_verdict)
    agree = shadow.verdict == legacy_coarse

    if agree:
        if legacy_task_completed and shadow.task_completed is False:
            conflict_type = "legacy_completed_shadow_not_completed"
        else:
            conflict_type = "no_conflict"
    else:
        if prose_derived_legacy:
            conflict_type = "legacy_prose_verdict"
        elif shadow.verdict in (VERDICT_NOT_CHECKABLE, VERDICT_AMBIGUOUS):
            conflict_type = "uncheckable"
        elif legacy_coarse == VERDICT_CORRECT and shadow.verdict == VERDICT_INCORRECT:
            conflict_type = "legacy_correct_shadow_incorrect"
        elif legacy_coarse == VERDICT_INCORRECT and shadow.verdict == VERDICT_CORRECT:
            conflict_type = "legacy_incorrect_shadow_correct"
        elif legacy_task_completed and shadow.task_completed is False:
            conflict_type = "legacy_completed_shadow_not_completed"
        else:
            conflict_type = "verdict_mismatch"

    return {
        "legacy_verdict": legacy_coarse,
        "legacy_verdict_detail": (str(legacy_verdict_detail) if legacy_verdict_detail is not None else None),
        "shadow_verdict": shadow.verdict,
        "shadow_verdict_detail": shadow.detail,
        "agreement": bool(agree and conflict_type == "no_conflict"),
        "conflict_type": conflict_type,
        "legacy_task_completed": legacy_task_completed,
        "shadow_task_completed": shadow.task_completed,
        "legacy_correct_streak": legacy_correct_streak,
        "shadow_grader_source": shadow.grader_source,
    }


# ---------------------------------------------------------------------------
# In-process aggregate metrics (diagnostics only; guarded by caller auth).
# ---------------------------------------------------------------------------
_METRICS_LOCK = threading.Lock()
_METRICS: dict[str, Any] = {
    "total": 0,
    "agreements": 0,
    "disagreements": 0,
    "deterministic": 0,
    "structured_gpt": 0,
    "uncheckable": 0,
    "conflicts_by_type": {},
}


def record_metrics(shadow: ShadowGradingResult, comparison: dict) -> None:
    with _METRICS_LOCK:
        _METRICS["total"] += 1
        if comparison.get("agreement"):
            _METRICS["agreements"] += 1
        else:
            _METRICS["disagreements"] += 1
        if shadow.grader_source == "deterministic":
            _METRICS["deterministic"] += 1
        elif shadow.grader_source == "structured_gpt":
            _METRICS["structured_gpt"] += 1
        if shadow.verdict == VERDICT_NOT_CHECKABLE:
            _METRICS["uncheckable"] += 1
        ctype = comparison.get("conflict_type") or "no_conflict"
        by_type = _METRICS["conflicts_by_type"]
        by_type[ctype] = by_type.get(ctype, 0) + 1


def get_metrics() -> dict:
    with _METRICS_LOCK:
        snapshot = dict(_METRICS)
        snapshot["conflicts_by_type"] = dict(_METRICS["conflicts_by_type"])
        snapshot["mode"] = engine_v2_mode()
        # Cohort + flag posture, so a canary process is identifiable in diagnostics.
        snapshot["canary"] = canary_marker()
        snapshot["flags"] = {
            "MATBOT_ENGINE_V2": engine_v2_mode(),
            "MATBOT_ENGINE_V2_GRADING": grading_mode(),
            "MATBOT_ENGINE_V2_PRACTICE": practice_mode(),
        }
        return snapshot


def reset_metrics() -> None:
    with _METRICS_LOCK:
        _METRICS["total"] = 0
        _METRICS["agreements"] = 0
        _METRICS["disagreements"] = 0
        _METRICS["deterministic"] = 0
        _METRICS["structured_gpt"] = 0
        _METRICS["uncheckable"] = 0
        _METRICS["conflicts_by_type"] = {}
