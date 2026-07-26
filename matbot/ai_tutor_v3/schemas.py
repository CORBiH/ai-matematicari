# -*- coding: utf-8 -*-
"""V3 Schema Foundation A — the typed contracts the whole architecture stands on.

Every model here exists to make one of six boundaries a compile-time/validation-
time fact instead of a convention someone can quietly erode:

  1. GPT interprets language but never decides the authoritative verdict —
     ``StudentTurnInterpretation`` cannot carry a verdict, a state patch, or any
     counter delta; ``extra="forbid"`` makes smuggling one in a ``ValidationError``,
     not a silent no-op.
  2. Mathematical verification uses typed requests, never arbitrary code —
     ``VerificationRequest`` is a closed, discriminated union of three data
     contracts. There is no ``code`` field and nothing here is ever executed.
  3. Narration writes language but cannot mutate state — ``NarrationResult`` has
     no verdict, state-patch or counter field to smuggle a decision through.
  4. Student identity uses an opaque ID, not an email — ``StudentIdentityReference``
     has no ``student_email``/``parent_email``/credential field, and rejects them
     if anyone tries to pass one.
  5. Prompt policy versions are recorded explicitly — ``PromptPolicyReference``
     requires every policy layer's version, with no actual prompt text.
  6. Reporting and session schemas are a later stage. Nothing here name-checks a
     database, Airtable, Make.com, or email delivery.

Deliberately NOT here yet (later stages, per the approved architecture):
OpenAI calls, prompt text, verifier logic, reducer logic, SQLite, the full
LessonBlueprint, TutorSessionState, Airtable events, weekly reports, audit log
models, feature flags, routing, or frontend integration.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

# --------------------------------------------------------------------------- #
# Strict base model                                                            #
# --------------------------------------------------------------------------- #
#: A short, non-empty string. Rejects empty/omitted values outright rather than
#: silently trimming or defaulting them — this module corrects nothing, it only
#: accepts or refuses.
NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]


class V3StrictModel(BaseModel):
    """Shared base for every V3 schema.

    ``extra="forbid"``   unknown fields are a ``ValidationError``, not silently
                          dropped — this is also what makes Pydantic emit
                          ``"additionalProperties": false`` in the exported JSON
                          Schema, which OpenAI Structured Outputs requires.
    ``strict=True``       no implicit coercion of one type into another — a JSON
                          string like ``"6"`` is never silently accepted where an
                          ``int`` is declared. (Int → float widening is still
                          allowed by Pydantic even in strict mode, since it is
                          lossless; that is intentional, not a gap.)

    NOTE — ``strict=True`` also means a plain ``enum.Enum`` field requires an
    actual enum *instance*, not its string value, when constructed from Python
    (``Model(x="foo")`` fails; only ``Model.model_validate_json(...)`` accepts
    the bare string, since JSON has no other way to spell an enum). That
    friction is why every closed vocabulary below (``TutorMode``, ``TurnKind``,
    ``Verdict``, …) is a ``Literal[...]`` type alias, not an ``Enum`` subclass —
    ``Literal`` fields match plain strings directly under strict mode without
    losing the exact-value constraint, and export to JSON Schema identically.

    NOT yet attempted here: forcing every field into OpenAI's stricter
    "all properties required, optionality expressed as a nullable union" shape.
    Optional fields below use idiomatic Pydantic (``Optional[X] = None``, absent
    from ``required``). Reshaping the exported schema for OpenAI's strict
    Structured Outputs contract specifically is later-stage work — see
    ``export_json_schema`` below.
    """

    model_config = ConfigDict(extra="forbid", strict=True)


# --------------------------------------------------------------------------- #
# Enums (as Literal type aliases — see V3StrictModel docstring for why)        #
# --------------------------------------------------------------------------- #
TutorMode = Literal["practice", "explain", "exam", "quick"]

TurnKind = Literal[
    "answer", "question", "comment", "help_request", "task_request",
    "solution_request", "difficulty_change", "confirmation", "off_topic",
    "ambiguous",
]

Certainty = Literal["certain", "uncertain"]

Precision = Literal["exact", "approximate", "unspecified"]

Verdict = Literal[
    "correct", "partial", "incorrect", "not_checkable",
    "needs_clarification", "no_attempt",
]

TaskStatus = Literal["active", "completed", "skipped", "revealed", "abandoned"]

#: Task statuses that count as the task being DONE with — used by
#: ``AuthoritativeOutcome`` to enforce "task_completed requires a terminal
#: status" without hardcoding the set in two places.
_TERMINAL_TASK_STATUSES = frozenset({"completed", "skipped", "revealed", "abandoned"})

#: A bounded, policy-neutral streak TRANSITION — not a delta. A delta cannot
#: express "reset an existing streak" without already knowing its current
#: value (session state this schema does not and should not carry), so the
#: schema names the instruction instead of computing the result:
#:   preserve   leave the current streak unchanged
#:   increment  increment it, by however much reducer policy decides
#:   reset      set it to zero
#: The schema deliberately does not compute the resulting streak value — that
#: is the (not-yet-built) reducer's job.
StreakAction = Literal["preserve", "increment", "reset"]

#: Verdicts that must never move the student's progress: no attempt was made,
#: or the turn wasn't gradeable at all. Shared by both halves of section 2 so
#: the two rule sets cannot drift apart.
_NO_PROGRESS_VERDICTS = frozenset({"no_attempt", "needs_clarification"})

#: Shared by every ``VerificationRequest`` variant's discriminator and by
#: ``VerifiedFact.verification_type``, so the two can never drift apart.
VerificationType = Literal["rational_equality", "divisibility", "equation_substitution"]

#: A small, CLOSED vocabulary of atomic student operations — deliberately not
#: free text. A deterministic coherence check (``task_coherence.py``) can only
#: reason about "does this task ask the student to expand a fraction and then
#: reduce it right back with no stated purpose" if "expand"/"reduce" are typed
#: values from a fixed set, never an arbitrary model-written phrase. Generic
#: across every grade 6-9 lesson/domain — nothing here names a specific topic.
StudentOperationKind = Literal[
    "identify", "expand", "reduce", "simplify", "compare", "add", "subtract",
    "multiply", "divide", "substitute", "solve_for_unknown", "convert_unit",
    "estimate", "verify", "apply_theorem", "construct", "classify", "other",
]

#: Ordinal, per-dimension difficulty vocabularies — see ``DifficultySignature``.
RepresentationComplexity = Literal["single", "mixed", "abstract"]
DistractorSimilarity = Literal["low", "medium", "high"]
VerbalComplexity = Literal["short", "medium", "long"]


# --------------------------------------------------------------------------- #
# Lesson context                                                               #
# --------------------------------------------------------------------------- #
class LessonContext(V3StrictModel):
    """Which lesson, for which grade, in which mode — nothing pedagogical yet."""

    schema_version: NonEmptyStr
    grade: Literal[6, 7, 8, 9]
    area_id: NonEmptyStr
    area_title: NonEmptyStr
    lesson_id: NonEmptyStr
    lesson_title: NonEmptyStr
    mode: TutorMode
    language: Literal["bs-Latn"]


# --------------------------------------------------------------------------- #
# Student identity                                                            #
# --------------------------------------------------------------------------- #
class StudentIdentityReference(V3StrictModel):
    """An opaque handle for a student — deliberately NOT a PII record.

    No ``student_email``, no ``parent_email``, no Airtable/Make credential field
    exists to accept. ``extra="forbid"`` means a caller that tries to pass one
    gets a ``ValidationError``, not a silently-dropped field.
    """

    student_id: NonEmptyStr
    external_identity_provider: Optional[NonEmptyStr] = None
    external_identity_hash: Optional[NonEmptyStr] = None
    reporting_enabled: bool
    created_at: datetime


# --------------------------------------------------------------------------- #
# Prompt policy reference                                                      #
# --------------------------------------------------------------------------- #
class PromptPolicyReference(V3StrictModel):
    """Which policy-layer VERSIONS were used for a turn — not the prompt text
    itself. That text is a later stage."""

    constitution_version: NonEmptyStr
    bosnian_language_policy_version: NonEmptyStr
    math_notation_policy_version: NonEmptyStr
    grade_policy_version: NonEmptyStr
    mode_policy_version: NonEmptyStr
    lesson_blueprint_version: NonEmptyStr


# --------------------------------------------------------------------------- #
# Verification requests — typed data contracts, never executed                #
# --------------------------------------------------------------------------- #
class RationalEqualityVerificationRequest(V3StrictModel):
    """Is ``claimed`` equal to ``expected``? Values stay strings — no float
    parsing here, no silent precision loss. This is a request to verify, not a
    computed result."""

    type: Literal["rational_equality"]
    expected: NonEmptyStr
    claimed: NonEmptyStr
    require_reduced_form: bool = False


class DivisibilityVerificationRequest(V3StrictModel):
    """Does ``divisor`` divide ``number``, matching ``claimed_result``?"""

    type: Literal["divisibility"]
    number: int
    divisor: int
    claimed_result: bool

    @field_validator("divisor")
    @classmethod
    def _divisor_not_zero(cls, value: int) -> int:
        if value == 0:
            raise ValueError("divisor must not be zero")
        return value


class EquationSubstitutionVerificationRequest(V3StrictModel):
    """Does substituting ``claimed_value`` for ``variable`` make
    ``left_expression`` equal ``right_expression``?"""

    type: Literal["equation_substitution"]
    left_expression: NonEmptyStr
    right_expression: NonEmptyStr
    variable: NonEmptyStr
    claimed_value: NonEmptyStr


#: The closed set of verification requests. Adding ``expression_equivalence`` or
#: any other family is explicitly a later stage, not this one.
VerificationRequest = Annotated[
    Union[
        RationalEqualityVerificationRequest,
        DivisibilityVerificationRequest,
        EquationSubstitutionVerificationRequest,
    ],
    Field(discriminator="type"),
]


# --------------------------------------------------------------------------- #
# Student turn interpretation — GPT's output, NEVER a verdict                  #
# --------------------------------------------------------------------------- #
class TypedClaim(V3StrictModel):
    """One thing the model believes the student asserted — a claim to be
    VERIFIED later, not a graded fact."""

    claim_id: NonEmptyStr
    claim_kind: NonEmptyStr
    claimed_value: Optional[str] = None
    verification_request: Optional[VerificationRequest] = None
    stated_reasoning_components: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class StudentTurnInterpretation(V3StrictModel):
    """What the model understood the student to mean.

    Deliberately absent from this model's fields, and therefore rejected by
    ``extra="forbid"`` if anyone tries to smuggle one in: ``verdict``,
    ``correct``, ``incorrect``, ``state_patch``, ``attempt_delta``,
    ``wrong_attempt_delta``, ``streak_action``, ``solved_count_delta``, task
    completion, mastery mutation, coverage mutation. Interpretation is not
    grading — that is ``AuthoritativeOutcome``'s job, computed server-side from
    ``VerifiedFact``s, never from this model.
    """

    schema_version: NonEmptyStr
    turn_kind: TurnKind
    is_answer_attempt: bool
    normalized_meaning: NonEmptyStr
    claims: list[TypedClaim] = Field(default_factory=list)
    certainty: Certainty
    precision: Precision
    visible_misconception_codes: list[str] = Field(default_factory=list)
    requested_action: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    clarification_question: Optional[str] = None


# --------------------------------------------------------------------------- #
# Verified fact — the verifier's typed, deterministic answer                   #
# --------------------------------------------------------------------------- #
class VerifiedFact(V3StrictModel):
    """A verifier's answer to one ``VerificationRequest``. No hidden reasoning
    field — ``safe_detail`` is a short, student-safe note, not a scratchpad."""

    claim_id: NonEmptyStr
    verification_type: VerificationType
    verified: bool
    matches_claim: bool
    canonical_result: Optional[str] = None
    verifier_method: NonEmptyStr
    error_code: Optional[str] = None
    safe_detail: Optional[str] = None


# --------------------------------------------------------------------------- #
# Authoritative outcome — server-computed, never a model output               #
# --------------------------------------------------------------------------- #
class AuthoritativeOutcome(V3StrictModel):
    """The one authoritative grading decision for a turn.

    This model is SERVER-CREATED from ``VerifiedFact``s by the (not-yet-built)
    reducer — it is never what GPT returns, and ``StudentTurnInterpretation``
    cannot express any of these fields. Deliberately encodes no lesson-specific
    grading rule — only the cross-field invariants that must hold for ANY
    lesson, any skill.
    """

    schema_version: NonEmptyStr
    verdict: Verdict
    attempt_count_delta: int = Field(ge=0)
    wrong_attempt_count_delta: int = Field(ge=0)
    streak_action: StreakAction
    solved_count_delta: int = Field(ge=0)
    task_status_after: TaskStatus
    task_completed: bool
    preserve_active_task: bool
    verified_facts: list[VerifiedFact] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    needs_narration: bool
    clarification_prompt_seed: Optional[NonEmptyStr] = None

    @model_validator(mode="after")
    def _cross_field_invariants(self) -> "AuthoritativeOutcome":
        # verdict == "needs_clarification" is the ONLY authoritative signal that
        # a turn needs clarification. There used to also be a separate
        # ``needs_clarification: bool`` field, which meant the model could say
        # verdict="not_checkable" while needs_clarification=True (or the
        # reverse) — two sources of truth that could disagree. Removed; do not
        # reintroduce a second boolean or a duplicate status field for this.
        if self.verdict == "needs_clarification":
            if not self.clarification_prompt_seed:
                raise ValueError(
                    "needs_clarification requires a non-empty clarification_prompt_seed"
                )
        elif self.clarification_prompt_seed is not None:
            raise ValueError(
                f"clarification_prompt_seed must be null for verdict={self.verdict!r} "
                "(only needs_clarification carries one)"
            )

        # Section 2: no_attempt and needs_clarification must never punish the
        # student or mutate progress — zero counters, an unchanged streak, the
        # task neither completed nor abandoned. no_attempt does NOT require a
        # clarification_prompt_seed (checked above, keyed on verdict alone).
        if self.verdict in _NO_PROGRESS_VERDICTS:
            if self.attempt_count_delta != 0:
                raise ValueError(f"{self.verdict} cannot increment attempt_count_delta")
            if self.wrong_attempt_count_delta != 0:
                raise ValueError(f"{self.verdict} cannot increment wrong_attempt_count_delta")
            if self.solved_count_delta != 0:
                raise ValueError(f"{self.verdict} cannot increment solved_count_delta")
            if self.streak_action != "preserve":
                raise ValueError(
                    f"{self.verdict} must preserve the streak "
                    f"(streak_action={self.streak_action!r})"
                )
            if self.task_completed:
                raise ValueError(f"{self.verdict} cannot complete the task")
            if not self.preserve_active_task:
                raise ValueError(f"{self.verdict} must preserve the active task")

        # Section 3: correct/incorrect invariants. Only what must hold for ANY
        # lesson — which counters, never how much or which streak resolution.
        if self.verdict == "correct":
            if self.wrong_attempt_count_delta != 0:
                raise ValueError("correct cannot increment wrong_attempt_count_delta")
            if self.streak_action == "reset":
                raise ValueError("correct cannot reset the streak")
        if self.verdict == "incorrect" and self.solved_count_delta != 0:
            raise ValueError("incorrect cannot increment solved_count_delta")
        # incorrect: streak_action may be "reset" or "preserve" — which one is
        # reducer policy, not a schema-level rule.
        # partial: no counter or streak policy encoded here at all — later
        # reducer policy decides, per the approved architecture.

        if self.task_completed and self.task_status_after not in _TERMINAL_TASK_STATUSES:
            raise ValueError(
                "task_completed requires a terminal task_status_after "
                f"(one of {sorted(_TERMINAL_TASK_STATUSES)}), "
                f"got {self.task_status_after!r}"
            )
        if self.task_status_after == "active" and self.task_completed:
            # Logically implied by the check above given today's TaskStatus
            # values (their complement is exactly {"active"}), but stated
            # explicitly per spec so it keeps holding if TaskStatus ever grows
            # another non-terminal, non-active value.
            raise ValueError("task_status_after == active means task_completed must be false")
        return self


# --------------------------------------------------------------------------- #
# Narration — language only, never state                                      #
# --------------------------------------------------------------------------- #
class NarrationResult(V3StrictModel):
    """The Bosnian text shown to the student. Writes language, nothing else —
    no verdict, no state patch, no counters, no mastery or task-state change
    field exists here for ``extra="forbid"`` to have to reject."""

    schema_version: NonEmptyStr
    student_text: NonEmptyStr
    response_category: NonEmptyStr
    latex_fragments: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


# ═══════════════════════════════════════════════════════════════════════════ #
# FUNCTIONAL PRACTICE CONTRACTS                                                 #
#                                                                               #
# Everything below is the vertical slice's runtime data. The five authority     #
# layers stay strictly separated by TYPE, so no layer can express another's     #
# decision:                                                                     #
#   1. model interpretation        StudentTurnInterpretation (above)            #
#   2. provisional model assessment PracticeModelAssessment  — a PROPOSAL       #
#   3. authoritative outcome        AuthoritativeOutcome (above) — server only  #
#   4. student-facing narration     NarrationResult (above)                     #
#   5. durable server state         TutorSessionState                           #
# ═══════════════════════════════════════════════════════════════════════════ #

VerificationStatus = Literal["model_only", "deterministic", "unavailable"]

#: Closed set of pedagogical actions the model may PROPOSE. The reducer decides
#: which (if any) is actually applied — this is a suggestion, never a command.
PedagogicalAction = Literal[
    "continue", "give_hint", "repeat_concept", "easier", "harder", "advance",
    "reveal_solution", "answer_concept_question", "new_task", "clarify",
    "off_topic", "confirm_answer",
]

DifficultySuggestion = Literal["easier", "harder", "same"]

#: What a task expects, at the level THIS stage can express. No deterministic
#: verifier exists yet, so ``planned_verification_type`` only RESERVES which
#: typed VerificationRequest family a later stage will build for this task.
AnswerKind = Literal[
    "boolean_with_reason", "rational_value", "integer_value",
    "equation_solution", "set_value", "free_text",
]


# --------------------------------------------------------------------------- #
# Lesson identity + blueprint                                                  #
# --------------------------------------------------------------------------- #
class LessonIdentity(V3StrictModel):
    """Canonical curriculum identity — the resolver's output, curriculum-sourced,
    never invented by the model."""

    grade: Literal[6, 7, 8, 9]
    area_id: NonEmptyStr
    area_title: NonEmptyStr
    lesson_id: NonEmptyStr
    lesson_title: NonEmptyStr
    language: Literal["bs-Latn"] = "bs-Latn"


class ConceptBlueprint(V3StrictModel):
    concept_id: NonEmptyStr
    name: NonEmptyStr
    importance: Literal["core", "supporting", "enrichment"] = "core"
    prerequisites: list[str] = Field(default_factory=list)
    example_task_types: list[str] = Field(default_factory=list)
    evidence_of_understanding: list[str] = Field(default_factory=list)
    typical_errors: list[str] = Field(default_factory=list)
    progression_criteria: NonEmptyStr


class CoverageTarget(V3StrictModel):
    target_id: NonEmptyStr
    name: NonEmptyStr
    concept_id: Optional[str] = None


class KeyRule(V3StrictModel):
    rule_id: NonEmptyStr
    statement: NonEmptyStr


class CommonMisconception(V3StrictModel):
    code: NonEmptyStr
    description: NonEmptyStr


# One atomic step, from the closed StudentOperationKind vocabulary — never
# free text. Nested inside TaskSpecification (see the comment above it).
class RequiredOperation(V3StrictModel):
    """One required student operation. ``detail`` is an optional short label,
    never a full sentence."""

    kind: StudentOperationKind
    detail: Optional[str] = None


class TaskFamily(V3StrictModel):
    """A reusable task shape declared ONCE per lesson Blueprint. The new fields
    below are OPTIONAL with safe defaults so an already-stored Blueprint
    (generated before this pass) still validates unchanged — see
    ``task_coherence.py`` module docstring for the compatibility policy this
    relies on."""

    family_id: NonEmptyStr
    description: NonEmptyStr
    answer_kind: AnswerKind
    #: Why tasks in this family exist pedagogically (e.g. "uporediti razlomke
    #: svođenjem na zajednički nazivnik") — never shown to the student.
    pedagogical_goal: Optional[str] = None
    #: The operations tasks in this family typically require.
    typical_required_operations: list[RequiredOperation] = Field(default_factory=list)
    #: If this family is about comparing two values or showing an invariant
    #: (e.g. expanding a fraction preserves its value), name that purpose here.
    #: Required by ``task_coherence`` whenever a generated task in this family
    #: pairs two self-cancelling operations (e.g. expand + reduce) — see
    #: ``_SELF_CANCELLING_PAIRS`` in task_coherence.py.
    comparison_or_invariance_goal: Optional[str] = None
    typical_difficulty_min: Optional[int] = Field(default=None, ge=1, le=5)
    typical_difficulty_max: Optional[int] = Field(default=None, ge=1, le=5)


class DifficultyDimension(V3StrictModel):
    dimension_id: NonEmptyStr
    description: NonEmptyStr


# Ten independently-orderable dimensions (of the larger candidate set
# considered), self-declared by the model without lesson-specific knowledge —
# see task_coherence.compare_difficulty for how these combine into an
# easier/harder/same/ambiguous verdict. Nested inside TaskSpecification, so
# this docstring is deliberately short (see the comment above
# TaskSpecification for why it matters here too).
class DifficultySignature(V3StrictModel):
    """A typed, per-task difficulty profile. Never shown to the student."""

    numeric_magnitude: int = Field(ge=1, le=5)
    operation_count: int = Field(ge=1, le=10)
    reasoning_steps: int = Field(ge=1, le=10)
    concept_count: int = Field(ge=1, le=5)
    representation_complexity: RepresentationComplexity = "single"
    distractor_similarity: DistractorSimilarity = "low"
    verbal_complexity: VerbalComplexity = "short"
    requires_multi_step_justification: bool = False
    nonstandard_form: bool = False
    requires_recall_only: bool = False


class HintStrategyStep(V3StrictModel):
    level: int = Field(ge=1)
    guidance: NonEmptyStr


class MasteryRequirement(V3StrictModel):
    min_concepts_mastered: int = Field(ge=0)
    min_independent_solves: int = Field(ge=0)


class LanguageGuidance(V3StrictModel):
    language_register: NonEmptyStr
    avoid: list[str] = Field(default_factory=list)
    preferred_terms: list[str] = Field(default_factory=list)


class LessonBlueprint(V3StrictModel):
    """The instructional essence of one lesson, generated once by the model and
    reused across sessions. The lesson TITLE is instructional data — its
    coverage targets must survive into ``coverage_targets`` (validated, not
    trusted: see ``lesson_blueprint.validate_blueprint``)."""

    schema_version: NonEmptyStr
    blueprint_id: NonEmptyStr
    blueprint_version: NonEmptyStr
    lesson_identity: LessonIdentity
    source_hash: NonEmptyStr
    source_metadata: dict[str, str] = Field(default_factory=dict)
    learning_objectives: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    concepts: list[ConceptBlueprint] = Field(default_factory=list)
    coverage_targets: list[CoverageTarget] = Field(default_factory=list)
    key_rules: list[KeyRule] = Field(default_factory=list)
    allowed_methods: list[str] = Field(default_factory=list)
    common_misconceptions: list[CommonMisconception] = Field(default_factory=list)
    task_families: list[TaskFamily] = Field(default_factory=list)
    difficulty_dimensions: list[DifficultyDimension] = Field(default_factory=list)
    hint_strategy: list[HintStrategyStep] = Field(default_factory=list)
    mastery_requirements: MasteryRequirement
    language_guidance: LanguageGuidance
    supported_verification_types: list[VerificationType] = Field(default_factory=list)
    generation_confidence: float = Field(ge=0.0, le=1.0)
    validation_status: Literal["draft", "needs_review", "validated"] = "draft"
    model: NonEmptyStr
    prompt_policy: "PromptPolicyReference"
    created_at: datetime


class LessonBlueprintProposal(V3StrictModel):
    """What the model is ALLOWED to propose for a Lesson Blueprint — pedagogical
    content only. This is the schema actually sent to OpenAI for
    ``blueprint_generation``; ``LessonBlueprint`` itself never is.

    Deliberately excludes every server-owned ``LessonBlueprint`` field:
    ``schema_version``, ``blueprint_id``, ``blueprint_version``,
    ``lesson_identity``, ``source_hash``, ``source_metadata``, ``model``,
    ``prompt_policy``, ``created_at``, ``validation_status``. The model must
    never be asked to invent authoritative server metadata — the server
    injects all of the above afterward (see
    ``lesson_blueprint._new_blueprint_from_model``). This also sidesteps
    ``source_metadata``'s free-form ``dict[str, str]`` shape, which an
    arbitrary-key map is never asked of the model in the first place.
    """

    learning_objectives: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    concepts: list[ConceptBlueprint] = Field(default_factory=list)
    coverage_targets: list[CoverageTarget] = Field(default_factory=list)
    key_rules: list[KeyRule] = Field(default_factory=list)
    allowed_methods: list[str] = Field(default_factory=list)
    common_misconceptions: list[CommonMisconception] = Field(default_factory=list)
    task_families: list[TaskFamily] = Field(default_factory=list)
    difficulty_dimensions: list[DifficultyDimension] = Field(default_factory=list)
    hint_strategy: list[HintStrategyStep] = Field(default_factory=list)
    mastery_requirements: MasteryRequirement
    language_guidance: LanguageGuidance
    supported_verification_types: list[VerificationType] = Field(default_factory=list)
    generation_confidence: float = Field(ge=0.0, le=1.0)


# --------------------------------------------------------------------------- #
# Task + provisional model assessment                                         #
# --------------------------------------------------------------------------- #
# Deliberately a short docstring, not a long rationale: Pydantic embeds a
# model's docstring verbatim into the exported JSON Schema "description" —
# sent to OpenAI on EVERY task_generation AND task_coherence_repair call (see
# ActiveTaskTurnDecision's comment above for how this was confirmed). The
# coherence/difficulty fields below are all OPTIONAL (default None/empty) so
# a caller or fixture that predates this pass keeps validating — see
# task_coherence.py for how a missing field degrades the gate rather than
# crashing it.
class TaskSpecification(V3StrictModel):
    """A task the model PROPOSES; the server validates it against the
    blueprint before activating it."""

    concept_id: NonEmptyStr
    target_id: NonEmptyStr
    question: NonEmptyStr
    answer_kind: AnswerKind
    expected_internal: Optional[str] = None
    difficulty_level: int = Field(ge=1, le=5)
    planned_verification_type: Optional[VerificationType] = None
    rationale: Optional[str] = None
    #: Which Blueprint ``TaskFamily`` this task belongs to, if any.
    task_family_id: Optional[str] = None
    pedagogical_goal: Optional[str] = None
    required_student_operations: list[RequiredOperation] = Field(default_factory=list)
    comparison_or_invariance_goal: Optional[str] = None
    #: A one-sentence, model-authored justification for why this task's
    #: operation sequence is pedagogically coherent — e.g. why an
    #: expand-then-reduce pair is not pointless. Never shown to the student.
    coherence_claim: Optional[str] = None
    expected_reasoning_steps: int = Field(default=1, ge=1, le=10)
    difficulty_signature: Optional["DifficultySignature"] = None


class ActiveTask(V3StrictModel):
    """The single live task. Server-owned counters live here, never on any model
    output."""

    task_id: NonEmptyStr
    concept_id: NonEmptyStr
    target_id: NonEmptyStr
    question: NonEmptyStr
    answer_kind: AnswerKind
    expected_internal: Optional[str] = None
    difficulty_level: int = Field(ge=1, le=5)
    planned_verification_type: Optional[VerificationType] = None
    attempts: int = Field(default=0, ge=0)
    wrong_attempts: int = Field(default=0, ge=0)
    hints_given: int = Field(default=0, ge=0)
    solution_revealed: bool = False
    #: Carried forward from the ``TaskSpecification`` that created this task so
    #: a LATER difficulty-change turn can compare the new task's signature
    #: against this one (``task_coherence.compare_difficulty``).
    task_family_id: Optional[str] = None
    difficulty_signature: Optional["DifficultySignature"] = None


class PracticeModelAssessment(V3StrictModel):
    """The model's PROVISIONAL grading proposal — never authoritative.

    The reducer reads this as a suggestion and decides the real
    ``AuthoritativeOutcome``. Deliberately cannot express a counter delta, a
    streak result, or a task-status change: it proposes, the server disposes.
    """

    schema_version: NonEmptyStr
    is_answer_attempt: bool
    proposed_verdict: Verdict
    proposed_pedagogical_action: PedagogicalAction
    misconception_codes: list[str] = Field(default_factory=list)
    suggested_next_concept: Optional[str] = None
    difficulty_suggestion: Optional[DifficultySuggestion] = None
    solved_independently: bool = False
    used_assistance: bool = False
    confidence: float = Field(ge=0.0, le=1.0)


class PracticeTurnInterpretation(V3StrictModel):
    """The combined structured output of the interpretation call: the language
    interpretation PLUS an optional provisional assessment PLUS an optional
    proposed narration. Three typed parts in one call, but the authority
    boundary holds — none of them can carry a verdict the reducer must
    honour. ``narration_proposal`` is ONLY a proposed student-facing text for
    an answer turn's feedback — the reducer's authoritative verdict (computed
    from ``AuthoritativeOutcome``, never from this proposal) decides what
    actually happened; the dispatcher may use this text as-is (through the
    same quality gate every other narration goes through) instead of making a
    SEPARATE narration call, which is the whole point of proposing it here."""

    interpretation: StudentTurnInterpretation
    assessment: Optional[PracticeModelAssessment] = None
    narration_proposal: Optional[NarrationResult] = None


# --------------------------------------------------------------------------- #
# Compact active-task turn context + decision (latency pass)                  #
#                                                                               #
# ``PracticeTurnInterpretation`` (above) stays the schema used when there is   #
# NO active task yet (bootstrap-adjacent/rare path) — that case genuinely      #
# needs the fuller context. For the DOMINANT case — a free-form student        #
# message while a Practice task is already active — these two compact models  #
# replace it: a typed, minimal INPUT context (never sent as a schema; it is   #
# serialized into the user message text) and a typed, minimal OUTPUT schema.  #
# --------------------------------------------------------------------------- #
class ActiveTaskTurnContext(V3StrictModel):
    """Only what the model needs to interpret and provisionally assess ONE
    message while a task is already active — never the full durable session,
    never the full LessonBlueprint. Built by
    ``orchestrator.build_active_task_context`` from server-side data the
    student never sees directly (blueprint + session state), so it carries no
    new privacy surface: no raw parent/student contact data, no full
    conversation history (only the last couple of turns), no reporting/outbox
    data, no unrelated concepts, no coverage/mastery/completed-task history."""

    grade: Literal[6, 7, 8, 9]
    lesson_id: NonEmptyStr
    lesson_title: NonEmptyStr
    concept_id: NonEmptyStr
    target_id: NonEmptyStr
    task_id: NonEmptyStr
    task_text: NonEmptyStr
    answer_kind: AnswerKind
    expected_internal: Optional[str] = None
    planned_verification_type: Optional[VerificationType] = None
    key_rules: list[str] = Field(default_factory=list)
    allowed_methods: list[str] = Field(default_factory=list)
    relevant_misconceptions: list[str] = Field(default_factory=list)
    hint_level: int = Field(ge=0)
    solution_revealed: bool
    difficulty_level: int = Field(ge=1, le=5)
    recent_turns: list[str] = Field(default_factory=list)
    student_message: NonEmptyStr


# A SMALLER, purpose-specific structured output for interpreting one turn
# while a Practice task is already active. Contains only the fields the
# reducer/dispatcher actually consume for this path (verified by direct
# inspection of reducer.py/dispatcher.py, not guessed) plus one narration
# proposal — never an authoritative counter, streak, mastery, coverage
# value, a model-chosen task ID, a state patch, a session version, or a
# completion mutation. The reducer computes every one of those from ITS OWN
# state; this model only proposes turn meaning, a provisional verdict, and
# wording.
#
# Deliberately a plain code comment, NOT a class docstring: Pydantic embeds
# a model's docstring verbatim as the exported JSON Schema's "description"
# — sent to OpenAI on EVERY active-task interpretation call (confirmed by
# inspecting ``export_json_schema(ActiveTaskTurnDecision)``'s actual output).
# A one-line docstring keeps that recurring cost small while this comment
# still documents the design for future readers.
class ActiveTaskTurnDecision(V3StrictModel):
    """Compact, non-authoritative interpretation of one active-task turn."""

    schema_version: NonEmptyStr
    turn_kind: TurnKind
    is_answer_attempt: bool
    confidence: float = Field(ge=0.0, le=1.0)
    clarification_question: Optional[str] = None
    requested_action: Optional[str] = None
    proposed_verdict: Optional[Verdict] = None
    difficulty_suggestion: Optional[DifficultySuggestion] = None
    issue_summary: Optional[str] = None
    narration_proposal: Optional[NarrationResult] = None


# --------------------------------------------------------------------------- #
# Practice conversation engine — compact context + ONE unified turn proposal  #
#                                                                              #
# Replaces, for the simplified conversational Practice engine ONLY (never    #
# the legacy engine, never Explain/Exam/Quick/Minimal): the combined         #
# interpretation + task-generation + narration + task-repair contracts above #
# with ONE compact input context and ONE compact strict output schema, so a  #
# normal turn needs exactly one Responses API call. The five authority       #
# layers still hold: this schema cannot express a counter delta, a streak    #
# result, a mastery/coverage mutation, a session version, a model-chosen     #
# task/DB id, or a completion flag — the (unchanged) reducer computes every  #
# one of those from ITS OWN state via the adapter in orchestrator.py         #
# (``conversation_proposal_to_interpretation_and_assessment``).              #
# --------------------------------------------------------------------------- #
class RecentTaskSummary(V3StrictModel):
    """A COMPACT record of one recent task — never the full ``ActiveTask``,
    never raw model output history."""

    displayed_task: NonEmptyStr
    difficulty_level: int = Field(ge=1, le=5)
    answer_kind: AnswerKind
    outcome: Verdict
    concept_summary: NonEmptyStr


class PerformanceSummary(V3StrictModel):
    """A compact rollup — never the full session counters/mastery/coverage."""

    recent_correct: int = Field(default=0, ge=0)
    recent_incorrect: int = Field(default=0, ge=0)
    recent_partial: int = Field(default=0, ge=0)
    correct_streak: int = Field(default=0, ge=0)


#: Recent-window size shared by both list fields below and by the dispatcher
#: code that BUILDS this context — a single source of truth for "2-3".
PRACTICE_CONVERSATION_RECENT_WINDOW = 3


class PracticeConversationContext(V3StrictModel):
    """Only what the model needs for ONE conversational Practice turn.

    Never sent to OpenAI as a schema — serialized into the user message text
    (mirrors ``ActiveTaskTurnContext``). Deliberately excludes: full session
    history, full Blueprint, full mastery/coverage trees, complete
    completed-task history, Sheets/outbox data, reporting metadata, every
    misconception, every task family, old rejected model outputs.
    """

    schema_version: NonEmptyStr
    grade: Literal[6, 7, 8, 9]
    lesson_id: NonEmptyStr
    lesson_title: NonEmptyStr
    short_lesson_description: Optional[str] = None
    current_task: Optional[str] = None
    current_task_expected_answer_summary: Optional[str] = None
    current_task_answer_kind: Optional[AnswerKind] = None
    current_task_requires_explanation: bool = False
    current_hint_level: int = Field(default=0, ge=0)
    current_difficulty: int = Field(default=2, ge=1, le=5)
    recent_tasks: list[RecentTaskSummary] = Field(default_factory=list)
    recent_turns: list[str] = Field(default_factory=list)
    performance_summary: PerformanceSummary = Field(default_factory=PerformanceSummary)
    current_student_message: NonEmptyStr
    trusted_ui_action: Optional[str] = None

    @field_validator("recent_tasks")
    @classmethod
    def _bounded_recent_tasks(cls, value: list) -> list:
        if len(value) > PRACTICE_CONVERSATION_RECENT_WINDOW:
            raise ValueError(
                f"recent_tasks must have at most {PRACTICE_CONVERSATION_RECENT_WINDOW} "
                f"entries, got {len(value)}")
        return value

    @field_validator("recent_turns")
    @classmethod
    def _bounded_recent_turns(cls, value: list) -> list:
        if len(value) > PRACTICE_CONVERSATION_RECENT_WINDOW:
            raise ValueError(
                f"recent_turns must have at most {PRACTICE_CONVERSATION_RECENT_WINDOW} "
                f"entries, got {len(value)}")
        return value


#: Closed action vocabulary for the unified proposal — see the docstring
#: above ``PracticeConversationTurnProposal`` for the per-action field
#: requirements this schema's validator enforces.
ConversationAction = Literal[
    "assess_answer", "answer_question", "give_hint", "give_solution",
    "create_initial_task", "create_new_task", "create_easier_task",
    "create_harder_task", "ask_clarification", "continue_conversation",
]

#: A model self-assessment of an answer attempt — NOT the authoritative
#: verdict (that stays ``Verdict``/``AuthoritativeOutcome``, server-only).
#: "not_an_answer"/"not_applicable" let the model say "I looked, this wasn't
#: actually an answer to grade" without forcing a wrong verdict.
ConversationAnswerAssessment = Literal[
    "correct", "partially_correct", "incorrect", "unclear",
    "not_an_answer", "not_applicable",
]

#: The task-creating actions — share the same "proposed_next_task required"
#: constraint below.
_TASK_CREATING_ACTIONS = frozenset({
    "create_initial_task", "create_new_task", "create_easier_task", "create_harder_task",
})


class ProposedTask(V3StrictModel):
    """A task the model proposes within the SAME unified call — the reducer
    still decides whether it is actually activated."""

    text: NonEmptyStr
    answer_kind: AnswerKind
    expected_answer_summary: Optional[str] = None
    requires_explanation: bool = False
    difficulty_level: int = Field(ge=1, le=5)
    #: Self-declared: true only when a self-cancelling-looking operation pair
    #: (e.g. "proširi pa skrati") has an explicit, stated purpose (comparison,
    #: invariance, reversibility, checking, or error analysis) — see
    #: ``conversation_dispatcher._looks_self_cancelling_without_purpose``.
    has_stated_purpose: bool = False


# Deliberately a short docstring (see the comment above ActiveTaskTurnDecision
# for why): this schema is sent on EVERY normal conversational Practice call.
class PracticeConversationTurnProposal(V3StrictModel):
    """One unified, non-authoritative proposal for one conversational
    Practice turn."""

    schema_version: NonEmptyStr
    action: ConversationAction
    understood_student_intent: NonEmptyStr
    assessment: Optional[ConversationAnswerAssessment] = None
    student_feedback: Optional[str] = None
    clarification_question: Optional[str] = None
    hint: Optional[str] = None
    explanation: Optional[str] = None
    proposed_next_task: Optional[ProposedTask] = None
    proposed_difficulty: Optional[DifficultySuggestion] = None
    keep_current_task: bool = True
    answer_evidence_summary: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _action_requires_matching_fields(self) -> "PracticeConversationTurnProposal":
        action = self.action
        if action == "assess_answer":
            if self.assessment is None or not self.student_feedback:
                raise ValueError(
                    "assess_answer requires both assessment and student_feedback")
        elif action == "answer_question":
            if not self.explanation:
                raise ValueError("answer_question requires explanation")
        elif action == "give_hint":
            if not self.hint:
                raise ValueError("give_hint requires hint")
        elif action == "give_solution":
            if not self.explanation:
                raise ValueError("give_solution requires explanation (the solution text)")
        elif action in _TASK_CREATING_ACTIONS:
            if self.proposed_next_task is None:
                raise ValueError(f"{action} requires proposed_next_task")
        elif action == "ask_clarification":
            if not self.clarification_question:
                raise ValueError("ask_clarification requires clarification_question")
        return self


# --------------------------------------------------------------------------- #
# Verification boundary (model-only in this stage)                            #
# --------------------------------------------------------------------------- #
class VerificationBatchResult(V3StrictModel):
    """Result of verifying a turn's claims. In this stage ``status`` is always
    ``model_only`` — there are no deterministic handlers yet, and this must
    never be labelled 'verified' or 'proven'."""

    status: VerificationStatus
    verified_facts: list[VerifiedFact] = Field(default_factory=list)
    note: Optional[str] = None


# --------------------------------------------------------------------------- #
# Durable session state (5)                                                    #
# --------------------------------------------------------------------------- #
class SessionCounters(V3StrictModel):
    attempts: int = Field(default=0, ge=0)
    wrong_attempts: int = Field(default=0, ge=0)
    solved_independent: int = Field(default=0, ge=0)
    solved_assisted: int = Field(default=0, ge=0)
    revealed: int = Field(default=0, ge=0)
    tasks_completed: int = Field(default=0, ge=0)
    correct_streak: int = Field(default=0, ge=0)


class CoverageState(V3StrictModel):
    targets: list[str] = Field(default_factory=list)
    covered: list[str] = Field(default_factory=list)
    attempts_per_target: dict[str, int] = Field(default_factory=dict)


class MasteryState(V3StrictModel):
    #: concept_id -> provisional mastery label; always provisional while
    #: verification is model_only.
    per_concept: dict[str, str] = Field(default_factory=dict)
    provisional: bool = True


class DifficultyState(V3StrictModel):
    level: int = Field(default=2, ge=1, le=5)


class HintState(V3StrictModel):
    current_level: int = Field(default=0, ge=0)
    max_level_reached: int = Field(default=0, ge=0)


class PendingClarification(V3StrictModel):
    prompt_seed: NonEmptyStr
    raised_at_turn: int = Field(ge=0)


class RecentTurn(V3StrictModel):
    turn_index: int = Field(ge=0)
    role: Literal["student", "tutor"]
    text: str
    turn_kind: Optional[str] = None
    verdict: Optional[str] = None


class StructuredConversationSummary(V3StrictModel):
    text: str
    as_of_turn: int = Field(ge=0)
    source: Literal["server_derived"] = "server_derived"


class CompletedTaskSummary(V3StrictModel):
    task_id: NonEmptyStr
    concept_id: NonEmptyStr
    target_id: NonEmptyStr
    verdict: Verdict
    independent: bool


class TutorSessionState(V3StrictModel):
    """The durable, server-owned truth for a Practice session. Privacy-safe:
    ``student_id`` is opaque, and there is NO parent/student email or credential
    field for one to be stored in."""

    schema_version: NonEmptyStr
    session_id: NonEmptyStr
    student_id: NonEmptyStr
    grade: Literal[6, 7, 8, 9]
    mode: Literal["practice"]
    lesson_id: NonEmptyStr
    blueprint_id: NonEmptyStr
    blueprint_version: NonEmptyStr
    prompt_policy: "PromptPolicyReference"
    turn_index: int = Field(default=0, ge=0)
    active_task: Optional[ActiveTask] = None
    counters: SessionCounters = Field(default_factory=SessionCounters)
    coverage: CoverageState = Field(default_factory=CoverageState)
    mastery: MasteryState = Field(default_factory=MasteryState)
    difficulty: DifficultyState = Field(default_factory=DifficultyState)
    hint: HintState = Field(default_factory=HintState)
    pending_clarification: Optional[PendingClarification] = None
    recent_turns: list[RecentTurn] = Field(default_factory=list)
    summary: Optional[StructuredConversationSummary] = None
    completed_tasks: list[CompletedTaskSummary] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- #
# Reporting / audit (privacy-safe; NO raw transcript)                          #
# --------------------------------------------------------------------------- #
ActivityEventType = Literal["image_upload", "sos", "test_completed", "session_end"]


class SessionLearningSummary(V3StrictModel):
    """Structured, transcript-free summary for an activity/reporting event."""

    lesson_id: NonEmptyStr
    concepts_practiced: list[str] = Field(default_factory=list)
    tasks_completed: int = Field(default=0, ge=0)
    solved_independent: int = Field(default=0, ge=0)
    solved_assisted: int = Field(default=0, ge=0)
    ai_note: Optional[str] = None


class StudentActivityEvent(V3StrictModel):
    """A privacy-safe activity-outbox record. Contains identity + structured
    summary ONLY — never a raw chat transcript, never an email."""

    event_id: NonEmptyStr
    event_type: ActivityEventType
    student_id: NonEmptyStr
    session_id: NonEmptyStr
    grade: Literal[6, 7, 8, 9]
    mode: Literal["practice"]
    lesson_id: NonEmptyStr
    created_at: datetime
    learning_summary: SessionLearningSummary
    ai_note: Optional[str] = None


class UsageMetrics(V3StrictModel):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    total_latency_ms: float = Field(default=0.0, ge=0.0)
    #: Per-call latency, same order as the audit record's ``call_purposes`` —
    #: Phase 9 telemetry ("latency per call"), privacy-safe (numbers only).
    per_call_latency_ms: list[float] = Field(default_factory=list)


class TurnAuditRecord(V3StrictModel):
    """Structured audit of one turn. No chain-of-thought, no raw prompts —
    ``interpretation_summary`` is a short restatement, not hidden reasoning."""

    schema_version: NonEmptyStr
    request_id: NonEmptyStr
    session_id: NonEmptyStr
    client_turn_id: NonEmptyStr
    lesson_id: NonEmptyStr
    mode: Literal["practice"]
    blueprint_id: str = ""
    blueprint_version: str = ""
    prompt_policy: "PromptPolicyReference"
    model: str = ""
    call_purposes: list[str] = Field(default_factory=list)
    verification_status: VerificationStatus = "model_only"
    turn_kind: Optional[str] = None
    interpretation_summary: Optional[str] = None
    proposed_verdict: Optional[str] = None
    applied_verdict: Optional[str] = None
    response_category: Optional[str] = None
    state_version_before: Optional[int] = None
    state_version_after: Optional[int] = None
    fallback_reason: Optional[str] = None
    schema_validation_ok: bool = True
    error_code: Optional[str] = None
    usage: UsageMetrics = Field(default_factory=UsageMetrics)


# Resolve forward references to PromptPolicyReference (defined earlier).
LessonBlueprint.model_rebuild()
TutorSessionState.model_rebuild()
TurnAuditRecord.model_rebuild()


# --------------------------------------------------------------------------- #
# Schema export                                                                #
# --------------------------------------------------------------------------- #
def export_json_schema(model: type[BaseModel]) -> dict:
    """Deterministic JSON Schema for a V3 strict model.

    ``model.model_json_schema()`` is already a pure function of the model's
    field definitions — the same model produces byte-identical output on every
    call, in this process or a fresh one. This wrapper is the single place that
    will host OpenAI Structured-Outputs-specific reshaping later (e.g. forcing
    every property into ``required`` with optionality expressed as a nullable
    union, which Pydantic's own export does not do — see ``V3StrictModel``).
    No such reshaping is added in this stage; today it returns exactly what
    Pydantic produces.
    """
    return model.model_json_schema()
