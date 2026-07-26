# -*- coding: utf-8 -*-
"""A bounded, deterministic task-coherence gate + difficulty comparison.

Runs on a model-PROPOSED ``TaskSpecification`` before it is activated (see
``dispatcher._render_new_task``), the same way ``quality_gate`` runs on
student-facing text before it is shown. Every check here is GENERIC — grounded
in the task's own typed, self-declared metadata (``task_family_id``,
``pedagogical_goal``, ``required_student_operations``,
``comparison_or_invariance_goal``, ``coherence_claim``,
``expected_reasoning_steps``, ``difficulty_signature``) and the Blueprint it
must belong to — never a lesson-specific phrase list or a hardcoded fix for
one example (e.g. "4/6" or "8/12").

Two honesty notes this module does NOT pretend around:

1. Deterministic code cannot understand arbitrary mathematical prose. Every
   check below either (a) verifies STRUCTURAL facts against the Blueprint
   (unknown family/target/concept, answer-kind mismatch, unsupported
   verification type), or (b) verifies INTERNAL CONSISTENCY of the model's
   OWN typed self-declaration (e.g. it claims 1 reasoning step but lists 3
   required operations). Neither kind requires — or claims — to grade whether
   the task's Bosnian sentence is mathematically well-written prose; that
   remains a live/human-review concern (see the final report).
2. A Blueprint generated before this pass has EMPTY defaults for every new
   invariant field (Pydantic fills them in on load — see ``schemas.py``'s
   ``TaskFamily``/``TaskSpecification`` additions). This gate FAILS OPEN for
   any check whose required data is simply absent (recorded as a metric, not
   a failure) rather than rejecting every task from an old, real, currently
   cached Blueprint. See ``lesson_blueprint.py``: the (lesson_id, source_hash)
   cache key is unrelated to this schema change, so an already-stored
   Blueprint is REUSED as-is, not regenerated, until its curriculum source
   metadata changes or an operator deliberately clears it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from matbot.ai_tutor_v3.schemas import DifficultySignature, TaskSpecification

#: At most ONE model repair attempt per rejected task proposal.
MAX_REPAIR_ATTEMPTS = 1

#: Structurally self-cancelling operation-kind pairs — a GENERIC, closed-
#: vocabulary rule (see ``schemas.StudentOperationKind``), never a fraction-
#: specific regex. If both kinds of a pair appear in one task's
#: ``required_student_operations`` with no stated
#: ``comparison_or_invariance_goal``, the task looks like "do X then undo X"
#: with no pedagogical point — problem (B) from the audit, generalized.
_SELF_CANCELLING_PAIRS = (
    frozenset({"expand", "reduce"}),
    frozenset({"add", "subtract"}),
    frozenset({"multiply", "divide"}),
)

#: Answer kinds that imply a checkable result should exist internally.
_CHECKABLE_ANSWER_KINDS = frozenset({
    "boolean_with_reason", "rational_value", "integer_value",
    "equation_solution", "set_value",
})

#: Generic, grade-independent ceiling on self-declared reasoning steps — a
#: sanity bound against an implausible claim, never a per-lesson number.
_MAX_PLAUSIBLE_REASONING_STEPS = 8

#: The 18 named failure categories this gate can report. Each is backed by a
#: concrete deterministic check below — see the module docstring for the
#: hard/soft split (``HARD_FAILURE_CATEGORIES``).
ALL_FAILURE_CATEGORIES = (
    "missing_pedagogical_goal",
    "missing_required_operations",
    "missing_task_family",
    "unknown_task_family",
    "unknown_target_id",
    "unknown_concept_id",
    "self_cancelling_operation_pair_without_goal",
    "reasoning_steps_below_operation_count",
    "reasoning_steps_implausibly_high",
    "empty_coherence_claim_for_multistep_task",
    "difficulty_level_out_of_family_range",
    "answer_kind_mismatch_with_family",
    "duplicate_operation_kind_without_detail",
    "comparison_goal_without_comparison_operation",
    "task_text_too_short_for_claimed_steps",
    "expected_internal_missing_for_checkable_kind",
    "planned_verification_type_unsupported_by_blueprint",
    "difficulty_change_direction_not_guaranteed",
)

#: HARD categories: if still present after the one bounded repair, the turn
#: fails safely (no task committed) rather than being accepted anyway — these
#: are the categories where accepting the task would let something concretely
#: WRONG reach the student (wrong lesson, wrong answer kind, wrong verifier,
#: or a difficulty change that provably didn't move in the requested
#: direction). Every other category is SOFT: recorded in telemetry and worth
#: one repair attempt, but never blocks an otherwise-valid task — matching
#: this codebase's existing accept-with-telemetry-flag pattern (see
#: ``quality_gate``'s bounded repair-then-fallback).
#: NOTE on ``difficulty_change_direction_not_guaranteed``: deliberately SOFT,
#: not hard. The session's own ``difficulty.level`` dial (reducer._apply_
#: difficulty) already moves reliably in the requested direction independent
#: of this check — that existing, always-correct mechanism must never be
#: discarded just because the NEWER, optional ``difficulty_signature``-based
#: verification could not be performed (e.g. a model call that omits the
#: optional field). One bounded repair is still attempted (worth trying), and
#: the outcome is recorded in telemetry either way — but a task is never
#: rejected outright over this alone.
HARD_FAILURE_CATEGORIES = frozenset({
    "unknown_task_family",
    "unknown_target_id",
    "unknown_concept_id",
    "answer_kind_mismatch_with_family",
    "planned_verification_type_unsupported_by_blueprint",
})

#: Categories that reflect merely ABSENT optional enrichment metadata, never
#: an actual coherence PROBLEM in the task's content — a task/Blueprint that
#: predates this pass (or a caller/test fixture that never set these optional
#: fields) will legitimately trigger these every single time. Recorded in
#: telemetry like any other category, but the caller should NOT spend its one
#: bounded repair call on these alone — see ``dispatcher._apply_task_
#: coherence_gate``. This is what makes the "fail open when data is simply
#: absent" promise in the module docstring actually true in practice, not
#: just in words.
METADATA_ONLY_CATEGORIES = frozenset({
    "missing_pedagogical_goal",
    "missing_required_operations",
    "missing_task_family",
})


@dataclass
class TaskCoherenceResult:
    passed: bool
    failure_categories: list = field(default_factory=list)
    hard_failure_categories: list = field(default_factory=list)
    deterministic_metrics: dict = field(default_factory=dict)
    repair_seed: Optional[str] = None


@dataclass
class DifficultyComparisonResult:
    classification: str  # "easier" | "harder" | "same" | "ambiguous" | "insufficient_data"
    per_dimension_delta: dict = field(default_factory=dict)
    dimensions_compared: list = field(default_factory=list)


#: Per-dimension ordinal mapping: higher = harder. A bare int field is its own
#: order; a Literal field is mapped through a small fixed ladder; a bool field
#: is mapped so ``True`` means the HARDER state (``requires_recall_only`` is
#: inverted: recall-only is EASIER, so ``True`` there maps to the lower order).
_LITERAL_ORDERS = {
    "representation_complexity": {"single": 0, "mixed": 1, "abstract": 2},
    "distractor_similarity": {"low": 0, "medium": 1, "high": 2},
    "verbal_complexity": {"short": 0, "medium": 1, "long": 2},
}
_BOOL_HARDER_WHEN_TRUE = frozenset({
    "requires_multi_step_justification", "nonstandard_form",
})
_BOOL_HARDER_WHEN_FALSE = frozenset({"requires_recall_only"})

#: Every ordinal dimension ``compare_difficulty`` knows how to compare.
DIFFICULTY_DIMENSIONS = (
    "numeric_magnitude", "operation_count", "reasoning_steps", "concept_count",
    "representation_complexity", "distractor_similarity", "verbal_complexity",
    "requires_multi_step_justification", "nonstandard_form",
    "requires_recall_only",
)


def _dimension_order(dimension: str, value) -> int:
    if dimension in _LITERAL_ORDERS:
        return _LITERAL_ORDERS[dimension].get(value, 0)
    if dimension in _BOOL_HARDER_WHEN_TRUE:
        return 1 if value else 0
    if dimension in _BOOL_HARDER_WHEN_FALSE:
        return 0 if value else 1
    return int(value)


def compare_difficulty(
    old: Optional[DifficultySignature],
    new: Optional[DifficultySignature],
    relevant_dimensions: Optional[list] = None,
) -> DifficultyComparisonResult:
    """Compare two typed difficulty signatures along ordinal dimensions.

    Returns "insufficient_data" (never a guess) when either signature is
    missing — this is the honest outcome for a Blueprint/task predating this
    pass. Otherwise: "easier" iff every compared dimension moved down-or-equal
    with at least one strict decrease; "harder" the mirror image; "same" iff
    every dimension is unchanged; "ambiguous" iff dimensions moved in BOTH
    directions (a real mixed signal, not a coin flip).
    """
    if old is None or new is None:
        return DifficultyComparisonResult(classification="insufficient_data")
    dims = list(relevant_dimensions) if relevant_dimensions else list(DIFFICULTY_DIMENSIONS)
    deltas: dict = {}
    for dim in dims:
        if dim not in DIFFICULTY_DIMENSIONS:
            continue
        old_ord = _dimension_order(dim, getattr(old, dim))
        new_ord = _dimension_order(dim, getattr(new, dim))
        deltas[dim] = new_ord - old_ord
    if not deltas:
        return DifficultyComparisonResult(classification="insufficient_data")
    has_harder = any(d > 0 for d in deltas.values())
    has_easier = any(d < 0 for d in deltas.values())
    if has_harder and has_easier:
        classification = "ambiguous"
    elif has_harder:
        classification = "harder"
    elif has_easier:
        classification = "easier"
    else:
        classification = "same"
    return DifficultyComparisonResult(
        classification=classification, per_dimension_delta=deltas,
        dimensions_compared=list(deltas.keys()))


def check_task_coherence(
    spec: TaskSpecification, *, blueprint, grade: int,
    expected_difficulty_direction: Optional[str] = None,
    previous_signature: Optional[DifficultySignature] = None,
) -> TaskCoherenceResult:
    """Evaluate one proposed task against its Blueprint's declared invariants.

    ``expected_difficulty_direction`` ("easier"/"harder") and
    ``previous_signature`` are only passed by the caller when this task
    generation was triggered by an explicit difficulty-change turn — for a
    plain "novi zadatak" request neither is passed, and category #18
    (``difficulty_change_direction_not_guaranteed``) is never evaluated, per
    the task's own rule that a fresh task carries no directional guarantee.
    """
    failures: list = []
    metrics: dict = {}

    known_families = {f.family_id: f for f in blueprint.task_families}
    known_targets = {t.target_id for t in blueprint.coverage_targets}
    known_concepts = {c.concept_id for c in blueprint.concepts}
    supported_verification = set(blueprint.supported_verification_types)

    if not spec.pedagogical_goal:
        failures.append("missing_pedagogical_goal")
    if not spec.required_student_operations:
        failures.append("missing_required_operations")

    family = None
    if not spec.task_family_id:
        failures.append("missing_task_family")
    elif spec.task_family_id not in known_families:
        failures.append("unknown_task_family")
    else:
        family = known_families[spec.task_family_id]

    # Mirrors dispatcher's existing (already-run, earlier) off-lesson check
    # EXACTLY: a task belongs to the lesson if EITHER its target_id OR its
    # concept_id resolves — the model's concept_id is looser/coarser than the
    # curriculum's own target granularity, so requiring BOTH would reject
    # many legitimate tasks the dispatcher already accepts. Given the
    # dispatcher's own check runs first and rejects when NEITHER resolves,
    # these two categories are structurally unreachable at this call site
    # today — kept named/checked for completeness of the typed result (and in
    # case a future caller invokes this gate without that earlier check).
    target_known = spec.target_id in known_targets
    concept_known = spec.concept_id in known_concepts
    if not target_known and not concept_known:
        failures.append("unknown_target_id")
        failures.append("unknown_concept_id")

    op_kinds = [op.kind for op in spec.required_student_operations]
    op_kind_set = set(op_kinds)
    has_self_cancelling_pair = any(pair <= op_kind_set for pair in _SELF_CANCELLING_PAIRS)
    if has_self_cancelling_pair and not spec.comparison_or_invariance_goal:
        failures.append("self_cancelling_operation_pair_without_goal")

    if op_kinds and spec.expected_reasoning_steps < len(op_kinds):
        failures.append("reasoning_steps_below_operation_count")
    if spec.expected_reasoning_steps > _MAX_PLAUSIBLE_REASONING_STEPS:
        failures.append("reasoning_steps_implausibly_high")

    if len(op_kinds) >= 2 and not spec.coherence_claim:
        failures.append("empty_coherence_claim_for_multistep_task")

    if family is not None:
        if (family.typical_difficulty_min is not None
                and spec.difficulty_level < family.typical_difficulty_min):
            failures.append("difficulty_level_out_of_family_range")
        if (family.typical_difficulty_max is not None
                and spec.difficulty_level > family.typical_difficulty_max):
            failures.append("difficulty_level_out_of_family_range")
        if family.answer_kind != spec.answer_kind:
            failures.append("answer_kind_mismatch_with_family")

    seen_kinds_with_detail: dict = {}
    for op in spec.required_student_operations:
        if op.kind in seen_kinds_with_detail:
            prior_detail = seen_kinds_with_detail[op.kind]
            if not op.detail and not prior_detail:
                failures.append("duplicate_operation_kind_without_detail")
        seen_kinds_with_detail[op.kind] = op.detail

    if (spec.comparison_or_invariance_goal and "compare" not in op_kind_set
            and not has_self_cancelling_pair):
        failures.append("comparison_goal_without_comparison_operation")

    if len(spec.question.strip()) < 15 and spec.expected_reasoning_steps > 2:
        failures.append("task_text_too_short_for_claimed_steps")

    if spec.answer_kind in _CHECKABLE_ANSWER_KINDS and not spec.expected_internal:
        failures.append("expected_internal_missing_for_checkable_kind")

    if (spec.planned_verification_type is not None
            and spec.planned_verification_type not in supported_verification):
        failures.append("planned_verification_type_unsupported_by_blueprint")

    difficulty_comparison = None
    if expected_difficulty_direction in ("easier", "harder"):
        difficulty_comparison = compare_difficulty(previous_signature, spec.difficulty_signature)
        metrics["difficulty_comparison"] = difficulty_comparison.classification
        if difficulty_comparison.classification != expected_difficulty_direction:
            failures.append("difficulty_change_direction_not_guaranteed")

    metrics["required_operation_count"] = len(op_kinds)
    metrics["expected_reasoning_steps"] = spec.expected_reasoning_steps
    metrics["has_self_cancelling_pair"] = has_self_cancelling_pair
    metrics["task_family_known"] = family is not None

    hard = [c for c in failures if c in HARD_FAILURE_CATEGORIES]
    repair_seed = ",".join(failures) if failures else None
    return TaskCoherenceResult(
        passed=not failures, failure_categories=failures,
        hard_failure_categories=hard, deterministic_metrics=metrics,
        repair_seed=repair_seed)
