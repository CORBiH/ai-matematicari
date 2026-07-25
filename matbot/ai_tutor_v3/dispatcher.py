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

import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from matbot.ai_tutor_v3 import adapter, lesson_blueprint, orchestrator, reducer, verifier
from matbot.ai_tutor_v3.orchestrator import StructuredModelClient
from matbot.ai_tutor_v3.schemas import (
    ActiveTask,
    CoverageState,
    SessionCounters,
    TutorSessionState,
    UsageMetrics,
)
from matbot.ai_tutor_v3.state_store import V3StateStore, VersionConflict, now_iso

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
# Turn execution                                                              #
# --------------------------------------------------------------------------- #
def _run_v3_turn(payload, *, identity, grade, model, timeout, shadow):
    store = _get_store()
    client = _model_client()

    blueprint, reason = lesson_blueprint.get_or_create_blueprint(
        store, client, identity=identity, grade=grade, model=model, timeout=timeout)
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
        student_message=student_message, model=model, timeout=timeout,
        verification=vdecision)
    if outcome_bundle.get("error"):
        store.fail_turn(turn_id=turn_id, error_code=outcome_bundle["error"],
                        completed_at=now_iso())
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
        version_before=version, version_after=version + 1, category=category)

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
    return response


def _execute_turn(client, *, blueprint, state, grade, student_message, model,
                  timeout, verification):
    """Interpret → reduce → render. Never mutates the store."""
    usage = UsageMetrics()
    purposes: list[str] = []

    interp_bundle, call = orchestrator.interpret_turn(
        client, grade=grade, blueprint=blueprint, state=state,
        student_message=student_message, model=model, timeout=timeout)
    _accumulate(usage, call)
    purposes.append(call.purpose)
    if interp_bundle is None:
        return {"error": call.error_code or call.status}

    interpretation = interp_bundle.interpretation
    assessment = interp_bundle.assessment
    result = reducer.reduce_turn(
        state=state, interpretation=interpretation, assessment=assessment,
        verification=verification)

    answer, category, err = _render(
        client, action=result.next_action, blueprint=blueprint,
        state=result.new_state, outcome=result.outcome,
        interpretation=interpretation, grade=grade,
        student_message=student_message, model=model, timeout=timeout,
        usage=usage, purposes=purposes)
    if err:
        return {"error": err}

    return {
        "new_state": result.new_state, "outcome": result.outcome,
        "answer": answer, "category": category or result.response_category,
        "turn_kind": interpretation.turn_kind, "usage": usage,
        "purposes": purposes,
        "interpretation": interpretation.model_dump(mode="json"),
        "assessment": assessment.model_dump(mode="json") if assessment else None,
    }


def _render(client, *, action, blueprint, state, outcome, interpretation, grade,
            student_message, model, timeout, usage, purposes):
    """Produce the student-facing text for the reducer's chosen action. Narration
    can never change the already-decided outcome — it only writes language."""
    if action == reducer.OFF_TOPIC:
        return orchestrator.OFF_TOPIC_FALLBACK, "off_topic", ""
    if action == reducer.CLARIFY:
        seed = (outcome.clarification_prompt_seed
                or interpretation.clarification_question
                or "Možeš li pojasniti?")
        return seed, "clarification", ""
    if action == reducer.GENERATE_TASK:
        return _render_new_task(client, blueprint=blueprint, state=state,
                                interpretation=interpretation, grade=grade,
                                model=model, timeout=timeout, usage=usage,
                                purposes=purposes)

    call_map = {
        reducer.NARRATE_FEEDBACK: lambda: orchestrator.narrate_feedback(
            client, grade=grade, blueprint=blueprint, state=state,
            verdict=outcome.verdict, model=model, timeout=timeout),
        reducer.GIVE_HINT: lambda: orchestrator.generate_hint(
            client, grade=grade, blueprint=blueprint, state=state,
            model=model, timeout=timeout),
        reducer.EXPLAIN_CONCEPT: lambda: orchestrator.explain_concept(
            client, grade=grade, blueprint=blueprint, state=state,
            student_message=student_message, model=model, timeout=timeout),
        reducer.REVEAL_SOLUTION: lambda: orchestrator.reveal_solution(
            client, grade=grade, blueprint=blueprint, state=state,
            model=model, timeout=timeout),
        reducer.ACKNOWLEDGE: lambda: orchestrator.acknowledge(
            client, grade=grade, blueprint=blueprint, state=state,
            student_message=student_message, model=model, timeout=timeout),
    }
    fn = call_map.get(action)
    if fn is None:
        return _FALLBACK_TEXT, "fallback", ""
    narration, call = fn()
    _accumulate(usage, call)
    purposes.append(call.purpose)
    if narration is None:
        return "", "", (call.error_code or call.status)
    return narration.student_text, narration.response_category, ""


def _render_new_task(client, *, blueprint, state, interpretation, grade, model,
                     timeout, usage, purposes):
    """Generate, validate-against-lesson, and activate one task. A rejected task
    leaves NO corrupt active task (the reducer already cleared it)."""
    target = _requested_target(interpretation, blueprint) \
        or reducer.next_uncovered_target(state)
    spec, call = orchestrator.generate_task(
        client, grade=grade, blueprint=blueprint, state=state,
        target_id=target, model=model, timeout=timeout)
    _accumulate(usage, call)
    purposes.append(call.purpose)
    if spec is None:
        return "", "", (call.error_code or call.status)
    # Task must belong to THIS lesson's blueprint (target OR concept known).
    known_targets = {t.target_id for t in blueprint.coverage_targets}
    known_concepts = {c.concept_id for c in blueprint.concepts}
    if spec.target_id not in known_targets and spec.concept_id not in known_concepts:
        return "", "", "task_off_lesson"
    task = ActiveTask(
        task_id=reducer.new_task_id(), concept_id=spec.concept_id,
        target_id=spec.target_id, question=spec.question,
        answer_kind=spec.answer_kind, expected_internal=spec.expected_internal,
        difficulty_level=spec.difficulty_level,
        planned_verification_type=spec.planned_verification_type)
    state.active_task = task
    if spec.target_id and spec.target_id not in state.coverage.attempts_per_target:
        state.coverage.attempts_per_target.setdefault(spec.target_id, 0)
    return task.question, "task", ""


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
    usage.total_latency_ms += getattr(call, "latency_ms", 0.0) or 0.0
    u = getattr(call, "usage", {}) or {}
    usage.prompt_tokens += int(u.get("prompt_tokens", 0) or 0)
    usage.completion_tokens += int(u.get("completion_tokens", 0) or 0)


def _input_summary(student_message: str) -> str:
    import json as _json
    return _json.dumps({"len": len(student_message)}, ensure_ascii=False)


def _audit_record(*, request_id, session_id, client_turn_id, identity, blueprint,
                  outcome_bundle, vdecision, version_before, version_after,
                  category) -> dict:
    interp = outcome_bundle.get("interpretation") or {}
    return {
        "schema_version": _SCHEMA_VERSION, "request_id": request_id,
        "session_id": session_id, "client_turn_id": client_turn_id,
        "lesson_id": identity.lesson_id, "mode": "practice",
        "blueprint_id": blueprint.blueprint_id,
        "blueprint_version": blueprint.blueprint_version,
        "prompt_policy": blueprint.prompt_policy.model_dump(mode="json"),
        "model": blueprint.model,
        "call_purposes": outcome_bundle.get("purposes") or [],
        "verification_status": vdecision.result.status,
        "turn_kind": outcome_bundle.get("turn_kind"),
        "interpretation_summary": interp.get("normalized_meaning"),
        "proposed_verdict": (outcome_bundle.get("assessment") or {}).get("proposed_verdict"),
        "applied_verdict": outcome_bundle["outcome"].verdict,
        "response_category": category,
        "state_version_before": version_before,
        "state_version_after": version_after,
        "schema_validation_ok": True,
        "usage": outcome_bundle["usage"].model_dump(mode="json"),
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
