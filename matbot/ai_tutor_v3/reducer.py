# -*- coding: utf-8 -*-
"""The deterministic, server-controlled reducer.

The model PROPOSES (turn kind, provisional verdict, pedagogical action, next
concept, difficulty). The reducer DISPOSES: it validates the proposal and
applies only allowed transitions to counters, task status, hint level, coverage,
mastery, difficulty and clarification state.

Hard rules enforced here, for every lesson and skill (no lesson-specific logic):

  * one client turn mutates counters at most once;
  * ``no_attempt`` mutates no progress; ``needs_clarification`` mutates no
    progress and preserves the task;
  * questions / comments / help / task-requests are never graded as answers;
  * a low-confidence, contradictory, or missing assessment preserves the task
    and asks for clarification rather than punishing the student;
  * revealed / assisted work is not independent mastery evidence;
  * while verification is model-only, mastery is marked provisional.

The reducer separates two things the schema keeps distinct on purpose:
  - ``AuthoritativeOutcome`` describes what GRADING did (verdict + counter
    deltas + whether grading completed/failed the task). For a non-answer turn
    grading does nothing, so the outcome is ``no_attempt`` with the task
    preserved.
  - the returned ``new_state`` also carries any TASK-LIFECYCLE change the turn
    legitimately requested (reveal, replace, difficulty) — an explicit student
    action, not a grading result. ``next_action`` tells the orchestrator what to
    render.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from matbot.ai_tutor_v3.schemas import (
    AuthoritativeOutcome,
    CompletedTaskSummary,
    PracticeModelAssessment,
    StudentTurnInterpretation,
    TutorSessionState,
)
from matbot.ai_tutor_v3.verifier import VerificationDecision

_SCHEMA_VERSION = "v3.1"

# What the orchestrator should render next.
NARRATE_FEEDBACK = "narrate_feedback"
GIVE_HINT = "give_hint"
EXPLAIN_CONCEPT = "explain_concept"
GENERATE_TASK = "generate_task"
REVEAL_SOLUTION = "reveal_solution"
CLARIFY = "clarify"
OFF_TOPIC = "off_topic"
ACKNOWLEDGE = "acknowledge"

_DEFAULT_CLARIFICATION = "Možeš li mi pojasniti šta si mislio/la?"

#: Below this, an answer's provisional assessment is treated as too uncertain to
#: grade — the turn becomes a clarification instead of a punishment.
_MIN_ANSWER_CONFIDENCE = 0.35


@dataclass
class ReducerResult:
    outcome: AuthoritativeOutcome
    new_state: TutorSessionState
    next_action: str
    response_category: str


def _clone(state: TutorSessionState) -> TutorSessionState:
    return state.model_copy(deep=True)


def _clarify_outcome(seed: str) -> AuthoritativeOutcome:
    return AuthoritativeOutcome(
        schema_version=_SCHEMA_VERSION, verdict="needs_clarification",
        attempt_count_delta=0, wrong_attempt_count_delta=0,
        streak_action="preserve", solved_count_delta=0,
        task_status_after="active", task_completed=False,
        preserve_active_task=True, needs_narration=True,
        clarification_prompt_seed=seed or _DEFAULT_CLARIFICATION,
    )


def _no_progress_outcome() -> AuthoritativeOutcome:
    return AuthoritativeOutcome(
        schema_version=_SCHEMA_VERSION, verdict="no_attempt",
        attempt_count_delta=0, wrong_attempt_count_delta=0,
        streak_action="preserve", solved_count_delta=0,
        task_status_after="active", task_completed=False,
        preserve_active_task=True, needs_narration=True,
    )


def reduce_turn(
    *,
    state: TutorSessionState,
    interpretation: StudentTurnInterpretation,
    assessment: PracticeModelAssessment | None,
    verification: VerificationDecision,
) -> ReducerResult:
    """Compute the authoritative outcome and next state for one turn."""
    kind = interpretation.turn_kind

    # Failure-safe: a required-verification refusal never reaches here (the
    # dispatcher refuses before mutation), but if it did, do not mutate.
    if not verification.can_proceed:
        return ReducerResult(_clarify_outcome(
            "Trenutno ne mogu pouzdano provjeriti odgovor. Pokušaj ponovo."),
            _clone(state), CLARIFY, "verification_unavailable")

    if kind == "off_topic":
        return ReducerResult(_no_progress_outcome(), _clone(state),
                             OFF_TOPIC, "off_topic")

    if kind == "ambiguous":
        seed = interpretation.clarification_question or _DEFAULT_CLARIFICATION
        return ReducerResult(_clarify_outcome(seed), _clone(state),
                             CLARIFY, "clarification")

    if kind in ("question", "comment", "confirmation"):
        # Answered/acknowledged without grading; the active task is preserved and
        # the student is reminded of it by the orchestrator.
        action = EXPLAIN_CONCEPT if kind == "question" else ACKNOWLEDGE
        return ReducerResult(_no_progress_outcome(), _clone(state),
                             action, "concept" if kind == "question" else "comment")

    if kind == "help_request":
        return _apply_hint(state)

    if kind == "task_request":
        return _apply_new_task(state, forced_concept=interpretation.requested_action)

    if kind == "difficulty_change":
        return _apply_difficulty(state, assessment)

    if kind == "solution_request":
        return _apply_reveal(state)

    if kind == "answer":
        return _apply_answer(state, interpretation, assessment)

    # Unknown/unhandled kind: never punish.
    return ReducerResult(_clarify_outcome(_DEFAULT_CLARIFICATION),
                         _clone(state), CLARIFY, "clarification")


# --------------------------------------------------------------------------- #
# Answer grading                                                              #
# --------------------------------------------------------------------------- #
def _apply_answer(
    state: TutorSessionState,
    interpretation: StudentTurnInterpretation,
    assessment: PracticeModelAssessment | None,
) -> ReducerResult:
    new = _clone(state)
    task = new.active_task

    # No active task to grade against, or the model itself says this is not an
    # attempt, or no/low-confidence assessment → clarify, never punish.
    if task is None or assessment is None or not assessment.is_answer_attempt:
        seed = interpretation.clarification_question or _DEFAULT_CLARIFICATION
        return ReducerResult(_clarify_outcome(seed), new, CLARIFY, "clarification")
    if (assessment.confidence < _MIN_ANSWER_CONFIDENCE
            or interpretation.confidence < _MIN_ANSWER_CONFIDENCE):
        seed = interpretation.clarification_question or _DEFAULT_CLARIFICATION
        return ReducerResult(_clarify_outcome(seed), new, CLARIFY, "clarification")

    verdict = assessment.proposed_verdict
    assisted = task.hints_given > 0 or task.solution_revealed
    # Misconception codes are recorded in the turn audit, not mutated into
    # mastery state — mastery is provisional while verification is model-only.

    if verdict == "correct":
        return _grade_correct(new, task, assisted)
    if verdict == "incorrect":
        return _grade_incorrect(new, task)
    if verdict == "partial":
        return _grade_partial(new, task)
    # not_checkable / needs_clarification / no_attempt proposed on an answer turn
    seed = interpretation.clarification_question or _DEFAULT_CLARIFICATION
    return ReducerResult(_clarify_outcome(seed), new, CLARIFY, "clarification")


def _grade_correct(new, task, assisted: bool) -> ReducerResult:
    task.attempts += 1
    new.counters.attempts += 1
    new.counters.tasks_completed += 1
    if assisted:
        new.counters.solved_assisted += 1
        streak = "preserve"
    else:
        new.counters.solved_independent += 1
        new.counters.correct_streak += 1
        streak = "increment"
    # Coverage + provisional mastery evidence for this concept.
    if task.target_id and task.target_id not in new.coverage.covered:
        new.coverage.covered.append(task.target_id)
    new.mastery.per_concept[task.concept_id] = (
        "provisional_mastered" if not assisted else "provisional_assisted")
    new.mastery.provisional = True
    new.completed_tasks.append(CompletedTaskSummary(
        task_id=task.task_id, concept_id=task.concept_id,
        target_id=task.target_id, verdict="correct", independent=not assisted))
    new.active_task = None
    new.hint.current_level = 0
    outcome = AuthoritativeOutcome(
        schema_version=_SCHEMA_VERSION, verdict="correct",
        attempt_count_delta=1, wrong_attempt_count_delta=0,
        streak_action=streak, solved_count_delta=1,
        task_status_after="completed", task_completed=True,
        preserve_active_task=False, needs_narration=True,
        reason_codes=["assisted"] if assisted else ["independent"],
    )
    return ReducerResult(outcome, new, NARRATE_FEEDBACK, "feedback_correct")


def _grade_incorrect(new, task) -> ReducerResult:
    task.attempts += 1
    task.wrong_attempts += 1
    new.counters.attempts += 1
    new.counters.wrong_attempts += 1
    new.counters.correct_streak = 0
    _bump_target_attempts(new, task.target_id)
    outcome = AuthoritativeOutcome(
        schema_version=_SCHEMA_VERSION, verdict="incorrect",
        attempt_count_delta=1, wrong_attempt_count_delta=1,
        streak_action="reset", solved_count_delta=0,
        task_status_after="active", task_completed=False,
        preserve_active_task=True, needs_narration=True,
    )
    return ReducerResult(outcome, new, NARRATE_FEEDBACK, "feedback_incorrect")


def _grade_partial(new, task) -> ReducerResult:
    task.attempts += 1
    new.counters.attempts += 1
    _bump_target_attempts(new, task.target_id)
    outcome = AuthoritativeOutcome(
        schema_version=_SCHEMA_VERSION, verdict="partial",
        attempt_count_delta=1, wrong_attempt_count_delta=0,
        streak_action="preserve", solved_count_delta=0,
        task_status_after="active", task_completed=False,
        preserve_active_task=True, needs_narration=True,
    )
    return ReducerResult(outcome, new, NARRATE_FEEDBACK, "feedback_partial")


# --------------------------------------------------------------------------- #
# Non-grading lifecycle                                                       #
# --------------------------------------------------------------------------- #
def _apply_hint(state: TutorSessionState) -> ReducerResult:
    new = _clone(state)
    if new.active_task is not None:
        new.active_task.hints_given += 1
        new.hint.current_level += 1
        new.hint.max_level_reached = max(
            new.hint.max_level_reached, new.hint.current_level)
    return ReducerResult(_no_progress_outcome(), new, GIVE_HINT, "hint")


def _apply_new_task(state: TutorSessionState, *, forced_concept) -> ReducerResult:
    # No grading; a fresh task will be generated by the orchestrator. The
    # current task is explicitly replaced (not failed), so counters are
    # untouched. Coverage targets are never removed.
    new = _clone(state)
    new.active_task = None
    new.hint.current_level = 0
    return ReducerResult(_no_progress_outcome(), new, GENERATE_TASK, "new_task")


def _apply_difficulty(state: TutorSessionState,
                      assessment: PracticeModelAssessment | None) -> ReducerResult:
    new = _clone(state)
    direction = assessment.difficulty_suggestion if assessment else None
    if direction == "easier":
        new.difficulty.level = max(1, new.difficulty.level - 1)
    elif direction == "harder":
        new.difficulty.level = min(5, new.difficulty.level + 1)
    # A difficulty change replaces the task but NEVER removes lesson coverage.
    new.active_task = None
    new.hint.current_level = 0
    return ReducerResult(_no_progress_outcome(), new, GENERATE_TASK, "difficulty_change")


def _apply_reveal(state: TutorSessionState) -> ReducerResult:
    new = _clone(state)
    task = new.active_task
    if task is not None:
        task.solution_revealed = True
        new.counters.revealed += 1
        # Revealed work is assisted, never independent mastery evidence.
        new.mastery.per_concept[task.concept_id] = "provisional_revealed"
        new.mastery.provisional = True
        new.completed_tasks.append(CompletedTaskSummary(
            task_id=task.task_id, concept_id=task.concept_id,
            target_id=task.target_id, verdict="no_attempt", independent=False))
        if task.target_id and task.target_id not in new.coverage.covered:
            new.coverage.covered.append(task.target_id)
        new.active_task = None
    new.hint.current_level = 0
    return ReducerResult(_no_progress_outcome(), new, REVEAL_SOLUTION, "reveal")


def _bump_target_attempts(new, target_id: str) -> None:
    if not target_id:
        return
    new.coverage.attempts_per_target[target_id] = (
        new.coverage.attempts_per_target.get(target_id, 0) + 1)


def next_uncovered_target(state: TutorSessionState) -> str | None:
    """The next coverage target to practice: first uncovered, else the
    least-attempted, so Practice covers the whole lesson instead of drilling one
    subtopic. Difficulty never removes a target from this rotation."""
    for target in state.coverage.targets:
        if target not in state.coverage.covered:
            return target
    if not state.coverage.targets:
        return None
    return min(state.coverage.targets,
               key=lambda t: state.coverage.attempts_per_target.get(t, 0))


def new_task_id() -> str:
    return "t_" + uuid.uuid4().hex[:12]
