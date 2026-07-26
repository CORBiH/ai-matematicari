# -*- coding: utf-8 -*-
"""V3 Practice dispatcher — the flag-gated entry point wired into the existing
chat routes, before any legacy preprocessing.

Returns a response dict when V3 Practice handled the turn (``on`` mode for an
eligible lesson), or ``None`` to fall through to the existing backend (``off``,
``shadow``, an ineligible lesson, or a safe pre-mutation fallback).

Transaction discipline (never a write lock held across a model call):
    reserve turn  →  [model calls, no txn]  →  reduce  →  commit turn

Model client and store are injectable so tests use fakes and a temp DB; no live
OpenAI call is ever made from a test.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from matbot.ai_tutor_v3 import (
    adapter, lesson_blueprint, orchestrator, quality_gate, reducer,
    sheets_outbox, verifier,
)
from matbot.ai_tutor_v3.orchestrator import StructuredModelClient
from matbot.ai_tutor_v3.rendering import normalize_math_for_display
from matbot.ai_tutor_v3.schemas import (
    ActiveTask,
    CoverageState,
    PracticeModelAssessment,
    SessionCounters,
    StudentTurnInterpretation,
    TutorSessionState,
    UsageMetrics,
)
from matbot.ai_tutor_v3.state_store import V3StateStore, VersionConflict, now_iso

log = logging.getLogger("matbot.ai_tutor_v3.dispatcher")

_SCHEMA_VERSION = "v3.1"
_PRACTICE_MODES = {"practice", "vjezba", "vježba"}
_VALID_FLAG = {"off", "shadow", "on"}

# Injectable model-client seam. Tests call set_model_client(fake); production
# builds an OpenAI Responses client lazily on first real use.
_MODEL_CLIENT: Optional[StructuredModelClient] = None

_FALLBACK_TEXT = (
    "Trenutno ne mogu pouzdano pripremiti odgovor. Pokušaj ponovo za koji "
    "trenutak.")


def set_model_client(client: Optional[StructuredModelClient]) -> None:
    """Inject a structured-output model client (tests) or clear it (None)."""
    global _MODEL_CLIENT
    _MODEL_CLIENT = client


def _model_client() -> StructuredModelClient:
    if _MODEL_CLIENT is not None:
        return _MODEL_CLIENT
    return orchestrator.OpenAIResponsesClient()


def practice_flag() -> str:
    value = (os.getenv("MATBOT_AI_TUTOR_V3_PRACTICE") or "off").strip().lower()
    return value if value in _VALID_FLAG else "off"


#: The single wildcard token. Anything else is an explicit canonical lesson id.
_WILDCARD = "*"


def lesson_whitelist_mode() -> tuple[str, frozenset[str]]:
    """Parse ``MATBOT_AI_TUTOR_V3_LESSONS`` into ``(mode, explicit_ids)``.

    ``mode`` is one of:
      "none"     — nothing eligible (default; empty or unparseable value)
      "explicit" — only the ids in ``explicit_ids`` are eligible
      "all"      — every lesson that resolves through retained curriculum
                   infrastructure, for an already-checked grade 6-9 Practice
                   turn, is eligible

    Whitespace around the whole value and around each comma-separated token is
    stripped. An empty value (or one with no non-empty tokens) means NO lessons
    are eligible — fail-safe, unchanged from before wildcard support.

    A bare ``*`` (optionally repeated, e.g. ``"*,*"``) means EVERY lesson that
    resolves through retained curriculum infrastructure is eligible, for a turn
    that has ALREADY passed the grade (6-9) and mode ("practice") checks in
    ``v3_practice_dispatch`` — the wildcard widens WHICH lesson, never which
    grade or mode.

    Mixed forms such as ``"*,6-03-024"`` are deliberately AMBIGUOUS — is the
    explicit id redundant, or is the wildcard a typo for a narrower intent? —
    so the documented, deterministic policy is to fail closed: mixing the
    wildcard with any explicit id is treated exactly like an empty whitelist
    (nothing eligible), never silently upgraded to "all".
    """
    raw = os.getenv("MATBOT_AI_TUTOR_V3_LESSONS") or ""
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    if not tokens:
        return "none", frozenset()
    has_wildcard = any(t == _WILDCARD for t in tokens)
    if has_wildcard:
        if all(t == _WILDCARD for t in tokens):
            return "all", frozenset()
        return "none", frozenset()   # mixed wildcard + explicit id(s): fail closed
    return "explicit", frozenset(tokens)


def lesson_whitelist() -> frozenset[str]:
    """Explicit canonical lesson IDs eligible for V3 (empty under wildcard or
    "none" mode). Kept for callers that only care about the explicit set;
    ``v3_practice_dispatch`` uses ``lesson_whitelist_mode`` directly so it can
    also see the wildcard."""
    mode, ids = lesson_whitelist_mode()
    return ids if mode == "explicit" else frozenset()


def _grade_ok(value) -> Optional[int]:
    try:
        g = int(value)
    except (TypeError, ValueError):
        return None
    return g if g in (6, 7, 8, 9) else None


def _get_store() -> V3StateStore:
    store = V3StateStore()
    store.init_db()
    return store


# --------------------------------------------------------------------------- #
# Public entry point                                                          #
# --------------------------------------------------------------------------- #
def v3_practice_dispatch(
    payload: dict, *, model: str = "",
    timeout: Optional[float] = None, endpoint: str = "",
) -> Optional[dict]:
    """Handle one Practice turn under V3, or return None to fall back to legacy.

    ``model`` is resolved HERE, once, via ``orchestrator.resolve_v3_model()``
    when the caller does not explicitly pass one (production callers in
    ``ai_tutor_service.py`` do not — V3 must not inherit the legacy
    ``OPENAI_MODEL_TEXT`` model argument). All seven V3 call purposes receive
    the SAME resolved model, since every downstream call threads this one
    value through.
    """
    model = model.strip() if isinstance(model, str) and model.strip() else orchestrator.resolve_v3_model()
    flag = practice_flag()
    if flag == "off":
        return None
    if not isinstance(payload, dict):
        return None
    mode = str(payload.get("mode") or payload.get("session_mode") or "").strip().lower()
    if mode not in _PRACTICE_MODES:
        return None
    grade = _grade_ok(payload.get("grade"))
    if grade is None:
        return None
    if payload.get("image_bytes") or payload.get("image_data_url"):
        return None  # images not handled by V3 Practice yet

    identity = lesson_blueprint.resolve_lesson_identity(
        grade, str(payload.get("selected_topic") or ""),
        str(payload.get("selected_oblast") or ""))
    if identity is None:
        return None  # unresolved lesson → legacy decides (no V3 mutation)

    # Eligibility. By this point grade is already 6-9 and mode is already
    # "practice" (checked above), and ``identity`` already proves this lesson
    # resolved through retained curriculum infrastructure — so under wildcard
    # ("all") every one of them is eligible. This function is Practice-only;
    # Explain/Quick/Exam have no dispatcher and are structurally unreachable
    # here regardless of the wildcard.
    whitelist_mode, explicit_ids = lesson_whitelist_mode()
    if whitelist_mode == "none":
        return None                                   # nothing eligible
    if whitelist_mode == "explicit" and identity.lesson_id not in explicit_ids:
        return None                                   # not on the explicit list

    # Eligible. In shadow mode we compute against an ISOLATED session and always
    # return None so the legacy response stays visible.
    shadow = flag == "shadow"
    try:
        response = _run_v3_turn(
            payload, identity=identity, grade=grade, model=model,
            timeout=timeout, shadow=shadow)
    except Exception:
        # Never let a V3 defect break the turn: before any commit we can fall
        # back to legacy; the store transaction guarantees no partial state.
        return None
    if shadow:
        return None  # legacy controls the visible response
    return response


# --------------------------------------------------------------------------- #
# Turn timing (Phase 8): a total wall-clock BUDGET across all of a turn's     #
# model calls, separate from each individual call's own timeout.             #
# --------------------------------------------------------------------------- #
class _TurnBudget:
    """Root cause of the live '⏳ Odgovor traje duže nego obično' timeout:
    the frontend's OWN client-side AbortController fires at a FIXED 60s
    (see templates/index.html) regardless of the backend, while a V3 turn can
    make more than one sequential model call, each previously given the full
    legacy per-call timeout (45s) — two such calls could take up to 90s,
    comfortably exceeding the client's abort window.

    This tracks ONE deadline across every model call in a turn. Each call
    gets ``min(per-call cap, remaining budget)`` — never more than what's
    actually left — and once the remaining budget drops below
    ``MIN_V3_CALL_TIMEOUT_S``, no further call is attempted at all: better to
    fail safely and fast than to start a call almost certain to be aborted
    anyway.
    """

    def __init__(self, external_timeout: Optional[float]):
        call_cap = orchestrator.resolve_v3_call_timeout_s()
        if external_timeout is not None:
            try:
                call_cap = min(call_cap, float(external_timeout))
            except (TypeError, ValueError):
                pass
        self._call_cap = call_cap
        self._deadline = time.monotonic() + orchestrator.resolve_v3_turn_budget_s()
        self.exceeded = False

    def next_timeout(self) -> Optional[float]:
        """The timeout for the NEXT model call, or ``None`` if the turn's
        total budget is already too low to safely attempt one — the caller
        must not make the call in that case."""
        remaining = self._deadline - time.monotonic()
        if remaining <= orchestrator.MIN_V3_CALL_TIMEOUT_S:
            self.exceeded = True
            return None
        return min(self._call_cap, remaining)


# --------------------------------------------------------------------------- #
# Turn execution                                                              #
# --------------------------------------------------------------------------- #
def _run_v3_turn(payload, *, identity, grade, model, timeout, shadow):
    turn_started = time.monotonic()
    store = _get_store()
    client = _model_client()
    budget = _TurnBudget(timeout)

    call_timeout = budget.next_timeout()
    if call_timeout is None:
        return None  # budget exhausted before any call — nothing mutated yet
    blueprint, reason, blueprint_cache_hit = lesson_blueprint.get_or_create_blueprint(
        store, client, identity=identity, grade=grade, model=model,
        timeout=call_timeout)
    if blueprint is None:
        # No session mutated yet → safe to fall back to legacy.
        return None

    session_id = str(payload.get("session_id") or ("sess_" + uuid.uuid4().hex[:12]))
    if shadow:
        session_id = "shadow:" + session_id
    client_turn_id = str(payload.get("client_turn_id") or uuid.uuid4().hex)
    request_id = str(payload.get("request_id") or uuid.uuid4().hex)
    student_message = _student_message(payload)

    # Load or bootstrap the durable session (bootstrap = first V3 mutation).
    loaded = store.load_session(session_id)
    is_bootstrap = loaded is None
    if loaded is None:
        state = _bootstrap_state(session_id, grade, identity, blueprint)
        store.create_session(
            session_id=session_id, student_id=state.student_id, grade=grade,
            mode="practice", lesson_id=identity.lesson_id,
            blueprint_id=blueprint.blueprint_id,
            blueprint_version=blueprint.blueprint_version,
            state_json=state.model_dump_json(),
            created_at=state.created_at.isoformat(),
            updated_at=state.updated_at.isoformat())
        loaded = store.load_session(session_id)
    state = TutorSessionState.model_validate_json(loaded[0])
    version = loaded[1]

    # Idempotency: reserve the turn.
    turn_id = "turn_" + uuid.uuid4().hex[:12]
    reservation = store.reserve_turn(
        session_id=session_id, client_turn_id=client_turn_id, turn_id=turn_id,
        request_id=request_id, created_at=now_iso(),
        input_summary_json=_input_summary(student_message))
    if reservation.status == "completed":
        # Exact duplicate delivery → replay the stored response, counters intact.
        import json as _json
        try:
            return _json.loads(reservation.response_json) if reservation.response_json else None
        except (ValueError, TypeError):
            return None
    if reservation.status == "in_progress":
        return _bounded_conflict_response(state, version, identity)
    turn_id = reservation.turn_id

    # Verification gate (required mode refuses before any state mutation).
    vdecision = verifier.verify_batch(claims_present=bool(student_message))
    if not vdecision.can_proceed:
        store.fail_turn(turn_id=turn_id, error_code=vdecision.refusal_reason,
                        completed_at=now_iso())
        return _refusal_response(state, version, identity, vdecision.refusal_reason)

    # Model calls happen here with NO db transaction open.
    outcome_bundle = _execute_turn(
        client, blueprint=blueprint, state=state, grade=grade,
        student_message=student_message, model=model, budget=budget,
        verification=vdecision, is_bootstrap=is_bootstrap,
        intent=str(payload.get("intent") or "").strip().lower(),
        difficulty_request=str(payload.get("difficulty_request") or "").strip().lower())
    if outcome_bundle.get("error"):
        # A real, billed model call may have happened here (HTTP 200 with an
        # invalid output — incomplete/refused/empty/malformed/schema-invalid
        # — is still one real call) — its usage/latency and structural
        # diagnostics must not be lost just because the OUTPUT was unusable.
        # Never the model output itself, only counts/purposes/error_code.
        import json as _json
        failed_usage = outcome_bundle.get("usage")
        usage_json = failed_usage.model_dump_json() if failed_usage is not None else ""
        audit_json = _json.dumps({
            "schema_version": _SCHEMA_VERSION,
            "call_purposes": outcome_bundle.get("purposes") or [],
            "call_count": len(outcome_bundle.get("purposes") or []),
            "error_code": outcome_bundle["error"],
            "active_task_telemetry": outcome_bundle.get("active_task_telemetry"),
        }, ensure_ascii=False) if outcome_bundle.get("purposes") else ""
        store.fail_turn(turn_id=turn_id, error_code=outcome_bundle["error"],
                        completed_at=now_iso(), usage_json=usage_json,
                        audit_json=audit_json)
        # Session state was NOT mutated → active task preserved.
        return _fallback_response(state, version, identity,
                                  outcome_bundle["error"], vdecision)

    new_state = outcome_bundle["new_state"]
    outcome = outcome_bundle["outcome"]
    answer = outcome_bundle["answer"]
    category = outcome_bundle["category"]

    # Bookkeeping the reducer intentionally leaves to the dispatcher.
    new_state = _record_turn_bookkeeping(new_state, student_message, answer,
                                         outcome, outcome_bundle["turn_kind"])

    response = adapter.build_response(
        answer=answer, state=new_state, outcome=outcome,
        effective_topic=identity.lesson_id,
        verification_status=vdecision.result.status,
        state_version=version + 1, response_category=category)

    audit = _audit_record(
        request_id=request_id, session_id=session_id,
        client_turn_id=client_turn_id, identity=identity, blueprint=blueprint,
        outcome_bundle=outcome_bundle, vdecision=vdecision,
        version_before=version, version_after=version + 1, category=category,
        blueprint_cache_hit=blueprint_cache_hit,
        turn_latency_ms=(time.monotonic() - turn_started) * 1000.0)
    # The SAME privacy-safe audit dict IS the compact V3 telemetry Phase 10
    # asks for in the Sheets row — no second telemetry shape to maintain.
    response["v3_telemetry"] = audit

    import json as _json
    try:
        store.commit_turn(
            session_id=session_id, turn_id=turn_id, expected_version=version,
            new_state_json=new_state.model_dump_json(), updated_at=now_iso(),
            interpretation_json=_json.dumps(outcome_bundle.get("interpretation") or {},
                                            ensure_ascii=False),
            model_assessment_json=_json.dumps(outcome_bundle.get("assessment") or {},
                                              ensure_ascii=False),
            outcome_json=outcome.model_dump_json(),
            response_json=_json.dumps(response, ensure_ascii=False),
            usage_json=outcome_bundle["usage"].model_dump_json(),
            audit_json=_json.dumps(audit, ensure_ascii=False),
            completed_at=now_iso(), state_version_before=version)
    except VersionConflict:
        # A concurrent turn advanced the session first. Do NOT re-apply — return
        # the current state safely; counters were never touched by this turn.
        store.fail_turn(turn_id=turn_id, error_code="version_conflict",
                        completed_at=now_iso())
        return _fallback_response(state, version, identity, "version_conflict",
                                  vdecision)

    # Sheets logging: ONLY after the authoritative commit above, ONLY for a
    # visible (non-shadow) turn. ``sheets_outbox.enqueue`` does exactly one
    # thing synchronously here — a local SQLite insert into the durable
    # ``v3_sheets_outbox`` table — so the response is NEVER blocked on Google
    # Sheets, regardless of Sheets health or any Sheets config. Actual
    # delivery happens on a separate, reused background worker (never a
    # thread spawned per request); anything it cannot deliver stays
    # retryable and durable across a process restart — see
    # ``matbot.ai_tutor_v3.sheets_outbox``. A duplicate replay never reaches
    # this line at all — it returns earlier from the
    # ``reservation.status == "completed"`` branch above.
    if not shadow:
        try:
            sheets_outbox.enqueue(
                store, client_turn_id=client_turn_id, session_id=session_id,
                payload=payload, response=response)
        except Exception:
            log.exception("V3 Sheets outbox enqueue failed; response already committed")
    return response


#: Trusted, EXISTING wire-contract signals the frontend already sends for a
#: specific UI button click (never inferred from free-text phrase matching) —
#: reading them lets the dispatcher skip a generic turn_interpretation call
#: for a turn whose kind is already known with certainty. See templates/
#: index.html: the "Ne znam — daj mi hint" chip sends intent=hint_request; the
#: "Lakši/Teži zadatak" chips send difficulty_request=easier/harder.
_TRUSTED_DIFFICULTY_REQUESTS = {"easier", "harder"}


def _synthetic_interpretation(turn_kind: str, *, meaning: str) -> StudentTurnInterpretation:
    """A deterministic, server-built interpretation for a turn whose kind is
    ALREADY known with certainty from request context — no model call, no
    guessing. Only ever used for turn kinds the reducer treats as strictly
    no-progress-mutation (task_request, help_request, difficulty_change), so
    a wrong guess here can never mis-grade an answer."""
    return StudentTurnInterpretation(
        schema_version=_SCHEMA_VERSION, turn_kind=turn_kind, is_answer_attempt=False,
        normalized_meaning=meaning, claims=[], certainty="certain",
        precision="unspecified", confidence=1.0)


def _synthetic_difficulty_assessment(direction: str) -> PracticeModelAssessment:
    return PracticeModelAssessment(
        schema_version=_SCHEMA_VERSION, is_answer_attempt=False,
        proposed_verdict="not_checkable", proposed_pedagogical_action="continue",
        difficulty_suggestion=direction, confidence=1.0)


def _execute_turn(client, *, blueprint, state, grade, student_message, model,
                  budget, verification, is_bootstrap=False, intent="",
                  difficulty_request=""):
    """Interpret → reduce → render. Never mutates the store.

    Three trusted, request-context-derived shortcuts skip the generic
    turn_interpretation model call entirely (Phase 7 consolidation) — never a
    brittle free-text phrase match:
      * ``is_bootstrap``: the very first V3 turn of a brand new session. The
        existing frontend auto-sends the Practice-entry trigger message the
        instant this mode is chosen, so "start practice" is what this turn
        structurally IS — and even if it weren't, task_request mutates no
        progress, so a wrong guess here is harmless.
      * ``intent == "hint_request"``: sent by the existing "Ne znam — daj mi
        hint" button, only honoured while a task is active.
      * ``difficulty_request in {"easier","harder"}``: sent by the existing
        difficulty chips.
    Free-form typed text always goes through the real interpretation call.

    ``budget`` is a ``_TurnBudget`` — every model call in this turn draws its
    own timeout from it (Phase 8), so a slow first call leaves a shrinking,
    never negative, window for any further one.
    """
    usage = UsageMetrics()
    purposes: list[str] = []
    narration_proposal = None
    active_task_telemetry = None

    if is_bootstrap:
        interpretation = _synthetic_interpretation(
            "task_request", meaning="Početak vježbe.")
        assessment = None
    elif intent == "hint_request" and state.active_task is not None:
        interpretation = _synthetic_interpretation(
            "help_request", meaning="Traži pomoć oko aktivnog zadatka.")
        assessment = None
    elif difficulty_request in _TRUSTED_DIFFICULTY_REQUESTS:
        interpretation = _synthetic_interpretation(
            "difficulty_change", meaning=f"Traži {difficulty_request} zadatak.")
        assessment = _synthetic_difficulty_assessment(difficulty_request)
    elif state.active_task is not None:
        # The DOMINANT case: free-form message, task already active — the
        # compact path (Section 3-6 latency pass). Smaller context, smaller
        # schema, bounded output tokens; the reducer receives the SAME
        # StudentTurnInterpretation/PracticeModelAssessment shape either way.
        call_timeout = budget.next_timeout()
        if call_timeout is None:
            return {"error": "turn_budget_exceeded", "usage": usage, "purposes": purposes}
        decision, call = orchestrator.interpret_active_task_turn(
            client, grade=grade, blueprint=blueprint, state=state,
            student_message=student_message, model=model, timeout=call_timeout)
        _accumulate(usage, call)
        purposes.append(call.purpose)
        # Structural telemetry only (Section 6) — never a prompt, student
        # text, or raw model output — to compare reasoning-effort/token-cap
        # behavior before and after this latency pass.
        active_task_telemetry = {
            "reasoning_effort": call.reasoning_effort,
            "max_output_tokens": call.max_output_tokens,
            "response_status": call.response_status,
            "incomplete_reason": call.incomplete_reason,
        }
        if decision is None:
            return {"error": call.error_code or call.status, "usage": usage,
                   "purposes": purposes, "active_task_telemetry": active_task_telemetry}
        interpretation, assessment = orchestrator.decision_to_interpretation_and_assessment(decision)
        narration_proposal = decision.narration_proposal
    else:
        # No active task yet (rare outside bootstrap/trusted-intent paths) —
        # genuinely needs the fuller session/Blueprint picture.
        call_timeout = budget.next_timeout()
        if call_timeout is None:
            return {"error": "turn_budget_exceeded", "usage": usage, "purposes": purposes}
        interp_bundle, call = orchestrator.interpret_turn(
            client, grade=grade, blueprint=blueprint, state=state,
            student_message=student_message, model=model, timeout=call_timeout)
        _accumulate(usage, call)
        purposes.append(call.purpose)
        if interp_bundle is None:
            return {"error": call.error_code or call.status, "usage": usage, "purposes": purposes}
        interpretation = interp_bundle.interpretation
        assessment = interp_bundle.assessment
        narration_proposal = interp_bundle.narration_proposal

    result = reducer.reduce_turn(
        state=state, interpretation=interpretation, assessment=assessment,
        verification=verification)

    answer, category, err, gate_info = _render(
        client, action=result.next_action, blueprint=blueprint,
        state=result.new_state, outcome=result.outcome,
        interpretation=interpretation, grade=grade,
        student_message=student_message, model=model, budget=budget,
        usage=usage, purposes=purposes, narration_proposal=narration_proposal)
    if err:
        return {"error": err, "usage": usage, "purposes": purposes,
               "active_task_telemetry": active_task_telemetry}

    return {
        "new_state": result.new_state, "outcome": result.outcome,
        "answer": answer, "category": category or result.response_category,
        "turn_kind": interpretation.turn_kind, "usage": usage,
        "purposes": purposes, "quality_gate": gate_info,
        "active_task_telemetry": active_task_telemetry,
        "interpretation": interpretation.model_dump(mode="json"),
        "assessment": assessment.model_dump(mode="json") if assessment else None,
    }


#: A response never gated (fixed, already-known-safe deterministic strings) —
#: cheap and unambiguous telemetry rather than running the gate for nothing.
_NO_GATE_INFO = {"checked": False, "repaired": False, "failure_categories": []}


def _apply_quality_gate(client, text, *, response_type, grade, blueprint, model,
                        budget, usage, purposes, previous_text=None,
                        forbidden_reveal=None):
    """Phase 5: deterministic checks, then at most ONE bounded model repair.

    Never mutates session/task state itself — callers decide what (if
    anything) to do with the returned text. Purely mechanical MathJax
    delimiter issues are fixed by ``normalize_math_for_display`` alone, with
    NO model call — the gate only escalates to a repair call for a failure
    that survives normalization. The repair call draws its timeout from the
    SAME turn ``budget`` as every other call (Phase 8) — if the budget is
    already exhausted, the safe fallback text is used instead of attempting
    one more call certain to be cut short.
    """
    normalized = normalize_math_for_display(text)
    result = quality_gate.check(
        normalized, response_type=response_type, grade=grade,
        previous_text=previous_text, forbidden_reveal=forbidden_reveal)
    if result.ok:
        return normalized, {"checked": True, "repaired": False, "failure_categories": []}

    call_timeout = budget.next_timeout()
    if call_timeout is None:
        return quality_gate.SAFE_FALLBACK_TEXT, {
            "checked": True, "repaired": False,
            "failure_categories": result.failure_categories,
            "gate_failed_final": True, "reason": "turn_budget_exceeded"}

    repaired, call = orchestrator.repair_student_text(
        client, grade=grade, rejected_text=normalized,
        failure_categories=result.failure_categories, response_type=response_type,
        blueprint=blueprint, model=model, timeout=call_timeout)
    _accumulate(usage, call)
    purposes.append(call.purpose)
    if repaired is not None:
        renormalized = normalize_math_for_display(repaired.student_text)
        result2 = quality_gate.check(
            renormalized, response_type=response_type, grade=grade,
            previous_text=previous_text, forbidden_reveal=forbidden_reveal)
        if result2.ok:
            return renormalized, {
                "checked": True, "repaired": True,
                "failure_categories": result.failure_categories, "gate_failed_final": False}
    return quality_gate.SAFE_FALLBACK_TEXT, {
        "checked": True, "repaired": True,
        "failure_categories": result.failure_categories, "gate_failed_final": True}


def _render(client, *, action, blueprint, state, outcome, interpretation, grade,
            student_message, model, budget, usage, purposes, narration_proposal=None):
    """Produce the student-facing text for the reducer's chosen action. Narration
    can never change the already-decided outcome — it only writes language."""
    if action == reducer.OFF_TOPIC:
        return orchestrator.OFF_TOPIC_FALLBACK, "off_topic", "", dict(_NO_GATE_INFO)
    if action == reducer.CLARIFY:
        seed = (outcome.clarification_prompt_seed
                or interpretation.clarification_question
                or "Možeš li pojasniti?")
        text, gate_info = _apply_quality_gate(
            client, seed, response_type="clarification", grade=grade,
            blueprint=blueprint, model=model, budget=budget,
            usage=usage, purposes=purposes)
        return text, "clarification", "", gate_info
    if action == reducer.GENERATE_TASK:
        return _render_new_task(client, blueprint=blueprint, state=state,
                                interpretation=interpretation, grade=grade,
                                model=model, budget=budget, usage=usage,
                                purposes=purposes)
    if action == reducer.NARRATE_FEEDBACK and narration_proposal is not None:
        # Phase 7 consolidation: the interpretation call already proposed
        # feedback text for this answer turn — use it (through the SAME
        # quality gate every other narration goes through) instead of an
        # extra narrate_feedback model call. The reducer's already-decided
        # ``outcome.verdict`` is what actually happened; this text only
        # supplies the WORDING, exactly like a real narrate_feedback call.
        text, gate_info = _apply_quality_gate(
            client, narration_proposal.student_text,
            response_type=narration_proposal.response_category or "feedback",
            grade=grade, blueprint=blueprint, model=model, budget=budget,
            usage=usage, purposes=purposes)
        return text, narration_proposal.response_category, "", gate_info

    call_timeout = budget.next_timeout()
    if call_timeout is None:
        return "", "", "turn_budget_exceeded", dict(_NO_GATE_INFO)
    call_map = {
        reducer.NARRATE_FEEDBACK: lambda: orchestrator.narrate_feedback(
            client, grade=grade, blueprint=blueprint, state=state,
            verdict=outcome.verdict, model=model, timeout=call_timeout),
        reducer.GIVE_HINT: lambda: orchestrator.generate_hint(
            client, grade=grade, blueprint=blueprint, state=state,
            model=model, timeout=call_timeout),
        reducer.EXPLAIN_CONCEPT: lambda: orchestrator.explain_concept(
            client, grade=grade, blueprint=blueprint, state=state,
            student_message=student_message, model=model, timeout=call_timeout),
        reducer.REVEAL_SOLUTION: lambda: orchestrator.reveal_solution(
            client, grade=grade, blueprint=blueprint, state=state,
            model=model, timeout=call_timeout),
        reducer.ACKNOWLEDGE: lambda: orchestrator.acknowledge(
            client, grade=grade, blueprint=blueprint, state=state,
            student_message=student_message, model=model, timeout=call_timeout),
    }
    fn = call_map.get(action)
    if fn is None:
        return _FALLBACK_TEXT, "fallback", "", dict(_NO_GATE_INFO)
    narration, call = fn()
    _accumulate(usage, call)
    purposes.append(call.purpose)
    if narration is None:
        return "", "", (call.error_code or call.status), dict(_NO_GATE_INFO)
    text, gate_info = _apply_quality_gate(
        client, narration.student_text, response_type=narration.response_category,
        grade=grade, blueprint=blueprint, model=model, budget=budget,
        usage=usage, purposes=purposes)
    return text, narration.response_category, "", gate_info


def _render_new_task(client, *, blueprint, state, interpretation, grade, model,
                     budget, usage, purposes):
    """Generate, validate-against-lesson, gate, and activate one task. A
    rejected task leaves NO corrupt active task (the reducer already cleared
    it); the quality gate runs and repairs BEFORE the task is assigned to
    ``state.active_task``, so a rejected/repaired question never leaks into
    durable state ahead of the text the student actually sees."""
    call_timeout = budget.next_timeout()
    if call_timeout is None:
        return "", "", "turn_budget_exceeded", dict(_NO_GATE_INFO)
    target = _requested_target(interpretation, blueprint) \
        or reducer.next_uncovered_target(state)
    spec, call = orchestrator.generate_task(
        client, grade=grade, blueprint=blueprint, state=state,
        target_id=target, model=model, timeout=call_timeout)
    _accumulate(usage, call)
    purposes.append(call.purpose)
    if spec is None:
        return "", "", (call.error_code or call.status), dict(_NO_GATE_INFO)
    # Task must belong to THIS lesson's blueprint (target OR concept known).
    known_targets = {t.target_id for t in blueprint.coverage_targets}
    known_concepts = {c.concept_id for c in blueprint.concepts}
    if spec.target_id not in known_targets and spec.concept_id not in known_concepts:
        return "", "", "task_off_lesson", dict(_NO_GATE_INFO)
    question, gate_info = _apply_quality_gate(
        client, spec.question, response_type="task", grade=grade,
        blueprint=blueprint, model=model, budget=budget,
        usage=usage, purposes=purposes, forbidden_reveal=spec.expected_internal)
    task = ActiveTask(
        task_id=reducer.new_task_id(), concept_id=spec.concept_id,
        target_id=spec.target_id, question=question,
        answer_kind=spec.answer_kind, expected_internal=spec.expected_internal,
        difficulty_level=spec.difficulty_level,
        planned_verification_type=spec.planned_verification_type)
    state.active_task = task
    if spec.target_id and spec.target_id not in state.coverage.attempts_per_target:
        state.coverage.attempts_per_target.setdefault(spec.target_id, 0)
    return task.question, "task", "", gate_info


# --------------------------------------------------------------------------- #
# State + bookkeeping helpers                                                 #
# --------------------------------------------------------------------------- #
def _bootstrap_state(session_id, grade, identity, blueprint) -> TutorSessionState:
    now = datetime.now(timezone.utc)
    targets = [t.target_id for t in blueprint.coverage_targets]
    return TutorSessionState(
        schema_version=_SCHEMA_VERSION, session_id=session_id,
        student_id=_opaque_student_id(session_id), grade=grade, mode="practice",
        lesson_id=identity.lesson_id, blueprint_id=blueprint.blueprint_id,
        blueprint_version=blueprint.blueprint_version,
        prompt_policy=orchestrator.current_prompt_policy(blueprint.blueprint_version),
        counters=SessionCounters(),
        coverage=CoverageState(targets=targets),
        created_at=now, updated_at=now)


def _record_turn_bookkeeping(state, student_message, answer, outcome, turn_kind):
    from matbot.ai_tutor_v3.schemas import RecentTurn
    state.turn_index += 1
    state.updated_at = datetime.now(timezone.utc)
    state.recent_turns.append(RecentTurn(
        turn_index=state.turn_index, role="student", text=student_message[:400],
        turn_kind=turn_kind, verdict=outcome.verdict))
    state.recent_turns.append(RecentTurn(
        turn_index=state.turn_index, role="tutor", text=answer[:400]))
    # Keep only the most recent meaningful turns in the durable projection.
    if len(state.recent_turns) > 30:
        state.recent_turns = state.recent_turns[-30:]
    # Clarification state mirrors the outcome.
    if outcome.verdict == "needs_clarification" and outcome.clarification_prompt_seed:
        from matbot.ai_tutor_v3.schemas import PendingClarification
        state.pending_clarification = PendingClarification(
            prompt_seed=outcome.clarification_prompt_seed,
            raised_at_turn=state.turn_index)
    else:
        state.pending_clarification = None
    return state


def _opaque_student_id(session_id: str) -> str:
    """A privacy-safe opaque id derived from the session — never an email."""
    import hashlib
    return "stu_" + hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]


def _student_message(payload: dict) -> str:
    for key in ("student_message", "message", "raw_student_message"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _requested_target(interpretation, blueprint) -> Optional[str]:
    """If the student explicitly asked for a specific target and the blueprint
    supports it, honour it. A forced concept must be a KNOWN target so coverage
    state is never corrupted."""
    action = (interpretation.requested_action or "").strip()
    if not action:
        return None
    known = {t.target_id for t in blueprint.coverage_targets}
    return action if action in known else None


def _accumulate(usage: UsageMetrics, call) -> None:
    usage.model_calls += 1
    latency = getattr(call, "latency_ms", 0.0) or 0.0
    usage.total_latency_ms += latency
    usage.per_call_latency_ms.append(latency)
    u = getattr(call, "usage", {}) or {}
    usage.prompt_tokens += int(u.get("prompt_tokens", 0) or 0)
    usage.completion_tokens += int(u.get("completion_tokens", 0) or 0)


def _input_summary(student_message: str) -> str:
    import json as _json
    return _json.dumps({"len": len(student_message)}, ensure_ascii=False)


def _audit_record(*, request_id, session_id, client_turn_id, identity, blueprint,
                  outcome_bundle, vdecision, version_before, version_after,
                  category, blueprint_cache_hit=None, turn_latency_ms=None) -> dict:
    """Structured, privacy-safe per-turn telemetry (Phase 9).

    Never includes: API keys, complete prompts, hidden chain-of-thought,
    parent contact information, or the raw conversation — only identifiers,
    counts, timings, categories and the already-committed outcome/verdict.
    """
    interp = outcome_bundle.get("interpretation") or {}
    usage = outcome_bundle["usage"]
    return {
        "schema_version": _SCHEMA_VERSION, "request_id": request_id,
        "session_id": session_id, "client_turn_id": client_turn_id,
        "lesson_id": identity.lesson_id, "mode": "practice",
        "blueprint_id": blueprint.blueprint_id,
        "blueprint_version": blueprint.blueprint_version,
        "blueprint_cache_hit": bool(blueprint_cache_hit),
        "prompt_policy": blueprint.prompt_policy.model_dump(mode="json"),
        "model": blueprint.model,
        "call_purposes": outcome_bundle.get("purposes") or [],
        "call_count": len(outcome_bundle.get("purposes") or []),
        "quality_gate": outcome_bundle.get("quality_gate") or {},
        "verification_status": vdecision.result.status,
        "turn_kind": outcome_bundle.get("turn_kind"),
        "interpretation_summary": interp.get("normalized_meaning"),
        "proposed_verdict": (outcome_bundle.get("assessment") or {}).get("proposed_verdict"),
        "applied_verdict": outcome_bundle["outcome"].verdict,
        "response_category": category,
        "state_version_before": version_before,
        "state_version_after": version_after,
        "schema_validation_ok": True,
        "turn_latency_ms": turn_latency_ms,
        "usage": usage.model_dump(mode="json"),
        "active_task_telemetry": outcome_bundle.get("active_task_telemetry"),
    }


# --------------------------------------------------------------------------- #
# Safe fallback responses (task-preserving)                                   #
# --------------------------------------------------------------------------- #
def _fallback_response(state, version, identity, reason, vdecision) -> dict:
    resp = adapter.build_response(
        answer=_FALLBACK_TEXT, state=state, outcome=None,
        effective_topic=identity.lesson_id,
        verification_status=vdecision.result.status,
        state_version=version, response_category="fallback")
    resp["v3_fallback_reason"] = reason
    return resp


def _refusal_response(state, version, identity, reason) -> dict:
    resp = adapter.build_response(
        answer=("Trenutno ne mogu provjeriti odgovor determinističkim putem, pa "
                "ne bih mijenjao/la tvoj napredak. Pokušaj kasnije."),
        state=state, outcome=None, effective_topic=identity.lesson_id,
        verification_status="unavailable", state_version=version,
        response_category="verification_required_unavailable")
    resp["v3_fallback_reason"] = reason
    return resp


def _bounded_conflict_response(state, version, identity) -> dict:
    resp = adapter.build_response(
        answer=("Tvoj prethodni zahtjev se još obrađuje. Sačekaj trenutak pa "
                "pokušaj ponovo."),
        state=state, outcome=None, effective_topic=identity.lesson_id,
        verification_status="model_only", state_version=version,
        response_category="in_progress")
    resp["v3_fallback_reason"] = "turn_in_progress"
    return resp
