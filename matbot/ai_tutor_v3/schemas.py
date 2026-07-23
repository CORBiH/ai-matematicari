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
