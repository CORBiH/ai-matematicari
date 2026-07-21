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
    task = result.state.active_task
    state_dict = result.state.to_dict()
    next_state = {
        # The core's own state travels in ONE namespaced key; the fields beside
        # it exist only because the current frontend reads them.
        "minimal_state": state_dict,
        "engine": "minimal",
        "task_id": task.task_id if task else None,
        "task_status": "active" if task else None,
        "active_task_kind": "practice" if task else None,
        "correct_streak": result.state.correct_streak,
        "attempt_count": task.attempts if task else 0,
        "wrong_attempt_count": task.wrong_attempts if task else 0,
        "hint_count": task.hints_given if task else 0,
        "expected_user_action": "answer_task" if task else "none",
        "task": {
            "task_id": task.task_id, "question": task.question,
            "skill_id": task.skill_id, "tema_id": task.npp_id,
            "tema_title": task.tema_title, "source": "minimal_template",
            "validation_status": "validated",
        } if task else None,
    }
    topic = result.state.topic
    return {
        "status": "ready" if result.topic_supported else "unsupported_topic",
        "answer": result.answer,
        "mode": "practice",
        "session_mode": "practice",
        # Only a structured ActiveTask can populate this; there is no prose path.
        "last_tutor_task": task.question if task else "",
        "answer_verdict": result.verdict,
        "answer_verdict_detail": (result.grading.detail if result.grading else None),
        "final_topic": topic.npp_id or "unknown",
        "effective_topic": topic.npp_id or "",
        "selected_oblast": str(payload.get("selected_oblast") or ""),
        "engine": "minimal",
        "next_state": next_state,
    }


def supported_topics_message() -> str:
    names = ", ".join(f"„{s.title}”" for s in SKILLS)
    return f"Za sada podržavam ove teme: {names}."


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
