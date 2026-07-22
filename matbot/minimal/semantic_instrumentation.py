# -*- coding: utf-8 -*-
"""Measure, don't estimate.

A prior report of "15-25 calls per 100 turns" and "1-3s latency" was an
educated guess, not a measurement. This module turns the SAME structured
telemetry ``grading.grade`` already writes into ``GradingResult.evidence``
(and, downstream, ``minimal_routing``) into an aggregate report — eligible
turns, actual calls, calls avoided, latency percentiles, token counts, parse
failures, low-confidence results, and disagreement counts by type.

It reads ONLY the existing structured fields — the same ones already
forbidden from carrying chain-of-thought — never raw model output.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CallRecord:
    """One graded turn's semantic-grading footprint, extracted from a
    ``GradingResult.evidence`` dict. ``eligible`` is the caller's own
    judgment (e.g. "this message was prose-like and a supported family");
    everything else comes straight out of the telemetry."""
    eligible: bool
    called: bool
    latency_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    parse_failed: bool = False
    low_confidence: bool = False
    disagreement_type: str = ""


def record_from_evidence(evidence: dict, *, eligible: bool) -> CallRecord:
    """Build a ``CallRecord`` from the telemetry ``grading.grade`` already
    produces — no separate hidden channel, no chain-of-thought."""
    reason = str(evidence.get("semantic_fallback_reason") or "")
    shadow_audit = evidence.get("shadow_audit") or {}
    return CallRecord(
        eligible=eligible,
        called=bool(evidence.get("semantic_judge_used")),
        latency_ms=evidence.get("semantic_latency_ms"),
        prompt_tokens=evidence.get("semantic_prompt_tokens"),
        completion_tokens=evidence.get("semantic_completion_tokens"),
        parse_failed=(reason in ("invalid_json", "empty_response")),
        low_confidence=(reason == "low_confidence"),
        disagreement_type=str(shadow_audit.get("shadow_disagreement_type") or ""),
    )


def _percentile(sorted_values: list[float], p: float) -> float | None:
    if not sorted_values:
        return None
    idx = min(len(sorted_values) - 1, int(round(p * (len(sorted_values) - 1))))
    return sorted_values[idx]


@dataclass
class SemanticGradingRecorder:
    """Accumulates ``CallRecord``s across a batch of graded turns (a shadow
    evaluation run, or a fixed test corpus) and reports aggregate stats.

    Deliberately NOT a global singleton — a caller (a shadow-eval script, or
    a test) constructs one, feeds it every turn's evidence dict, and reads
    ``summarize()`` when done. Nothing here calls OpenAI or measures wall
    time itself; it only aggregates what ``grading.grade`` already measured.
    """
    records: list[CallRecord] = field(default_factory=list)

    def record(self, evidence: dict, *, eligible: bool) -> None:
        self.records.append(record_from_evidence(evidence, eligible=eligible))

    def record_direct(self, record: CallRecord) -> None:
        """For tests that construct a ``CallRecord`` directly rather than via
        a real ``GradingResult.evidence`` dict."""
        self.records.append(record)

    def summarize(self) -> dict:
        eligible = [r for r in self.records if r.eligible]
        called = [r for r in eligible if r.called]
        latencies = sorted(r.latency_ms for r in called if r.latency_ms is not None)
        prompt_tokens = [r.prompt_tokens for r in called if r.prompt_tokens is not None]
        completion_tokens = [r.completion_tokens for r in called
                            if r.completion_tokens is not None]
        disagreements = Counter(
            r.disagreement_type for r in called
            if r.disagreement_type and r.disagreement_type != "none")
        return {
            "eligible_prose_turns": len(eligible),
            "actual_semantic_calls": len(called),
            "calls_avoided_by_deterministic_grading": len(eligible) - len(called),
            "p50_latency_ms": _percentile(latencies, 0.50),
            "p95_latency_ms": _percentile(latencies, 0.95),
            "prompt_tokens_total": sum(prompt_tokens),
            "prompt_tokens_avg": (sum(prompt_tokens) / len(prompt_tokens)
                                 if prompt_tokens else None),
            "completion_tokens_total": sum(completion_tokens),
            "completion_tokens_avg": (sum(completion_tokens) / len(completion_tokens)
                                     if completion_tokens else None),
            "parse_failures": sum(1 for r in called if r.parse_failed),
            "low_confidence_results": sum(1 for r in called if r.low_confidence),
            "disagreement_counts": dict(disagreements),
        }
