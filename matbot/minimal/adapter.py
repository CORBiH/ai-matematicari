# -*- coding: utf-8 -*-
"""The boundary. The ONLY module that knows the legacy wire format.

The core (intent/state/grading/renderer/engine) has no compatibility fields in
it. Everything the existing frontend and Sheets logger expect —
``last_tutor_task``, ``next_state``, ``session_mode``, ``answer_verdict`` — is
produced here by translating a ``TurnResult``.

That is what keeps the reset honest: when the frontend eventually changes, this
file is what gets deleted, and the core does not move.
"""
from __future__ import annotations

import os
from typing import Any, Callable

from matbot.minimal.engine import TurnResult, handle_turn
from matbot.minimal.skills import SKILLS
from matbot.minimal.state import SessionState

FLAG = "MATBOT_MINIMAL_ENGINE"


def minimal_engine_enabled() -> bool:
    """``off`` (default) | ``on``. Off → the legacy path runs, untouched."""
    return (os.getenv(FLAG) or "off").strip().lower() == "on"


def _int_grade(value: Any, default: int = 6) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def state_from_payload(payload: dict) -> SessionState:
    """Rebuild the session from the round-tripped ``previous_next_state``."""
    prev = payload.get("previous_next_state")
    prev = prev if isinstance(prev, dict) else {}
    return SessionState.from_dict(
        prev.get("minimal_state"),
        session_id=str(payload.get("session_id") or "")[:80],
        grade=_int_grade(payload.get("grade")),
    )


def response_from_result(result: TurnResult, payload: dict) -> dict:
    """Translate a TurnResult into the response shape the frontend consumes."""
    # The ACTIVE task drives forward-looking fields; ``audit`` is the task this
    # turn actually concerned. On a correct completion the active task is
    # already null, so reading counters from it produced task_id="",
    # task_status="", attempts=0 on exactly the row that mattered most.
    task = result.state.active_task
    audit = task or result.task
    completed = task is None and result.task is not None and result.task.solved
    revealed = bool(result.task is not None and result.task.solution_revealed)
    state_dict = result.state.to_dict()
    next_state = {
        # The core's own state travels in ONE namespaced key; the fields beside
        # it exist only because the current frontend reads them.
        "minimal_state": state_dict,
        "engine": "minimal",
        # Audit fields describe the task THIS turn concerned, so a completion
        # row stays fully auditable after active_task becomes null.
        "task_id": audit.task_id if audit else None,
        "task_status": ("active" if task else "revealed" if revealed
                        else "completed" if completed else None),
        "completed_task_id": audit.task_id if completed else None,
        "solution_revealed": revealed,
        "active_task_kind": "practice" if task else None,
        "correct_streak": result.state.correct_streak,
        "difficulty_level": result.state.difficulty_level,
        "attempt_number": audit.attempts if audit else 0,
        "attempt_count": audit.attempts if audit else 0,
        "total_attempt_count": audit.attempts if audit else 0,
        "wrong_attempt_count": audit.wrong_attempts if audit else 0,
        "hint_count": audit.hints_given if audit else 0,
        "expected_user_action": "answer_task" if task else "none",
        "task": {
            "task_id": task.task_id, "question": task.question,
            "skill_id": task.skill_id, "tema_id": task.npp_id,
            "tema_title": task.tema_title, "source": "minimal_template",
            "validation_status": "validated",
        } if task else None,
        "pending_confirmation": result.state.pending_confirmation,
    }
    topic = result.state.topic
    # Sheets reads its audit columns from answer_check.items[0] (see
    # sheets_log._first_answer_check_item). Emitting it here is what fills
    # expected_answer / normalized_* / deterministic_check, which were blank
    # even though the evidence already existed inside the GradingResult.
    answer_check = None
    grading = result.grading
    if grading is not None:
        answer_check = {
            "checkable": grading.deterministic,
            "items": [{
                "n": 1,
                "verdict": grading.detail or grading.verdict,
                "answer_type": grading.answer_type,
                "expected_answer": grading.expected_display,
                "normalized_expected": grading.normalized_expected,
                "student_answer": grading.graded_answer,
                "normalized_student": grading.normalized_student,
                "deterministic_check": dict(grading.evidence),
            }],
        }
    return {
        "status": "ready" if result.topic_supported else "unsupported_topic",
        "answer": result.answer,
        "mode": "practice",
        "session_mode": "practice",
        # Only a structured ActiveTask can populate this; there is no prose path.
        "last_tutor_task": task.question if task else "",
        "answer_verdict": result.verdict,
        "answer_verdict_detail": (result.grading.detail if result.grading else None),
        "answer_check": answer_check,
        "task_id": audit.task_id if audit else None,
        "task_status": ("active" if task else "revealed" if revealed
                        else "completed" if completed else None),
        "solution_revealed": revealed,
        "attempt_number": audit.attempts if audit else None,
        "total_attempt_count": audit.attempts if audit else None,
        "wrong_attempt_count": audit.wrong_attempts if audit else None,
        "hint_count": audit.hints_given if audit else None,
        # The minimal engine never asks the model for a verdict.
        "gpt_check_used": False if result.grading else None,
        "final_topic": topic.npp_id or "unknown",
        "effective_topic": topic.npp_id or "",
        "selected_oblast": str(payload.get("selected_oblast") or ""),
        "engine": "minimal",
        # Decision trace, merged into minimal_routing by the service. Telemetry
        # only — no existing Sheets column moves.
        "minimal_telemetry": dict(result.telemetry or {}),
        "next_state": next_state,
    }


def supported_topics_message() -> str:
    names = ", ".join(f"„{s.title}”" for s in SKILLS)
    return f"Za sada podržavam ove teme: {names}."


def unresolved_response(payload: dict, topic: Any, reason: str) -> dict:
    """Honest answer when an explicitly selected topic cannot be served.

    Returned INSTEAD of falling through to free legacy generation: a task from
    another topic is worse than no task, because the student cannot tell the
    difference. Never invents a task and never names a substitute topic.
    """
    named = getattr(topic, "title", "") or ""
    if reason == "unresolved_runtime_topic":
        body = ("Ne mogu pouzdano prepoznati koja je tema izabrana, pa ti ne bih "
                "dao zadatak iz pogrešne teme. Izaberi temu iz liste pa nastavljamo.")
    elif named:
        body = (f"Za temu „{named}” još nemam zadatke koje mogu pouzdano "
                "provjeriti, pa ti ne bih dao zadatak iz druge teme.")
    else:
        body = ("Za tu temu još nemam zadatke koje mogu pouzdano provjeriti, "
                "pa ti ne bih dao zadatak iz druge teme.")
    return {
        "status": "unsupported_topic",
        "answer": f"{body}\n\n{supported_topics_message()}",
        "mode": "practice",
        "session_mode": "practice",
        "last_tutor_task": "",           # nothing activated — nothing to answer
        "answer_verdict": None,
        "answer_verdict_detail": None,
        "final_topic": getattr(topic, "npp_id", "") or "unknown",
        "effective_topic": getattr(topic, "npp_id", "") or "",
        "selected_oblast": str(payload.get("selected_oblast") or ""),
        "engine": "minimal",
        "next_state": {
            "minimal_state": None,
            "engine": "minimal",
            "task_id": None,
            "task_status": None,
            "active_task_kind": None,
            "correct_streak": 0,
            "expected_user_action": "select_topic",
            "task": None,
        },
    }


def handle_chat_minimal(
    payload: dict,
    openai_chat: Callable | None = None,
    *,
    model: str = "",
    timeout: float | None = None,
) -> dict:
    """Entry point used by ``ai_tutor_service.handle_chat`` when the flag is on.

    ``payload`` is READ-ONLY here: the raw student message is passed through
    verbatim and the caller's dict is never mutated.
    """
    raw_message = payload.get("student_message")
    if raw_message is None:
        raw_message = payload.get("message")

    result = handle_turn(
        raw_message=raw_message,
        state=state_from_payload(payload),
        selected_topic=payload.get("selected_topic"),
        selected_oblast=payload.get("selected_oblast"),
        openai_chat=openai_chat, model=model, timeout=timeout,
    )
    response = response_from_result(result, payload)
    if not result.topic_supported:
        response["answer"] = f"{response['answer']}\n\n{supported_topics_message()}"
    return response
