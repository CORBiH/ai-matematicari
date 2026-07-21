# -*- coding: utf-8 -*-
"""The flow, in one readable function.

    raw input → classify TurnIntent → read SessionState → operate on ActiveTask
    → produce one GradingResult → update state → render

Ownership, by construction:
  * topic identity   → ``skills.resolve_topic`` only
  * task lifecycle   → this module only (via ``state`` transitions)
  * grading          → ``grading.grade`` only
  * wording          → ``renderer`` only

``handle_turn`` is pure with respect to its inputs: it copies nothing back into
the caller's dict and returns a fresh state. The raw student message is carried
through untouched.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from matbot.minimal import skills
from matbot.minimal.grading import GradingResult, grade
from matbot.minimal.intent import NEW_TASK_INTENTS, TurnIntent, classify
from matbot.minimal.renderer import RenderContext, render
from matbot.minimal.state import ActiveTask, SessionState, new_task

#: How many wrong attempts before the answer is shown and the task closed, so a
#: child is never trapped on one task.
MAX_WRONG_ATTEMPTS = 3


@dataclass(frozen=True)
class TurnResult:
    """What one turn produced. The adapter translates this at the boundary."""
    answer: str
    state: SessionState
    intent: str
    grading: GradingResult | None = None
    task: ActiveTask | None = None
    topic_supported: bool = True
    student_raw: str = ""

    @property
    def verdict(self) -> str | None:
        return self.grading.verdict if self.grading else None


def _offer_task(state: SessionState) -> tuple[SessionState, ActiveTask | None]:
    """Create and activate a task for the selected topic, or nothing."""
    topic = state.topic
    if not topic.supported:
        return state, None
    made = skills.generate_question(
        topic.skill_id, seed=f"{state.session_id}|{state.turn_index}",
        avoid=state.recent_questions, difficulty=state.difficulty_level)
    if made is None:
        return state, None
    question, expected = made
    task = new_task(skill_id=topic.skill_id, question=question,
                    expected_display=expected, topic=topic)
    return state.with_task(task), task


def _apply_difficulty(state: SessionState, intent: TurnIntent) -> SessionState:
    """HARDER/EASIER move the level by one, bounded; NEW_TASK keeps it.

    The level only changes for skills that actually implement bands, so the
    engine never records a difficulty change it cannot deliver.
    """
    if intent not in (TurnIntent.HARDER, TurnIntent.EASIER):
        return state
    if not skills.supports_difficulty(state.topic.skill_id):
        return state
    step = 1 if intent is TurnIntent.HARDER else -1
    return state.with_difficulty(state.difficulty_level + step)


def handle_turn(
    *,
    raw_message: Any,
    state: SessionState,
    selected_topic: Any = "",
    selected_oblast: Any = "",
    openai_chat: Callable | None = None,
    model: str = "",
    timeout: float | None = None,
) -> TurnResult:
    """Run exactly one Practice turn."""
    student_raw = str(raw_message if raw_message is not None else "")
    state = state.next_turn()

    # The SELECTED topic is authoritative and is re-resolved every turn, so the
    # client cannot drift it and the student's words cannot change it.
    topic = skills.resolve_topic(state.grade, selected_topic, selected_oblast)
    if not topic.supported and state.topic.supported and not str(selected_topic or "").strip():
        topic = state.topic          # nothing new selected → keep the session's
    state = state.with_topic(topic)

    # The client echoes the CANONICAL id back on later turns (index.html's
    # adoptResponseTopic overwrites state.topic with effective_topic), so the
    # ORIGINAL runtime id is recorded once and then never overwritten.
    incoming = str(selected_topic or "").strip()
    if incoming and not state.origin_runtime_id:
        state = state.with_origin_runtime_id(incoming)

    intent = classify(student_raw).intent
    task = state.active_task

    if not topic.supported:
        # Honest refusal. Never a task from a neighbouring topic; the caller
        # decides whether to hand the turn to the legacy system.
        ctx = RenderContext(state=state, intent=intent.value,
                            unsupported_topic=topic.title or topic.runtime_id)
        return TurnResult(answer=render(ctx), state=state, intent=intent.value,
                          topic_supported=False, student_raw=student_raw)

    # ---- NEW TASK ---------------------------------------------------------
    if intent in NEW_TASK_INTENTS or (task is None and intent is not TurnIntent.HELP):
        state = _apply_difficulty(state, intent)
        state, task = _offer_task(state)
        if task is None:
            ctx = RenderContext(state=state, intent=intent.value,
                                unsupported_topic=topic.title)
            return TurnResult(answer=render(ctx), state=state,
                              intent=intent.value, topic_supported=False,
                              student_raw=student_raw)
        ctx = RenderContext(state=state, intent=TurnIntent.NEW_TASK.value, task=task)
        return TurnResult(answer=render(ctx), state=state,
                          intent=intent.value, task=task,
                          student_raw=student_raw)

    # ---- HELP: never consumes or replaces the task -------------------------
    if intent is TurnIntent.HELP:
        if task is None:
            ctx = RenderContext(state=state, intent=intent.value)
            return TurnResult(answer=render(ctx), state=state,
                              intent=intent.value, student_raw=student_raw)
        updated = ActiveTask(**{**task.to_dict(),
                                "hints_given": task.hints_given + 1})
        state = state.with_updated_task(updated)
        ctx = RenderContext(state=state, intent=intent.value, task=updated,
                            hint_level=updated.hints_given)
        return TurnResult(answer=render(ctx), state=state, intent=intent.value,
                          task=updated, student_raw=student_raw)

    # ---- ANSWER: exactly one GradingResult ---------------------------------
    if intent is TurnIntent.ANSWER and task is not None:
        result = grade(task, student_raw)
        counted = ActiveTask(**{
            **task.to_dict(),
            "attempts": task.attempts + 1,
            "wrong_attempts": task.wrong_attempts + (0 if result.solved else 1),
            "solved": result.solved,
        })
        if result.solved:
            state = state.with_updated_task(counted).completed_task()
            ctx = RenderContext(state=state, intent=intent.value, grading=result,
                                task=counted, may_reveal=True)
            return TurnResult(answer=render(ctx, openai_chat=openai_chat,
                                            model=model, timeout=timeout),
                              state=state, intent=intent.value, grading=result,
                              task=counted, student_raw=student_raw)
        give_up = counted.wrong_attempts >= MAX_WRONG_ATTEMPTS
        state = state.broke_streak()
        state = state.cleared_task() if give_up else state.with_updated_task(counted)
        ctx = RenderContext(state=state, intent=intent.value, grading=result,
                            task=counted, may_reveal=give_up)
        return TurnResult(answer=render(ctx), state=state, intent=intent.value,
                          grading=result, task=counted, student_raw=student_raw)

    # ---- anything else -----------------------------------------------------
    ctx = RenderContext(state=state, intent=intent.value, task=task)
    return TurnResult(answer=render(ctx), state=state, intent=intent.value,
                      task=task, student_raw=student_raw)
