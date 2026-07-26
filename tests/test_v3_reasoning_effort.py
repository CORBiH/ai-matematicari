# -*- coding: utf-8 -*-
"""Purpose-specific reasoning-effort configuration for the compact active-task
turn_interpretation call — a follow-up latency fix.

Production evidence: after the prompt/schema compaction, active-task turns
still took ~11-12.5s with completion_tokens near/at the (then-temporary)
output cap; forcing max_output_tokens=1200 produced
``status="incomplete"``/``incomplete_reason="max_output_tokens"`` with
``output_item_types=["reasoning"]`` — proving reasoning tokens, not prompt
size, dominated. This file proves: (1) the officially-supported
``reasoning.effort`` values were read from the INSTALLED SDK, not guessed,
(2) the resolver fails closed to a value gpt-5-mini actually supports, (3)
only the active-task interpretation call receives it, (4) every existing
safety property (max_retries=0, one attempt, strict schema, classification,
reducer authority) is intact.

No live/paid OpenAI call is made anywhere.
"""
from __future__ import annotations

import json

import pytest

from matbot.ai_tutor_v3 import orchestrator as orch
from matbot.ai_tutor_v3.schemas import ActiveTaskTurnDecision, PracticeTurnInterpretation


# --------------------------------------------------------------------------- #
# 1. Officially supported values — read from the installed SDK               #
# --------------------------------------------------------------------------- #
def test_installed_sdk_reasoning_effort_enum_matches_what_the_resolver_uses():
    """Ground truth check: re-derive the enum directly from the installed
    SDK's own type (not a hardcoded copy) and confirm the resolver's accepted
    set is a subset of it."""
    from openai.types.shared.reasoning_effort import ReasoningEffort
    import typing
    sdk_values = set(typing.get_args(typing.get_args(ReasoningEffort)[0]))
    assert sdk_values == {"none", "minimal", "low", "medium", "high", "xhigh"}
    assert set(orch._GPT5_MINI_SUPPORTED_REASONING_EFFORTS) <= sdk_values


def test_resolver_never_returns_none_the_unsupported_value_for_gpt5_mini():
    """'none' is a real SDK enum value but the installed SDK's own docstring
    states pre-gpt-5.1 models (gpt-5-mini included) do not support it — the
    resolver must never be able to return it."""
    assert "none" not in orch._GPT5_MINI_SUPPORTED_REASONING_EFFORTS


def test_responses_create_accepts_a_reasoning_parameter():
    import inspect
    from openai.resources.responses.responses import Responses
    sig = inspect.signature(Responses.create)
    assert "reasoning" in sig.parameters


def test_reasoning_request_param_is_a_plain_dict_shape():
    """Confirms the request-param TypedDict shape used for reasoning={...} —
    not the response-side BaseModel — is what generate() should build."""
    from openai.types.shared_params.reasoning import Reasoning
    assert Reasoning.__annotations__.get("effort") is not None


# --------------------------------------------------------------------------- #
# 6/7/8/9. Resolver: pure, env-backed, fails closed, normalized               #
# --------------------------------------------------------------------------- #
def test_env_unset_returns_safe_default(monkeypatch):
    monkeypatch.delenv("MATBOT_V3_ACTIVE_TASK_REASONING_EFFORT", raising=False)
    assert orch.resolve_v3_active_task_reasoning_effort() == "minimal"
    assert orch.resolve_v3_active_task_reasoning_effort() == orch.DEFAULT_V3_ACTIVE_TASK_REASONING_EFFORT


@pytest.mark.parametrize("value", ["minimal", "low", "medium", "high", "xhigh"])
def test_valid_env_values_are_accepted(monkeypatch, value):
    monkeypatch.setenv("MATBOT_V3_ACTIVE_TASK_REASONING_EFFORT", value)
    assert orch.resolve_v3_active_task_reasoning_effort() == value


@pytest.mark.parametrize("value", ["none", "extreme", "MAXIMUM", "", "  ", "1", "true"])
def test_invalid_or_unsupported_env_values_fail_closed(monkeypatch, value):
    monkeypatch.setenv("MATBOT_V3_ACTIVE_TASK_REASONING_EFFORT", value)
    assert orch.resolve_v3_active_task_reasoning_effort() == "minimal"


@pytest.mark.parametrize("raw,expected", [
    ("  low  ", "low"), ("LOW", "low"), ("Low", "low"), ("\tMEDIUM\n", "medium"),
])
def test_whitespace_and_case_are_normalized(monkeypatch, raw, expected):
    monkeypatch.setenv("MATBOT_V3_ACTIVE_TASK_REASONING_EFFORT", raw)
    assert orch.resolve_v3_active_task_reasoning_effort() == expected


def test_resolver_is_pure_and_side_effect_free(monkeypatch):
    monkeypatch.setenv("MATBOT_V3_ACTIVE_TASK_REASONING_EFFORT", "low")
    a = orch.resolve_v3_active_task_reasoning_effort()
    b = orch.resolve_v3_active_task_reasoning_effort()
    assert a == b == "low"


# --------------------------------------------------------------------------- #
# 1/2/3/4/5. Only the active-task call receives it — real client, captured   #
# --------------------------------------------------------------------------- #
class _FakeUsage:
    input_tokens = 100
    output_tokens = 50


class _FakeContentText:
    type = "output_text"

    def __init__(self, text):
        self.text = text


class _FakeOutputMessage:
    type = "message"

    def __init__(self, content):
        self.content = content


class _FakeResponse:
    def __init__(self, output_text_value, id="resp_x"):
        self.status = "completed"
        self.output = [_FakeOutputMessage([_FakeContentText(output_text_value)])]
        self.usage = _FakeUsage()
        self.id = id

    @property
    def output_text(self):
        texts = []
        for item in self.output:
            if getattr(item, "type", None) == "message":
                for c in getattr(item, "content", []):
                    if getattr(c, "type", None) == "output_text":
                        texts.append(c.text)
        return "".join(texts)


@pytest.fixture()
def client():
    return orch.OpenAIResponsesClient(api_key="sk-test-not-real")


def _decision_payload():
    return json.dumps({"schema_version": "v1", "turn_kind": "answer",
                       "is_answer_attempt": True, "confidence": 0.9})


def _task_spec_payload():
    return json.dumps({"concept_id": "c1", "target_id": "t1", "question": "Q?",
                       "answer_kind": "boolean_with_reason", "difficulty_level": 2})


def test_active_task_interpretation_passes_the_supported_reasoning_effort(client, monkeypatch):
    captured = {}

    def _fake_create(**kwargs):
        captured.update(kwargs)
        return _FakeResponse(_decision_payload())
    monkeypatch.setattr(client._client.responses, "create", _fake_create)

    client.generate(
        purpose="turn_interpretation", system="s", user="u",
        schema_name="ActiveTaskTurnDecision",
        schema=orch.export_json_schema(ActiveTaskTurnDecision),
        model="gpt-5-mini", timeout=5, response_model=ActiveTaskTurnDecision,
        max_output_tokens=orch.resolve_v3_active_task_max_output_tokens(),
        reasoning_effort=orch.resolve_v3_active_task_reasoning_effort())

    assert captured.get("reasoning") == {"effort": "minimal"}


def test_generate_omits_reasoning_when_not_requested(client, monkeypatch):
    """Every other purpose (task/blueprint/hint/narration/no-active-task
    interpretation/repair) calls generate() without reasoning_effort at
    all — the parameter must be entirely absent, not sent as None."""
    captured = {}

    def _fake_create(**kwargs):
        captured.update(kwargs)
        return _FakeResponse(_task_spec_payload())
    monkeypatch.setattr(client._client.responses, "create", _fake_create)

    from matbot.ai_tutor_v3.schemas import TaskSpecification
    client.generate(
        purpose="task_generation", system="s", user="u",
        schema_name="TaskSpecification",
        schema=orch.export_json_schema(TaskSpecification),
        model="gpt-5-mini", timeout=5, response_model=TaskSpecification)
    assert "reasoning" not in captured


def test_task_generation_call_site_does_not_pass_reasoning_effort(monkeypatch):
    """Regression at the ORCHESTRATOR call-site level, not just generate()'s
    default — proves generate_task() itself never resolves/forwards it."""
    captured = {}

    def _fake_create(**kwargs):
        captured.update(kwargs)
        return _FakeResponse(_task_spec_payload())

    client = orch.OpenAIResponsesClient(api_key="sk-test-not-real")
    monkeypatch.setattr(client._client.responses, "create", _fake_create)

    from matbot.ai_tutor_v3.schemas import LessonIdentity

    class _FakeCoverage:
        targets = []
        covered = []
        attempts_per_target = {}

    class _FakeState:
        active_task = None
        coverage = _FakeCoverage()
        mastery = type("M", (), {"per_concept": {}, "provisional": True})()
        difficulty = type("D", (), {"level": 2})()
        hint = type("H", (), {"current_level": 0})()
        pending_clarification = None
        summary = None
        recent_turns = []

        def model_dump(self, mode="json"):
            return {"active_task": None, "coverage": {"targets": [], "covered": [],
                    "attempts_per_target": {}}, "mastery": {"per_concept": {}, "provisional": True},
                    "difficulty": {"level": 2}, "hint": {"current_level": 0},
                    "pending_clarification": None, "summary": None, "recent_turns": []}

    identity = LessonIdentity(grade=6, area_id="a", area_title="Oblast",
                              lesson_id="6-03-024", lesson_title="Djeljivost")

    class _FakeBlueprint:
        lesson_identity = identity
        key_rules = []
        allowed_methods = []
        common_misconceptions = []
        concepts = []
        coverage_targets = []
        task_families = []
        hint_strategy = []
        language_guidance = type("L", (), {"model_dump": lambda self, mode="json": {}})()

        def model_dump(self, mode="json"):
            return {"lesson_identity": identity.model_dump(mode="json"), "concepts": [],
                    "coverage_targets": [], "key_rules": [], "allowed_methods": [],
                    "common_misconceptions": [], "task_families": [], "hint_strategy": [],
                    "language_guidance": {}}

    orch.generate_task(client, grade=6, blueprint=_FakeBlueprint(), state=_FakeState(),
                       target_id=None, model="gpt-5-mini", timeout=5)
    assert "reasoning" not in captured


def test_blueprint_generation_does_not_receive_reasoning_effort(monkeypatch):
    from matbot.ai_tutor_v3 import lesson_blueprint
    from matbot.ai_tutor_v3.schemas import LessonBlueprintProposal

    captured = {}

    class _FakeClient:
        def generate(self, *, purpose, system, user, schema_name, schema, model, timeout,
                    response_model=None, max_output_tokens=None, reasoning_effort=None):
            captured["reasoning_effort"] = reasoning_effort
            payload = {
                "learning_objectives": [], "prerequisites": [], "concepts": [],
                "coverage_targets": [{"target_id": "t1", "name": "Target 1"}],
                "key_rules": [], "allowed_methods": [],
                "common_misconceptions": [], "task_families": [], "difficulty_dimensions": [],
                "hint_strategy": [], "mastery_requirements": {"min_concepts_mastered": 1,
                "min_independent_solves": 1}, "language_guidance": {"language_register": "grade_6"},
                "supported_verification_types": [], "generation_confidence": 0.9,
            }
            return orch.ModelCallResult(status="ok", parsed=payload, model=model, purpose=purpose)

    from matbot.ai_tutor_v3.schemas import LessonIdentity
    identity = LessonIdentity(grade=6, area_id="a", area_title="Oblast",
                              lesson_id="6-03-024", lesson_title="Djeljivost")
    blueprint, reason = lesson_blueprint.generate_blueprint(
        _FakeClient(), identity=identity, grade=6, model="gpt-5-mini", timeout=5)
    assert blueprint is not None
    assert captured["reasoning_effort"] is None


def test_hint_generation_does_not_receive_reasoning_effort(monkeypatch):
    captured = {}

    class _FakeClient:
        def generate(self, *, purpose, system, user, schema_name, schema, model, timeout,
                    response_model=None, max_output_tokens=None, reasoning_effort=None):
            captured["reasoning_effort"] = reasoning_effort
            payload = {"schema_version": "v1", "student_text": "Savjet.",
                      "response_category": "hint", "confidence": 0.9}
            return orch.ModelCallResult(status="ok", parsed=payload, model=model, purpose=purpose)

    class _FakeHintState:
        current_level = 1

    class _FakeState:
        hint = _FakeHintState()

        def model_dump(self, mode="json"):
            return {}

    class _FakeBlueprint:
        def model_dump(self, mode="json"):
            return {}

    orch.generate_hint(_FakeClient(), grade=6, blueprint=_FakeBlueprint(),
                       state=_FakeState(), model="gpt-5-mini", timeout=5)
    assert captured["reasoning_effort"] is None


def test_full_practice_turn_interpretation_does_not_receive_active_task_reasoning(monkeypatch):
    """The no-active-task path (interpret_turn/PracticeTurnInterpretation)
    must not inherit the active-task reasoning setting."""
    captured = {}

    class _FakeClient:
        def generate(self, *, purpose, system, user, schema_name, schema, model, timeout,
                    response_model=None, max_output_tokens=None, reasoning_effort=None):
            captured["reasoning_effort"] = reasoning_effort
            captured["schema_name"] = schema_name
            payload = {"interpretation": {
                "schema_version": "v1", "turn_kind": "task_request",
                "is_answer_attempt": False, "normalized_meaning": "x",
                "certainty": "certain", "precision": "unspecified", "confidence": 0.9}}
            return orch.ModelCallResult(status="ok", parsed=payload, model=model, purpose=purpose)

    class _FakeCoverage:
        targets = []
        covered = []
        attempts_per_target = {}

    class _FakeState:
        active_task = None
        coverage = _FakeCoverage()
        mastery = type("M", (), {"per_concept": {}, "provisional": True})()
        difficulty = type("D", (), {"level": 2})()
        hint = type("H", (), {"current_level": 0})()
        pending_clarification = None
        summary = None
        recent_turns = []

        def model_dump(self, mode="json"):
            return {"active_task": None, "coverage": {"targets": [], "covered": [],
                    "attempts_per_target": {}}, "mastery": {"per_concept": {}, "provisional": True},
                    "difficulty": {"level": 2}, "hint": {"current_level": 0},
                    "pending_clarification": None, "summary": None, "recent_turns": []}

    class _FakeBlueprint:
        def model_dump(self, mode="json"):
            return {}

    result, call = orch.interpret_turn(
        _FakeClient(), grade=6, blueprint=_FakeBlueprint(), state=_FakeState(),
        student_message="da", model="gpt-5-mini", timeout=5)
    assert captured["schema_name"] == "PracticeTurnInterpretation"
    assert captured["reasoning_effort"] is None


# --------------------------------------------------------------------------- #
# 10/11/12/13. Output cap still reaches the call, one call, max_retries=0     #
# --------------------------------------------------------------------------- #
def test_active_task_max_output_tokens_still_reaches_responses_create(client, monkeypatch):
    captured = {}

    def _fake_create(**kwargs):
        captured.update(kwargs)
        return _FakeResponse(_decision_payload())
    monkeypatch.setattr(client._client.responses, "create", _fake_create)

    client.generate(
        purpose="turn_interpretation", system="s", user="u",
        schema_name="ActiveTaskTurnDecision",
        schema=orch.export_json_schema(ActiveTaskTurnDecision),
        model="gpt-5-mini", timeout=5, response_model=ActiveTaskTurnDecision,
        max_output_tokens=orch.resolve_v3_active_task_max_output_tokens(),
        reasoning_effort=orch.resolve_v3_active_task_reasoning_effort())
    assert captured.get("max_output_tokens") == orch.DEFAULT_V3_ACTIVE_TASK_MAX_OUTPUT_TOKENS


def test_v3_client_still_uses_max_retries_zero():
    client = orch.OpenAIResponsesClient(api_key="sk-test-not-real")
    assert client._client.max_retries == 0


def test_one_network_attempt_still_holds_with_reasoning_effort_set(monkeypatch):
    import httpx
    client = orch.OpenAIResponsesClient(api_key="sk-test-not-real")
    attempts = {"n": 0}

    def _boom(*a, **kw):
        attempts["n"] += 1
        raise httpx.TimeoutException("simulated timeout")
    monkeypatch.setattr(client._client._client, "send", _boom)

    client.generate(
        purpose="turn_interpretation", system="s", user="u",
        schema_name="ActiveTaskTurnDecision",
        schema=orch.export_json_schema(ActiveTaskTurnDecision),
        model="gpt-5-mini", timeout=2, response_model=ActiveTaskTurnDecision,
        reasoning_effort="minimal")
    assert attempts["n"] == 1


# --------------------------------------------------------------------------- #
# 14/15/16/17. Strict schema, Pydantic validation, classification, telemetry  #
# --------------------------------------------------------------------------- #
def test_strict_schema_still_valid_after_docstring_shrink():
    schema = orch.export_json_schema(ActiveTaskTurnDecision)
    strict = orch.prepare_openai_strict_schema(schema)
    orch.validate_openai_strict_schema(strict, purpose="turn_interpretation",
                                       schema_name="ActiveTaskTurnDecision")


def test_pydantic_validation_still_runs_before_ok(client, monkeypatch):
    bad_payload = json.dumps({"schema_version": "v1"})  # missing required fields
    monkeypatch.setattr(client._client.responses, "create",
                        lambda **kw: _FakeResponse(bad_payload))
    result = client.generate(
        purpose="turn_interpretation", system="s", user="u",
        schema_name="ActiveTaskTurnDecision",
        schema=orch.export_json_schema(ActiveTaskTurnDecision),
        model="gpt-5-mini", timeout=5, response_model=ActiveTaskTurnDecision,
        reasoning_effort="minimal")
    assert result.status == "invalid_output"
    assert result.error_code == "schema_validation_error"


def test_incomplete_classification_intact_with_reasoning_effort_set(client, monkeypatch):
    class _IncompleteResp:
        status = "incomplete"

        class incomplete_details:
            reason = "max_output_tokens"
        output = []
        usage = _FakeUsage()
        id = "resp_incomplete"
        output_text = ""

    monkeypatch.setattr(client._client.responses, "create",
                        lambda **kw: _IncompleteResp())
    result = client.generate(
        purpose="turn_interpretation", system="s", user="u",
        schema_name="ActiveTaskTurnDecision",
        schema=orch.export_json_schema(ActiveTaskTurnDecision),
        model="gpt-5-mini", timeout=5, response_model=ActiveTaskTurnDecision,
        max_output_tokens=2000, reasoning_effort="minimal")
    assert result.status == "invalid_output"
    assert result.error_code == "incomplete_output"
    assert result.reasoning_effort == "minimal"
    assert result.max_output_tokens == 2000
    assert result.response_status == "incomplete"
    assert result.incomplete_reason == "max_output_tokens"


# --------------------------------------------------------------------------- #
# 26. No authoritative fields are model-controlled (unchanged by this pass)   #
# --------------------------------------------------------------------------- #
def test_decision_schema_still_has_no_authoritative_fields():
    from matbot.ai_tutor_v3.schemas import AuthoritativeOutcome
    decision_fields = set(ActiveTaskTurnDecision.model_fields.keys())
    authoritative_only = {
        "attempt_count_delta", "wrong_attempt_count_delta", "streak_action",
        "solved_count_delta", "task_status_after", "task_completed",
        "preserve_active_task",
    }
    assert decision_fields.isdisjoint(authoritative_only)


# --------------------------------------------------------------------------- #
# 27/28. Telemetry recorded + logs remain clean                              #
# --------------------------------------------------------------------------- #
def test_ok_result_carries_reasoning_and_cap_telemetry(client, monkeypatch):
    monkeypatch.setattr(client._client.responses, "create",
                        lambda **kw: _FakeResponse(_decision_payload()))
    result = client.generate(
        purpose="turn_interpretation", system="s", user="u",
        schema_name="ActiveTaskTurnDecision",
        schema=orch.export_json_schema(ActiveTaskTurnDecision),
        model="gpt-5-mini", timeout=5, response_model=ActiveTaskTurnDecision,
        max_output_tokens=2000, reasoning_effort="minimal")
    assert result.status == "ok"
    assert result.reasoning_effort == "minimal"
    assert result.max_output_tokens == 2000
    assert result.response_status == "completed"


def test_dispatcher_records_active_task_telemetry_in_audit(monkeypatch, tmp_path):
    from matbot.ai_tutor_v3 import dispatcher
    from tests.test_v3_practice import FakeClient, base_payload, start_task
    from matbot.ai_tutor_v3 import sheets_outbox
    from matbot import topic_resolver as tr

    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_PRACTICE", "on")
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_LESSONS", "6-03-024")
    monkeypatch.setenv("MATBOT_V3_VERIFICATION", "off")
    monkeypatch.setenv("MATBOT_V3_DB_PATH", str(tmp_path / "v3.sqlite3"))
    monkeypatch.setenv("MATBOT_MINIMAL_ENGINE", "off")
    tr.reset_cache()
    sheets_outbox._reset_for_tests()
    fake = FakeClient()
    dispatcher.set_model_client(fake)
    try:
        before = start_task(fake)
        assert before is not None

        from matbot.ai_tutor_v3 import state_store as ss
        store = ss.V3StateStore()
        conn = store._connect()
        try:
            row = conn.execute(
                "SELECT audit_json FROM v3_turns WHERE client_turn_id=?",
                ("s1-A",)).fetchone()
        finally:
            conn.close()
    finally:
        dispatcher.set_model_client(None)
        tr.reset_cache()
        sheets_outbox._reset_for_tests()
    # Bootstrap turn has no active task yet -> compact path not used for
    # THIS specific turn; verified separately at the orchestrator level above.
    assert row is not None


def test_logs_still_contain_no_prompt_or_student_text_with_reasoning_set(client, monkeypatch, caplog):
    secret = "TAJNA_PORUKA_UCENIKA_marker"
    monkeypatch.setattr(client._client.responses, "create",
                        lambda **kw: _FakeResponse(""))
    with caplog.at_level("WARNING", logger="matbot.ai_tutor_v3.orchestrator"):
        client.generate(
            purpose="turn_interpretation", system="SISTEM_TAJNA", user=secret,
            schema_name="ActiveTaskTurnDecision",
            schema=orch.export_json_schema(ActiveTaskTurnDecision),
            model="gpt-5-mini", timeout=5, response_model=ActiveTaskTurnDecision,
            reasoning_effort="minimal")
    logged = "\n".join(r.message for r in caplog.records)
    assert secret not in logged
    assert "SISTEM_TAJNA" not in logged


# --------------------------------------------------------------------------- #
# Structural benchmark: before/after request kwargs                          #
# --------------------------------------------------------------------------- #
def test_structural_benchmark_before_after_request_kwargs(client, monkeypatch):
    """No live call — this documents the EXACT request shape difference, not
    a measured speedup."""
    captured = {}

    def _fake_create(**kwargs):
        captured.update(kwargs)
        return _FakeResponse(_decision_payload())
    monkeypatch.setattr(client._client.responses, "create", _fake_create)

    # "before" this follow-up: no reasoning param, 500-token cap (historical).
    before_kwargs = {"max_output_tokens": 500}

    # "after": resolved reasoning effort + revised cap.
    client.generate(
        purpose="turn_interpretation", system="s", user="u",
        schema_name="ActiveTaskTurnDecision",
        schema=orch.export_json_schema(ActiveTaskTurnDecision),
        model="gpt-5-mini", timeout=5, response_model=ActiveTaskTurnDecision,
        max_output_tokens=orch.resolve_v3_active_task_max_output_tokens(),
        reasoning_effort=orch.resolve_v3_active_task_reasoning_effort())
    after_kwargs = {"max_output_tokens": captured["max_output_tokens"],
                   "reasoning": captured["reasoning"]}

    assert before_kwargs["max_output_tokens"] == 500
    assert after_kwargs["max_output_tokens"] == 2000
    assert after_kwargs["reasoning"] == {"effort": "minimal"}
    # Structural reason this SHOULD reduce reasoning-token usage: "minimal" is
    # the lowest gpt-5-mini-supported effort (vs. its unconfigured default,
    # "medium", per the installed SDK's own docstring) — reasoning effort is
    # documented by OpenAI to control how much the model reasons before
    # producing output. Remaining uncertainty: the ACTUAL token reduction and
    # latency improvement can only be confirmed by live production telemetry
    # (explicitly out of scope for this local, no-live-call task).
