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

from dataclasses import dataclass, field
from typing import Any, Callable

from matbot.minimal import concept_facts, skills
from matbot.minimal.grading import (
    AMBIGUOUS_FINAL_ANSWER,
    GradingResult,
    grade,
    target_denominator,
)
from matbot.minimal.intent import (
    NEW_TASK_INTENTS,
    TurnIntent,
    classify_turn,
)
from matbot.minimal.intent import fold as intent_fold
from matbot.minimal.intent import (
    confirmation_choice,
    is_affirmation,
    is_decline,
)
from matbot.minimal.renderer import RenderContext, render
from matbot.minimal.state import ActiveTask, SessionState, new_task

#: How many wrong attempts before the answer is shown and the task closed, so a
#: child is never trapped on one task.
MAX_WRONG_ATTEMPTS = 3

#: Skills whose concept questions are about fraction expansion.
_FRACTION_SKILLS = frozenset({"fraction_expand", "fraction_add_unlike"})


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
    #: Decision trace for telemetry ONLY. Never read back to drive behavior.
    telemetry: dict = field(default_factory=dict)

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


def _request_signature(raw_message: Any) -> str:
    """Compact signature of a new-task REQUEST, for immediate-repeat detection."""
    import re as _re
    folded = _re.sub(r"[^a-z0-9 ]+", " ", intent_fold(raw_message))
    return " ".join(folded.split())[:80]


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
    # Decision trace. Written as decisions happen, never read back.
    trace: dict = {
        "turn_intent": "", "intent_source": "", "concept_fact_kind": "",
        "concept_facts_resolved": False,
        "pending_confirmation_before": state.pending_confirmation,
        "pending_confirmation_after": "", "confirmation_choice": "",
    }
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

    # ---- a pending yes/no the TUTOR asked -----------------------------------
    # Consumed BEFORE classification and always cleared, so a stale "da" can
    # never leak into a later turn. Production asked "Da li želiš novi zadatak?"
    # and then answered "da" with "Nije mi jasno šta želiš."
    pending = state.pending_confirmation
    forced_intent: TurnIntent | None = None
    if pending:
        state = state.confirmation_consumed()
        if pending == "new_task" and is_affirmation(student_raw):
            forced_intent = TurnIntent.NEW_TASK
            trace["confirmation_choice"] = "task"
        elif pending == "new_task" and is_decline(student_raw):
            trace["confirmation_choice"] = "decline"
            trace.update(turn_intent="DECLINED", intent_source="deterministic",
                         pending_confirmation_after=state.pending_confirmation)
            ctx = RenderContext(state=state, intent="declined",
                                task=state.active_task)
            return TurnResult(answer=render(ctx), state=state,
                              intent="declined", task=state.active_task,
                              student_raw=student_raw, telemetry=dict(trace))
        elif pending == "task_or_explanation":
            choice = confirmation_choice(student_raw)
            trace["confirmation_choice"] = choice
            if choice == "task":
                forced_intent = TurnIntent.NEW_TASK
            elif choice == "explanation":
                forced_intent = TurnIntent.CONCEPT_QUESTION
        # anything else: the confirmation is dropped and the turn is classified
        # normally, so an unrelated message is never swallowed by it.

    # Deterministic rules decide; the model is consulted only for a genuine tie.
    if forced_intent is not None:
        intent = forced_intent           # no model call for a plain "da"
        trace["intent_source"] = "confirmation"
    else:
        decided = classify_turn(student_raw, openai_chat=openai_chat,
                                model=model, timeout=timeout,
                                has_active_task=state.active_task is not None)
        intent = decided.intent
        trace["intent_source"] = ("model" if decided.matched == "model_classifier"
                                  else "deterministic")
    trace["turn_intent"] = intent.name
    task = state.active_task

    if not topic.supported:
        # Honest refusal. Never a task from a neighbouring topic; the caller
        # decides whether to hand the turn to the legacy system.
        ctx = RenderContext(state=state, intent=intent.value,
                            unsupported_topic=topic.title or topic.runtime_id)
        trace["pending_confirmation_after"] = state.pending_confirmation
        return TurnResult(answer=render(ctx), state=state, intent=intent.value,
                          topic_supported=False, student_raw=student_raw,
                          telemetry=dict(trace))

    # ---- CONCEPT QUESTION: answer it, change NOTHING ------------------------
    # A question about the maths is not a task request. Production created
    # "Proširi 2/4 na nazivnik 20." in reply to "šta ako imamo isti brojnik…"
    # and never answered the student.
    if intent is TurnIntent.CONCEPT_QUESTION:
        # The expansion rule underlies BOTH fraction skills, so a concept
        # question about it resolves to verified facts either way.
        facts = concept_facts.resolve_expand_question(student_raw) \
            if state.topic.skill_id in _FRACTION_SKILLS else None
        trace["concept_facts_resolved"] = facts is not None
        trace["concept_fact_kind"] = facts.kind if facts is not None else ""
        trace["pending_confirmation_after"] = state.pending_confirmation
        ctx = RenderContext(state=state, intent=intent.value, task=task,
                            concept_question=student_raw)
        return TurnResult(
            answer=render(ctx, openai_chat=openai_chat, model=model,
                          timeout=timeout),
            state=state,                 # streak, attempts, task all untouched
            intent=intent.value, task=task, student_raw=student_raw,
            telemetry=dict(trace))

    # ---- NEW TASK ----------------------------------------------------------
    # ONLY an explicit new-task intent may create a task. Previously any
    # non-HELP intent did so whenever no task was active, which is how an
    # unrecognised message silently produced one.
    if intent in NEW_TASK_INTENTS:
        # A repeated identical request while a task is already active returns
        # THAT task instead of replacing it (production issued two ids for the
        # same generated task ten seconds apart).
        signature = _request_signature(student_raw)
        if (task is not None and intent is TurnIntent.NEW_TASK
                and signature and signature == state.last_request_signature):
            ctx = RenderContext(state=state, intent=intent.value, task=task)
            trace["pending_confirmation_after"] = state.pending_confirmation
            return TurnResult(answer=render(ctx), state=state,
                              intent=intent.value, task=task,
                              student_raw=student_raw, telemetry=dict(trace))
        state = state.with_request_signature(signature)
        state = _apply_difficulty(state, intent)
        state, task = _offer_task(state)
        if task is None:
            ctx = RenderContext(state=state, intent=intent.value,
                                unsupported_topic=topic.title)
            trace["pending_confirmation_after"] = state.pending_confirmation
            return TurnResult(answer=render(ctx), state=state,
                              intent=intent.value, topic_supported=False,
                              student_raw=student_raw, telemetry=dict(trace))
        ctx = RenderContext(
            state=state, intent=TurnIntent.NEW_TASK.value, task=task,
            difficulty_unsupported=(
                intent in (TurnIntent.HARDER, TurnIntent.EASIER)
                and not skills.supports_difficulty(state.topic.skill_id)))
        trace["pending_confirmation_after"] = state.pending_confirmation
        return TurnResult(answer=render(ctx), state=state,
                          intent=intent.value, task=task,
                          student_raw=student_raw, telemetry=dict(trace))

    # ---- SOLUTION REQUEST: reveal, but never credit it as progress ---------
    if intent is TurnIntent.SOLUTION_REQUEST and task is not None:
        revealed = ActiveTask(**{**task.to_dict(),
                                 "solved": True, "solution_revealed": True})
        # The task is CLOSED but not solved by the student: no solved_count and
        # no streak increment. The streak is left EXACTLY as it was — asking for
        # the worked solution is not a wrong mathematical attempt, so it must
        # not reset earlier progress either. Attempts and wrong_attempts are
        # untouched, so an immediate request records 0/0.
        state = state.with_updated_task(revealed).cleared_task()
        state = state.awaiting("new_task")
        ctx = RenderContext(state=state, intent=intent.value, task=revealed,
                            may_reveal=True)
        trace["pending_confirmation_after"] = state.pending_confirmation
        return TurnResult(answer=render(ctx), state=state,
                          intent=intent.value, task=revealed,
                          student_raw=student_raw, telemetry=dict(trace))

    # ---- HELP: never consumes or replaces the task -------------------------
    if intent is TurnIntent.HELP:
        if task is None:
            ctx = RenderContext(state=state, intent=intent.value)
            trace["pending_confirmation_after"] = state.pending_confirmation
            return TurnResult(answer=render(ctx), state=state,
                              intent=intent.value, student_raw=student_raw,
                              telemetry=dict(trace))
        updated = ActiveTask(**{**task.to_dict(),
                                "hints_given": task.hints_given + 1})
        state = state.with_updated_task(updated)
        ctx = RenderContext(state=state, intent=intent.value, task=updated,
                            hint_level=updated.hints_given)
        trace["pending_confirmation_after"] = state.pending_confirmation
        return TurnResult(answer=render(ctx), state=state, intent=intent.value,
                          task=updated, student_raw=student_raw,
                          telemetry=dict(trace))

    # ---- ANSWER: exactly one GradingResult ---------------------------------
    if intent is TurnIntent.ANSWER and task is not None:
        result = grade(task, student_raw)
        # An AMBIGUOUS message was never graded, so it is not an attempt. Left
        # counting, three lines of working would have tripped MAX_WRONG_ATTEMPTS
        # and revealed the answer to a student who had made no mistake.
        ambiguous = result.detail == AMBIGUOUS_FINAL_ANSWER
        counted = task if ambiguous else ActiveTask(**{
            **task.to_dict(),
            "attempts": task.attempts + 1,
            "wrong_attempts": task.wrong_attempts + (0 if result.solved else 1),
            "solved": result.solved,
        })
        if result.solved:
            # The feedback ASKS whether to continue, so the answer to that
            # question must be understood on the next turn.
            state = state.with_updated_task(counted).completed_task().awaiting("new_task")
            ctx = RenderContext(state=state, intent=intent.value, grading=result,
                                task=counted, may_reveal=True)
            trace["pending_confirmation_after"] = state.pending_confirmation
            return TurnResult(answer=render(ctx, openai_chat=openai_chat,
                                            model=model, timeout=timeout),
                              state=state, intent=intent.value, grading=result,
                              task=counted, student_raw=student_raw,
                              telemetry=dict(trace))
        give_up = (not ambiguous) and counted.wrong_attempts >= MAX_WRONG_ATTEMPTS
        # An ambiguous message is not a wrong answer, so the streak survives it.
        state = state if ambiguous else state.broke_streak()
        state = state.cleared_task() if give_up else state.with_updated_task(counted)
        ctx = RenderContext(state=state, intent=intent.value, grading=result,
                            task=counted, may_reveal=give_up,
                            target_denominator=target_denominator(counted))
        trace["pending_confirmation_after"] = state.pending_confirmation
        return TurnResult(answer=render(ctx), state=state, intent=intent.value,
                          grading=result, task=counted, student_raw=student_raw,
                          telemetry=dict(trace))

    # ---- anything else: ask what was meant; never touch task state ---------
    # The clarification ASKS "novi zadatak ili objašnjenje?", so the reply to
    # that question must be understood on the next turn.
    if task is None:
        state = state.awaiting("task_or_explanation")
    ctx = RenderContext(state=state, intent=TurnIntent.OTHER.value, task=task)
    trace["pending_confirmation_after"] = state.pending_confirmation
    return TurnResult(answer=render(ctx), state=state, intent=intent.value,
                      task=task, student_raw=student_raw,
                      telemetry=dict(trace))
