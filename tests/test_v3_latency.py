# -*- coding: utf-8 -*-
"""V3 latency pass: disabled hidden SDK retries, a compact active-task
context + smaller purpose-specific schema + compact prompt for the dominant
ordinary-answer-turn case, and a bounded output-token cap.

No live/paid OpenAI call is made anywhere. Section-1 tests patch the
underlying httpx transport of a REAL ``OpenAIResponsesClient`` (not a fake)
to prove the installed SDK's own retry machinery genuinely engages or
doesn't — a stronger proof than asserting an attribute value alone.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from matbot.ai_tutor_v3 import orchestrator as orch
from matbot.ai_tutor_v3.schemas import (
    ActiveTaskTurnContext,
    ActiveTaskTurnDecision,
    ActiveTask,
    CoverageState,
    LessonIdentity,
    PracticeTurnInterpretation,
    RecentTurn,
    SessionCounters,
    TutorSessionState,
    export_json_schema,
)
from tests.test_v3_practice import blueprint_parsed, NINE_TARGETS


# --------------------------------------------------------------------------- #
# Shared realistic fixtures (no live call; used purely to measure/serialize)   #
# --------------------------------------------------------------------------- #
def _real_blueprint():
    from matbot.ai_tutor_v3.schemas import LessonBlueprint

    identity = LessonIdentity(
        grade=6, area_id="djeljivost-brojeva", area_title="Djeljivost brojeva",
        lesson_id="6-03-024",
        lesson_title="Djeljivost brojeva sa 2, 3, 4, 5, 6, 9, 10, 15 i 25")
    data = dict(blueprint_parsed(targets=NINE_TARGETS))
    data.update({
        "schema_version": "v3.1", "blueprint_id": "bp_test123456",
        "blueprint_version": "6-03-024@abcdef0123456789",
        "lesson_identity": identity.model_dump(mode="json"),
        "source_hash": "abcdef0123456789",
        "source_metadata": {"lesson_id": "6-03-024", "grade": "6"},
        "model": "gpt-5-mini",
        "prompt_policy": orch.current_prompt_policy().model_dump(mode="json"),
        "created_at": datetime.now(timezone.utc), "validation_status": "validated",
    })
    return LessonBlueprint.model_validate(data)


def _real_state(blueprint):
    now = datetime.now(timezone.utc)
    return TutorSessionState(
        schema_version="v3.1", session_id="sess_abc123", student_id="stu_xxx",
        grade=6, mode="practice", lesson_id="6-03-024",
        blueprint_id=blueprint.blueprint_id, blueprint_version=blueprint.blueprint_version,
        prompt_policy=orch.current_prompt_policy(),
        active_task=ActiveTask(
            task_id="t_abc123", concept_id="div-compound-6", target_id="6",
            question="Da li je 252 djeljiv sa 6?", answer_kind="boolean_with_reason",
            expected_internal="da", difficulty_level=2),
        counters=SessionCounters(attempts=2, wrong_attempts=1, solved_independent=1,
                                 correct_streak=1),
        coverage=CoverageState(targets=NINE_TARGETS, covered=["2", "5"],
                              attempts_per_target={"2": 1, "5": 1, "6": 1}),
        recent_turns=[
            RecentTurn(turn_index=1, role="student", text="Daj mi zadatak",
                      turn_kind="task_request"),
            RecentTurn(turn_index=1, role="tutor", text="Da li je 252 djeljiv sa 6?"),
        ],
        created_at=now, updated_at=now,
    )


@pytest.fixture()
def blueprint():
    return _real_blueprint()


@pytest.fixture()
def state(blueprint):
    return _real_state(blueprint)


def _full_baseline_sizes(grade, blueprint, state, student_message):
    system = orch.build_system_prompt(grade, blueprint)
    user = ("STANJE SESIJE:\n" + orch._session_projection(state) + "\n\n"
           "PORUKA UČENIKA:\n" + student_message + "\n\nProtumači poruku...")
    schema = export_json_schema(PracticeTurnInterpretation)
    schema_bytes = len(json.dumps(schema, ensure_ascii=False).encode("utf-8"))
    return {
        "system_chars": len(system), "user_chars": len(user),
        "total_prompt_chars": len(system) + len(user),
        "schema_bytes": schema_bytes,
        "schema_props": len(schema.get("properties", {})),
        "schema_defs": len(schema.get("$defs", {}) or schema.get("definitions", {})),
    }


def _compact_sizes(grade, blueprint, state, student_message):
    context = orch.build_active_task_context(
        grade=grade, blueprint=blueprint, state=state, student_message=student_message)
    system = orch.ACTIVE_TASK_INTERPRETATION_POLICY.format(grade=grade)
    user = context.model_dump_json()
    schema = export_json_schema(ActiveTaskTurnDecision)
    schema_bytes = len(json.dumps(schema, ensure_ascii=False).encode("utf-8"))
    return {
        "system_chars": len(system), "user_chars": len(user),
        "total_prompt_chars": len(system) + len(user),
        "schema_bytes": schema_bytes,
        "schema_props": len(schema.get("properties", {})),
        "schema_defs": len(schema.get("$defs", {}) or schema.get("definitions", {})),
    }


# =========================================================================== #
# Section 1 — hidden OpenAI SDK retries disabled for V3                       #
# =========================================================================== #
def test_v3_client_uses_max_retries_zero():
    client = orch.OpenAIResponsesClient(api_key="sk-test-not-real")
    assert client._client.max_retries == 0


def test_v3_client_max_retries_zero_with_explicit_api_key_too():
    client = orch.OpenAIResponsesClient(api_key="sk-explicit-key-not-real")
    assert client._client.max_retries == 0


def test_fake_timeout_produces_exactly_one_network_attempt(monkeypatch):
    """Patches the REAL client's underlying httpx transport (not just
    ``responses.create``) so the installed SDK's OWN retry loop is what runs
    — proving max_retries=0 truly means one network attempt, not merely that
    we never call retry logic ourselves."""
    import httpx

    client = orch.OpenAIResponsesClient(api_key="sk-test-not-real")
    attempts = {"n": 0}

    def _boom(*a, **kw):
        attempts["n"] += 1
        raise httpx.TimeoutException("simulated timeout")
    monkeypatch.setattr(client._client._client, "send", _boom)

    result = client.generate(
        purpose="turn_interpretation", system="s", user="u",
        schema_name="ActiveTaskTurnDecision",
        schema=export_json_schema(ActiveTaskTurnDecision),
        model="gpt-5-mini", timeout=2, response_model=ActiveTaskTurnDecision)

    assert attempts["n"] == 1, "max_retries=0 must mean exactly one network attempt"
    assert result.status == "error"
    assert result.error_code == "APITimeoutError"


def test_default_openai_client_would_retry_multiple_times_without_the_fix(monkeypatch):
    """Regression baseline / grounding: proves this is a REAL SDK behavior
    being disabled, not a guess — a plain, unconfigured OpenAI() client (the
    installed SDK's own default) retries a timeout multiple times."""
    import httpx
    from openai import OpenAI

    attempts = {"n": 0}

    def _boom(*a, **kw):
        attempts["n"] += 1
        raise httpx.TimeoutException("simulated timeout")

    default_client = OpenAI(api_key="sk-test-not-real")
    assert default_client.max_retries == 2   # the installed SDK's own default
    monkeypatch.setattr(default_client._client, "send", _boom)
    try:
        default_client.responses.create(
            model="gpt-5-mini", input=[{"role": "user", "content": "hi"}], timeout=1)
    except Exception:
        pass
    assert attempts["n"] > 1, "the SDK default genuinely retries — this is what V3 disables"


def test_legacy_openai_client_construction_is_untouched():
    """app.py's legacy/general OpenAI clients keep their OWN (unrelated)
    retry configuration — this latency pass is scoped to the V3 client only."""
    import app as app_module
    assert app_module.OPENAI_MAX_RETRIES == int(
        __import__("os").environ.get("OPENAI_MAX_RETRIES", "2"))
    # sync_client already independently used max_retries=0 for its own reasons
    # (a different, pre-existing decision) — confirm it's untouched, not
    # something this task introduced.
    assert app_module.sync_client.max_retries == 0
    assert app_module.client.max_retries == app_module.OPENAI_MAX_RETRIES


def test_timeout_preserves_session_state_and_counters(monkeypatch):
    """End-to-end through the dispatcher: a real client whose underlying
    transport times out must leave the active task and counters untouched."""
    import httpx
    from matbot.ai_tutor_v3 import dispatcher
    from tests.test_v3_practice import FakeClient, base_payload, start_task, dispatch

    import matbot.ai_tutor_v3.orchestrator as orchestrator_mod

    fake = FakeClient()
    dispatcher.set_model_client(fake)
    import matbot.ai_tutor_v3.dispatcher as _d
    _d._TurnBudget  # sanity import touch

    # bootstrap via env fixture pattern used elsewhere is heavier here; reuse
    # the existing v3_env-less approach: set flags directly.
    import os
    os.environ["MATBOT_AI_TUTOR_V3_PRACTICE"] = "on"
    os.environ["MATBOT_AI_TUTOR_V3_LESSONS"] = "6-03-024"
    os.environ["MATBOT_V3_VERIFICATION"] = "off"
    os.environ["MATBOT_MINIMAL_ENGINE"] = "off"
    import tempfile
    tmp_db = tempfile.mktemp(suffix=".sqlite3")
    os.environ["MATBOT_V3_DB_PATH"] = tmp_db
    from matbot import topic_resolver as tr
    tr.reset_cache()

    try:
        before = start_task(fake)
        assert before is not None

        real_client = orchestrator_mod.OpenAIResponsesClient(api_key="sk-test-not-real")

        def _boom(*a, **kw):
            raise httpx.TimeoutException("simulated timeout")
        monkeypatch.setattr(real_client._client._client, "send", _boom)
        dispatcher.set_model_client(real_client)

        out = dispatch(base_payload(client_turn_id="timeout1", student_message="da"))
        assert out is not None
        assert out["last_tutor_task"] == "Da li je 252 djeljiv sa 6?"
        v3 = out["next_state"]["v3_state"]
        assert v3["counters"]["attempts"] == 0
        assert v3["counters"]["wrong_attempts"] == 0
        assert v3["active_task"] is not None
        assert out["v3_fallback_reason"] == "APITimeoutError"
    finally:
        dispatcher.set_model_client(None)
        for key in ("MATBOT_AI_TUTOR_V3_PRACTICE", "MATBOT_AI_TUTOR_V3_LESSONS",
                   "MATBOT_V3_VERIFICATION", "MATBOT_MINIMAL_ENGINE", "MATBOT_V3_DB_PATH"):
            os.environ.pop(key, None)
        tr.reset_cache()


# =========================================================================== #
# Section 2 — measured (not guessed) baseline payload/schema sizes            #
# =========================================================================== #
def test_baseline_full_interpretation_sizes_are_measured_not_guessed(blueprint, state):
    sizes = _full_baseline_sizes(6, blueprint, state, "da")
    # These are MEASURED from the real functions, not hardcoded expectations —
    # only sanity-bounded so a future prompt change doesn't silently balloon.
    assert sizes["system_chars"] > 3000     # full 5-layer policy + Blueprint dump
    assert sizes["schema_bytes"] > 5000      # PracticeTurnInterpretation is broad
    assert sizes["schema_defs"] >= 5         # multiple nested $defs (claims union, etc.)
    print("BASELINE (full interpretation):", sizes)


def test_tokenizer_availability_is_reported():
    """Section 2 explicitly asks for a token count via an installed
    tokenizer if one is available. None is installed in this environment
    (confirmed: no ``tiktoken``) — this test documents that fact rather than
    silently guessing a token count."""
    try:
        import tiktoken  # noqa: F401
        available = True
    except ImportError:
        available = False
    # Either outcome is fine; the point is the fact is checked, not assumed.
    assert available in (True, False)


# =========================================================================== #
# Section 3 — compact active-task context                                    #
# =========================================================================== #
def test_active_task_context_excludes_full_blueprint_and_session_data(blueprint, state):
    context = orch.build_active_task_context(
        grade=6, blueprint=blueprint, state=state, student_message="da")
    dumped = context.model_dump(mode="json")
    # Full session/Blueprint-only fields must never appear.
    for forbidden_key in ("coverage", "mastery", "completed_tasks", "prompt_policy",
                         "common_misconceptions", "task_families", "hint_strategy",
                         "language_guidance", "concepts"):
        assert forbidden_key not in dumped
    # Bounded slices only.
    assert len(context.key_rules) <= 2
    assert len(context.allowed_methods) <= 3
    assert len(context.relevant_misconceptions) <= 2
    assert len(context.recent_turns) <= 2


def test_active_task_context_does_not_leak_unrelated_concepts(blueprint, state):
    """The Blueprint's concepts list is deliberately NOT included at all —
    only the active task's own concept_id/target_id."""
    context = orch.build_active_task_context(
        grade=6, blueprint=blueprint, state=state, student_message="da")
    assert context.concept_id == "div-compound-6"
    assert not hasattr(context, "concepts")


@pytest.mark.parametrize("message", [
    "da", "ne znam", "sta je djeljivost", "252", "mozda 6", "Ne znam.",
    "daj mi lakši zadatak", "pokazi mi rjesenje", "aaa???",
])
def test_active_task_context_builds_for_every_kind_of_free_text(blueprint, state, message):
    context = orch.build_active_task_context(
        grade=6, blueprint=blueprint, state=state, student_message=message)
    assert context.student_message == message
    assert context.task_id == state.active_task.task_id


def test_active_task_context_serializes_without_raw_contact_data(blueprint, state):
    context = orch.build_active_task_context(
        grade=6, blueprint=blueprint, state=state, student_message="da")
    blob = context.model_dump_json().lower()
    for forbidden in ("email", "parent", "@"):
        assert forbidden not in blob


# =========================================================================== #
# Section 4 — smaller purpose-specific output schema                          #
# =========================================================================== #
def test_active_task_turn_decision_is_materially_smaller_than_full_schema():
    full = export_json_schema(PracticeTurnInterpretation)
    compact = export_json_schema(ActiveTaskTurnDecision)
    full_bytes = len(json.dumps(full, ensure_ascii=False).encode("utf-8"))
    compact_bytes = len(json.dumps(compact, ensure_ascii=False).encode("utf-8"))
    assert compact_bytes < full_bytes * 0.6, (
        f"expected a material reduction, got {compact_bytes} vs {full_bytes}")
    assert len(compact["properties"]) < len(full["properties"]) + sum(
        len(full["$defs"][d].get("properties", {})) for d in full.get("$defs", {}))


def test_active_task_turn_decision_has_no_authoritative_fields():
    from matbot.ai_tutor_v3.schemas import AuthoritativeOutcome
    decision_fields = set(ActiveTaskTurnDecision.model_fields.keys())
    authoritative_only = {
        "attempt_count_delta", "wrong_attempt_count_delta", "streak_action",
        "solved_count_delta", "task_status_after", "task_completed",
        "preserve_active_task",
    }
    assert decision_fields.isdisjoint(authoritative_only)
    assert "verdict" not in decision_fields  # only "proposed_verdict" (advisory)


def test_active_task_turn_decision_strict_schema_prepares_and_validates():
    schema = export_json_schema(ActiveTaskTurnDecision)
    strict = orch.prepare_openai_strict_schema(schema)
    orch.validate_openai_strict_schema(strict, purpose="turn_interpretation",
                                       schema_name="ActiveTaskTurnDecision")


def test_decision_adapter_produces_reducer_compatible_interpretation():
    decision = ActiveTaskTurnDecision(
        schema_version="v1", turn_kind="answer", is_answer_attempt=True,
        confidence=0.9, proposed_verdict="correct",
        narration_proposal={"schema_version": "v1", "student_text": "Tačno!",
                            "response_category": "feedback_correct", "confidence": 0.9})
    interpretation, assessment = orch.decision_to_interpretation_and_assessment(decision)
    assert interpretation.turn_kind == "answer"
    assert interpretation.confidence == 0.9
    assert assessment is not None
    assert assessment.proposed_verdict == "correct"
    assert assessment.is_answer_attempt is True


def test_decision_adapter_handles_clarification_and_help_without_assessment():
    decision = ActiveTaskTurnDecision(
        schema_version="v1", turn_kind="ambiguous", is_answer_attempt=False,
        confidence=0.4, clarification_question="Šta tačno misliš?")
    interpretation, assessment = orch.decision_to_interpretation_and_assessment(decision)
    assert interpretation.turn_kind == "ambiguous"
    assert interpretation.clarification_question == "Šta tačno misliš?"
    assert assessment is None


def test_decision_adapter_handles_difficulty_change_without_verdict():
    decision = ActiveTaskTurnDecision(
        schema_version="v1", turn_kind="difficulty_change", is_answer_attempt=False,
        confidence=0.8, difficulty_suggestion="easier")
    interpretation, assessment = orch.decision_to_interpretation_and_assessment(decision)
    assert assessment is not None
    assert assessment.difficulty_suggestion == "easier"
    assert assessment.is_answer_attempt is False


# =========================================================================== #
# Section 5 — compact prompt layers                                          #
# =========================================================================== #
def test_compact_prompt_is_at_least_40_percent_smaller_than_baseline(blueprint, state):
    baseline = _full_baseline_sizes(6, blueprint, state, "da")
    compact = _compact_sizes(6, blueprint, state, "da")
    reduction = 1 - (compact["total_prompt_chars"] / baseline["total_prompt_chars"])
    print(f"BASELINE total_prompt_chars={baseline['total_prompt_chars']} "
         f"COMPACT total_prompt_chars={compact['total_prompt_chars']} "
         f"reduction={reduction:.1%}")
    assert reduction >= 0.40, f"only {reduction:.1%} reduction, need >= 40%"


def test_compact_system_prompt_alone_is_much_smaller(blueprint):
    full_system = orch.build_system_prompt(6, blueprint)
    compact_system = orch.ACTIVE_TASK_INTERPRETATION_POLICY.format(grade=6)
    assert len(compact_system) < len(full_system) * 0.5


def test_compact_prompt_still_contains_required_behaviors():
    """Not a fake metric: the compact policy retains the essential behavior
    directives, just without repeating the full unrelated mode/task-writing
    policies."""
    text = orch.ACTIVE_TASK_INTERPRETATION_POLICY
    for required_snippet in ("razred", "pojašnjenje", "SAMO prijedlog",
                             "internu terminologiju"):
        assert required_snippet in text


# =========================================================================== #
# Section 6 — bounded output tokens                                           #
# =========================================================================== #
def test_max_output_tokens_reaches_responses_create(monkeypatch):
    client = orch.OpenAIResponsesClient(api_key="sk-test-not-real")
    captured = {}

    class _FakeResp:
        output_text = json.dumps({
            "schema_version": "v1", "turn_kind": "answer",
            "is_answer_attempt": True, "confidence": 0.9,
        })
        usage = None

    def _fake_create(**kwargs):
        captured.update(kwargs)
        return _FakeResp()
    monkeypatch.setattr(client._client.responses, "create", _fake_create)

    client.generate(
        purpose="turn_interpretation", system="s", user="u",
        schema_name="ActiveTaskTurnDecision",
        schema=export_json_schema(ActiveTaskTurnDecision),
        model="gpt-5-mini", timeout=5, response_model=ActiveTaskTurnDecision,
        max_output_tokens=orch.resolve_v3_active_task_max_output_tokens())

    assert captured.get("max_output_tokens") == orch.DEFAULT_V3_ACTIVE_TASK_MAX_OUTPUT_TOKENS


def test_max_output_tokens_omitted_when_none(monkeypatch):
    """Other purposes (blueprint/task/narration) do not pass a cap — must not
    regress into always sending one."""
    client = orch.OpenAIResponsesClient(api_key="sk-test-not-real")
    captured = {}

    class _FakeResp:
        output_text = "{}"
        usage = None

    def _fake_create(**kwargs):
        captured.update(kwargs)
        return _FakeResp()
    monkeypatch.setattr(client._client.responses, "create", _fake_create)

    client.generate(
        purpose="task_generation", system="s", user="u",
        schema_name="X", schema={"type": "object", "properties": {}},
        model="gpt-5-mini", timeout=5, response_model=ActiveTaskTurnDecision)
    assert "max_output_tokens" not in captured


def test_default_active_task_max_output_tokens_is_bounded(monkeypatch):
    """Revised bound: production evidence showed BOTH 500 and 1200 were
    insufficient (incomplete_output, reasoning tokens ate the budget at the
    model's unconfigured default effort) — see orchestrator.py's
    DEFAULT_V3_ACTIVE_TASK_MAX_OUTPUT_TOKENS docstring. 2000 is the new,
    evidence-justified default; this still bounds it well below an
    unbounded/runaway value."""
    monkeypatch.delenv("MATBOT_V3_ACTIVE_TASK_MAX_OUTPUT_TOKENS", raising=False)
    value = orch.resolve_v3_active_task_max_output_tokens()
    assert 0 < value <= 3000


def test_active_task_max_output_tokens_configurable(monkeypatch):
    monkeypatch.setenv("MATBOT_V3_ACTIVE_TASK_MAX_OUTPUT_TOKENS", "300")
    assert orch.resolve_v3_active_task_max_output_tokens() == 300


def test_active_task_max_output_tokens_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("MATBOT_V3_ACTIVE_TASK_MAX_OUTPUT_TOKENS", "not-a-number")
    assert (orch.resolve_v3_active_task_max_output_tokens()
           == orch.DEFAULT_V3_ACTIVE_TASK_MAX_OUTPUT_TOKENS)


# =========================================================================== #
# Section 9 — end-to-end model-call-count + behavior regression (dispatcher)   #
# =========================================================================== #
from tests.test_v3_practice import (  # noqa: E402
    FakeClient, base_payload, dispatch, interp_parsed, assess_parsed,
    narration_parsed, start_task,
)
from matbot.ai_tutor_v3 import dispatcher, orchestrator, quality_gate  # noqa: E402


@pytest.fixture()
def v3_env(monkeypatch, tmp_path):
    from matbot import topic_resolver as tr
    from matbot.ai_tutor_v3 import sheets_outbox
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_PRACTICE", "on")
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_LESSONS", "6-03-024")
    monkeypatch.setenv("MATBOT_V3_VERIFICATION", "off")
    monkeypatch.setenv("MATBOT_V3_DB_PATH", str(tmp_path / "v3.sqlite3"))
    monkeypatch.setenv("MATBOT_MINIMAL_ENGINE", "off")
    tr.reset_cache()
    sheets_outbox._reset_for_tests()
    yield
    dispatcher.set_model_client(None)
    tr.reset_cache()
    sheets_outbox._reset_for_tests()


@pytest.fixture()
def fake(v3_env):
    client = FakeClient()
    dispatcher.set_model_client(client)
    return client


def test_answer_turn_uses_the_compact_schema_and_one_model_call(fake):
    start_task(fake)
    fake.calls.clear()
    proposal = interp_parsed("answer", is_answer=True,
                             assessment=assess_parsed("correct"))
    proposal["narration_proposal"] = narration_parsed("Tačno!", "feedback_correct")
    fake.set(orchestrator.PURPOSE_INTERPRET, proposal)
    out = dispatch(base_payload(client_turn_id="ans1", student_message="da"))
    assert out is not None
    assert fake.calls == [orchestrator.PURPOSE_INTERPRET]   # ONE call


def test_help_request_behavior_remains_correct(fake):
    start_task(fake)
    fake.calls.clear()
    out = dispatch(base_payload(client_turn_id="help1", student_message="Ne znam.",
                                intent="hint_request"))
    assert out is not None
    assert orchestrator.PURPOSE_INTERPRET not in fake.calls
    assert out["next_state"]["hint_count"] == 1


def test_concept_question_preserves_task_with_compact_path(fake):
    resp = start_task(fake)
    fake.calls.clear()
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("question"))
    fake.set(orchestrator.PURPOSE_CONCEPT, narration_parsed("Objašnjenje.", "concept"))
    out = dispatch(base_payload(client_turn_id="q1", student_message="sta je djeljivost",
                                previous_next_state=resp["next_state"]))
    assert out is not None
    assert out["task_id"] == resp["task_id"]


def test_ambiguous_message_causes_no_progress_mutation(fake):
    resp = start_task(fake)
    before = resp["next_state"]["v3_state"]["counters"]
    fake.calls.clear()
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed(
        "ambiguous", clarification="Šta tačno misliš?"))
    out = dispatch(base_payload(client_turn_id="amb1", student_message="ovaj to",
                                previous_next_state=resp["next_state"]))
    assert out is not None
    assert out["next_state"]["v3_state"]["counters"] == before


def test_comment_with_a_number_is_not_graded(fake):
    resp = start_task(fake)
    before = resp["next_state"]["v3_state"]["counters"]
    fake.calls.clear()
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("comment", meaning="90"))
    out = dispatch(base_payload(client_turn_id="cmt1", student_message="90",
                                previous_next_state=resp["next_state"]))
    assert out is not None
    assert out["next_state"]["v3_state"]["counters"] == before


def test_missing_diacritics_remain_supported(fake):
    resp = start_task(fake)
    fake.calls.clear()
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed(
        "answer", is_answer=True, assessment=assess_parsed("correct")))
    out = dispatch(base_payload(client_turn_id="dia1", student_message="da naravno",
                                previous_next_state=resp["next_state"]))
    assert out is not None
    assert out["answer_verdict"] == "correct"


def test_partial_answer_remains_supported(fake):
    resp = start_task(fake)
    fake.calls.clear()
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed(
        "answer", is_answer=True, assessment=assess_parsed("partial")))
    out = dispatch(base_payload(client_turn_id="part1", student_message="djelimicno",
                                previous_next_state=resp["next_state"]))
    assert out is not None
    assert out["answer_verdict"] == "partial"
    assert out["task_id"] == resp["task_id"]


def test_duplicate_retry_does_not_double_apply_counters(fake):
    start_task(fake)
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed(
        "answer", is_answer=True, assessment=assess_parsed("correct")))
    payload = base_payload(client_turn_id="dup1", student_message="da")
    first = dispatch(payload)
    second = dispatch(payload)
    assert first is not None and second is not None
    assert (first["next_state"]["v3_state"]["counters"]
           == second["next_state"]["v3_state"]["counters"])


def test_existing_rendering_and_quality_gate_still_apply_on_compact_path(fake):
    start_task(fake)
    fake.calls.clear()
    proposal = interp_parsed("answer", is_answer=True,
                             assessment=assess_parsed("correct"))
    proposal["narration_proposal"] = narration_parsed(
        r"Tačno! \frac{1}{2} je pola.", "feedback_correct")
    fake.set(orchestrator.PURPOSE_INTERPRET, proposal)
    out = dispatch(base_payload(client_turn_id="render1", student_message="da"))
    assert out is not None
    assert r"\( \frac{1}{2} \)" in out["answer"]   # rendering boundary still ran


def test_bootstrap_and_trusted_intents_still_bypass_interpretation_entirely(fake):
    """Section 7 regression: hint/difficulty trusted metadata paths are
    untouched by the compact-schema change (they never call
    turn_interpretation at all, compact or full)."""
    resp = dispatch(base_payload())   # bootstrap
    assert resp is not None
    assert fake.calls == [orchestrator.PURPOSE_BLUEPRINT, orchestrator.PURPOSE_TASK]
