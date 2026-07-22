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

from matbot.minimal import concept_facts, skills, solution_facts
from matbot.minimal.grading import (
    AMBIGUOUS_FINAL_ANSWER,
    NOT_CHECKABLE,
    GradingResult,
    grade,
    target_denominator,
)
from matbot.minimal.intent import (
    NEW_TASK_INTENTS,
    TurnIntent,
    classify_turn,
    parse_shape_request,
)
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

#: Concept families now live in concept_facts, keyed by resolved skill.


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


def _offer_task(state: SessionState,
                shape: str | None = None) -> tuple[SessionState, ActiveTask | None]:
    """Create and activate a task for the selected topic, or nothing.

    ``shape`` is a hint only — ``skills.generate_question`` itself decides
    whether the resolved skill supports it (only ``fraction_equation_additive``
    does), so passing one for an unrelated skill is harmless.
    """
    topic = state.topic
    if not topic.supported:
        return state, None
    made = skills.generate_question(
        topic.skill_id, seed=f"{state.session_id}|{state.turn_index}",
        avoid=state.recent_questions, difficulty=state.difficulty_level,
        shape=shape)
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
    client_turn_id: Any = "",
    openai_chat: Callable | None = None,
    model: str = "",
    timeout: float | None = None,
) -> TurnResult:
    """Run exactly one Practice turn.

    ``client_turn_id`` is carried through to telemetry only. Whether a PHYSICAL
    request is a replay of an earlier one (same browser turn retried over a
    transport fallback) is decided at the boundary, before this function is
    ever called — this function has no memory of previous calls and always
    performs a real transition for the turn it is given.
    """
    student_raw = str(raw_message if raw_message is not None else "")
    # Decision trace. Written as decisions happen, never read back.
    trace: dict = {
        "turn_intent": "", "intent_source": "", "concept_fact_kind": "",
        "concept_facts_resolved": False,
        "pending_confirmation_before": state.pending_confirmation,
        "pending_confirmation_after": "", "confirmation_choice": "",
        "client_turn_id": str(client_turn_id or ""),
        "idempotency_replay": False,
        "task_transition": "", "previous_task_id": "", "current_task_id": "",
        "requested_task_shape": "",
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
        # Facts are chosen by the RESOLVED SKILL first. Selecting them by
        # question shape alone is what let an equation lesson be answered with
        # fraction-expansion prose.
        equation = solution_facts.resolve_equation_facts(task.question) \
            if task is not None else None
        facts = concept_facts.resolve_for_skill(
            state.topic.skill_id, student_raw, equation)
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
    #
    # EVERY distinct call that reaches here creates a fresh task, even when the
    # student's wording is identical to an earlier turn and even when a task is
    # already active — a repeated message is a repeated REQUEST, not a repeated
    # HTTP delivery of the same one. Production kept returning the existing
    # task for "Daj mi novi zadatak." sent again because it treated identical
    # NORMALIZED TEXT as proof of a duplicate transport request; the two are
    # unrelated; the actual transport case (SSE falling back to JSON for the
    # SAME physical submission) is handled above this function, keyed by
    # ``client_turn_id``, before ``handle_turn`` is ever invoked.
    if intent in NEW_TASK_INTENTS:
        previous_task_id = task.task_id if task is not None else ""
        # A message describing a whole equation SHAPE ("a-x=b", "5/6 - x =
        # 1/3") is a task-shape request, detected deterministically — never by
        # OpenAI. Shared, skill-agnostic parsing; only ``fraction_equation_
        # additive`` currently has more than one shape to honour. A shape
        # named under any other skill gets an honest decline rather than a
        # silently unrelated task or, worse, being graded as a wrong answer
        # (the exact production defect this closes).
        requested_shape = parse_shape_request(student_raw)
        trace["requested_task_shape"] = requested_shape or ""
        if requested_shape and state.topic.skill_id != "fraction_equation_additive":
            trace["task_transition"] = ""
            trace["pending_confirmation_after"] = state.pending_confirmation
            ctx = RenderContext(state=state, intent=intent.value, task=task,
                                unsupported_shape=requested_shape)
            return TurnResult(answer=render(ctx), state=state,
                              intent=intent.value, task=task,
                              student_raw=student_raw, telemetry=dict(trace))
        state = _apply_difficulty(state, intent)
        state, task = _offer_task(state, shape=requested_shape)
        if task is None:
            trace["task_transition"] = ""
            ctx = RenderContext(state=state, intent=intent.value,
                                unsupported_topic=topic.title)
            trace["pending_confirmation_after"] = state.pending_confirmation
            return TurnResult(answer=render(ctx), state=state,
                              intent=intent.value, topic_supported=False,
                              student_raw=student_raw, telemetry=dict(trace))
        # The OLD task is neither solved nor wrong — it is simply no longer
        # being asked. No verdict, no attempt, no streak or solved_count change;
        # its identity is preserved here for audit, since active_task already
        # points at the new one.
        trace["task_transition"] = "replaced" if previous_task_id else "created"
        trace["previous_task_id"] = previous_task_id
        trace["current_task_id"] = task.task_id
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
        # AMBIGUOUS ("several numbers, no declared answer") and NOT_CHECKABLE
        # ("nothing recognisable as any answer form") both mean no candidate
        # answer was ever identified — ``graded_answer`` is empty for either.
        # Neither is an attempt: left counting, three lines of working (or a
        # task-shape request the intent classifier failed to recognise) would
        # have tripped MAX_WRONG_ATTEMPTS and revealed the answer to a student
        # who never actually answered wrong.
        not_an_attempt = result.detail in (AMBIGUOUS_FINAL_ANSWER, NOT_CHECKABLE)
        counted = task if not_an_attempt else ActiveTask(**{
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
        give_up = (not not_an_attempt) and counted.wrong_attempts >= MAX_WRONG_ATTEMPTS
        # Neither an ambiguous nor an uncheckable message is a wrong answer,
        # so the streak survives either.
        state = state if not_an_attempt else state.broke_streak()
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
