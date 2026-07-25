# -*- coding: utf-8 -*-
"""The frontend wire-format boundary — the ONLY V3 module that knows it.

The V3 core (schemas/reducer/orchestrator/state) has no compatibility fields.
Everything the existing frontend reads — ``answer``, ``last_tutor_task``,
``next_state``, ``correct_streak``, ``session_mode``, ``answer_verdict``,
``task_id`` — is produced here by translating a committed turn into the legacy
shape. When the frontend eventually changes, this file is what gets deleted.
"""
from __future__ import annotations

from typing import Optional

from matbot.ai_tutor_v3.rendering import normalize_math_for_display
from matbot.ai_tutor_v3.schemas import AuthoritativeOutcome, TutorSessionState

# Maps an authoritative verdict to the legacy ``answer_verdict`` vocabulary the
# frontend/statistics already understand.
_VERDICT_WIRE = {
    "correct": "correct",
    "incorrect": "incorrect",
    "partial": "partial",
    "needs_clarification": "clarification",
    "no_attempt": "no_attempt",
    "not_checkable": "not_checkable",
}


def build_response(
    *, answer: str, state: TutorSessionState, outcome: Optional[AuthoritativeOutcome],
    effective_topic: str, verification_status: str,
    state_version: int, response_category: str,
) -> dict:
    """Translate a committed V3 turn into the frontend response dict."""
    task = state.active_task
    verdict = outcome.verdict if outcome else None
    answer = normalize_math_for_display(answer)
    task_question = normalize_math_for_display(task.question) if task else ""
    clarification_seed = (
        normalize_math_for_display(state.pending_clarification.prompt_seed)
        if state.pending_clarification else None)
    next_state = {
        # The V3 core's own durable state travels in ONE namespaced key.
        "v3_state": state.model_dump(mode="json"),
        "engine": "v3_practice",
        "state_version": state_version,
        "task_id": task.task_id if task else None,
        "task_status": "active" if task else "none",
        "active_task_kind": "practice" if task else None,
        "correct_streak": state.counters.correct_streak,
        "difficulty_level": state.difficulty.level,
        "attempt_count": task.attempts if task else 0,
        "wrong_attempt_count": task.wrong_attempts if task else 0,
        "hint_count": task.hints_given if task else 0,
        "expected_user_action": "answer_task" if task else "none",
        "task": {
            "task_id": task.task_id, "question": task_question,
            "concept_id": task.concept_id, "target_id": task.target_id,
            "answer_kind": task.answer_kind, "source": "v3_model",
            "validation_status": "validated",
        } if task else None,
        "pending_clarification": clarification_seed,
        "verification_status": verification_status,
    }
    return {
        "status": "ready",
        "answer": answer,
        "mode": "practice",
        "session_mode": "practice",
        "last_tutor_task": task_question if task else "",
        "answer_verdict": _VERDICT_WIRE.get(verdict) if verdict else None,
        "answer_verdict_detail": verdict,
        "task_id": task.task_id if task else None,
        "task_status": "active" if task else "none",
        "final_topic": state.lesson_id or "unknown",
        "effective_topic": effective_topic or state.lesson_id or "",
        "engine": "v3_practice",
        "response_category": response_category,
        "verification_status": verification_status,
        # V3 never asks the model for a verdict; grading is server-side.
        "gpt_check_used": False,
        "next_state": next_state,
    }


def state_from_payload(payload: dict) -> Optional[dict]:
    """Extract the round-tripped V3 state dict from ``previous_next_state``, or
    None for a first turn. The dispatcher rehydrates the actual model from the
    durable store; this is only the transport echo."""
    prev = payload.get("previous_next_state")
    if isinstance(prev, dict):
        v3 = prev.get("v3_state")
        if isinstance(v3, dict):
            return v3
    return None
