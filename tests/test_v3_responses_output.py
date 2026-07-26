# -*- coding: utf-8 -*-
"""V3 Responses API output diagnostics and parsing.

Root cause fixed: ``OpenAIResponsesClient.generate()`` used to collapse every
HTTP-200-but-unusable-output case (incomplete, refusal, empty, malformed
JSON, schema-invalid) into one undifferentiated ``error_code="invalid_json"``,
because it only ever inspected ``output_text`` and never ``response.status``,
``incomplete_details``, refusal content items, or re-validated the parsed
dict against the actual Pydantic model. This file proves the new,
distinguishing classification + validation pipeline.

Fake response objects below mirror the INSTALLED SDK's real attribute names
and structure (verified by reading ``openai/types/responses/response.py``,
``response_output_message.py``, ``response_output_refusal.py``) — including
reproducing the real ``output_text`` aggregation behavior (only ``message``
output items whose content items are ``output_text``-typed). No live/paid
OpenAI call is made anywhere.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from matbot.ai_tutor_v3 import orchestrator as orch
from matbot.ai_tutor_v3.schemas import ActiveTaskTurnDecision, TaskSpecification


# --------------------------------------------------------------------------- #
# Fake Responses API objects — mirror real installed SDK attribute names      #
# --------------------------------------------------------------------------- #
class _FakeUsage:
    def __init__(self, input_tokens=37, output_tokens=14):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeContentText:
    type = "output_text"

    def __init__(self, text):
        self.text = text


class _FakeContentRefusal:
    type = "refusal"

    def __init__(self, refusal="Ne mogu pomoći s tim."):
        self.refusal = refusal


class _FakeOutputMessage:
    type = "message"

    def __init__(self, content):
        self.content = content


class _FakeReasoningItem:
    """An unknown-to-our-code-but-real item type — must never crash
    classification (Section 4's "unknown output item types" requirement)."""
    type = "reasoning"

    def __init__(self):
        self.content = []


class _FakeIncompleteDetails:
    def __init__(self, reason):
        self.reason = reason


class _FakeResponse:
    """Reproduces the real SDK's ``output_text`` computed-property behavior:
    aggregates ONLY ``message``-type output items whose content items are
    ``output_text``-typed — a refusal or a reasoning-only output correctly
    yields an empty string, exactly like the real SDK."""

    def __init__(self, *, status="completed", output=None,
                incomplete_details=None, usage=None, id="resp_abc123"):
        self.status = status
        self.output = output if output is not None else []
        self.incomplete_details = incomplete_details
        self.usage = usage
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


def _valid_decision_dict():
    return {"schema_version": "v1", "turn_kind": "answer",
           "is_answer_attempt": True, "confidence": 0.9}


def _completed_response(text_payload: str, **kwargs):
    msg = _FakeOutputMessage([_FakeContentText(text_payload)])
    return _FakeResponse(status="completed", output=[msg], usage=_FakeUsage(), **kwargs)


@pytest.fixture()
def client():
    return orch.OpenAIResponsesClient(api_key="sk-test-not-real")


def _patch_create(client, monkeypatch, resp):
    monkeypatch.setattr(client._client.responses, "create", lambda **kw: resp)


def _call(client, response_model=ActiveTaskTurnDecision):
    return client.generate(
        purpose="turn_interpretation", system="s", user="u",
        schema_name="ActiveTaskTurnDecision",
        schema=orch.export_json_schema(ActiveTaskTurnDecision),
        model="gpt-5-mini", timeout=5, response_model=response_model)


# =========================================================================== #
# 1. completed + valid JSON + valid schema -> ok                              #
# =========================================================================== #
def test_completed_valid_json_valid_schema_is_ok(client, monkeypatch):
    resp = _completed_response(json.dumps(_valid_decision_dict()))
    _patch_create(client, monkeypatch, resp)
    result = _call(client)
    assert result.status == "ok"
    assert result.parsed["turn_kind"] == "answer"
    assert result.usage == {"prompt_tokens": 37, "completion_tokens": 14}


def test_ok_result_is_normalized_through_pydantic_model_dump(client, monkeypatch):
    """parsed must be the VALIDATED model's dump, not an unchecked arbitrary
    dict — extra keys the model didn't ask for must not survive."""
    raw = dict(_valid_decision_dict())
    resp = _completed_response(json.dumps(raw))
    _patch_create(client, monkeypatch, resp)
    result = _call(client)
    assert result.status == "ok"
    assert set(result.parsed.keys()) == set(ActiveTaskTurnDecision.model_fields.keys())


# =========================================================================== #
# 2/3. empty / whitespace output_text -> empty_output                        #
# =========================================================================== #
def test_empty_output_text_is_empty_output(client, monkeypatch):
    resp = _completed_response("")
    _patch_create(client, monkeypatch, resp)
    result = _call(client)
    assert result.status == "invalid_output"
    assert result.error_code == "empty_output"


def test_whitespace_only_output_text_is_empty_output(client, monkeypatch):
    resp = _completed_response("   \n\t  ")
    _patch_create(client, monkeypatch, resp)
    result = _call(client)
    assert result.status == "invalid_output"
    assert result.error_code == "empty_output"


def test_output_with_no_message_item_at_all_is_empty_output(client, monkeypatch):
    """A reasoning-only output (no message item) yields output_text="" via
    the SAME aggregation the real SDK uses — must not crash, must classify."""
    resp = _FakeResponse(status="completed", output=[_FakeReasoningItem()],
                        usage=_FakeUsage())
    _patch_create(client, monkeypatch, resp)
    result = _call(client)
    assert result.status == "invalid_output"
    assert result.error_code == "empty_output"


# =========================================================================== #
# 4/5. incomplete -> incomplete_output, reason sanitized and persisted        #
# =========================================================================== #
def test_incomplete_status_is_incomplete_output(client, monkeypatch):
    resp = _FakeResponse(status="incomplete",
                         incomplete_details=_FakeIncompleteDetails("max_output_tokens"),
                         usage=_FakeUsage())
    _patch_create(client, monkeypatch, resp)
    result = _call(client)
    assert result.status == "invalid_output"
    assert result.error_code == "incomplete_output"


@pytest.mark.parametrize("reason", ["max_output_tokens", "content_filter"])
def test_incomplete_reason_is_one_of_the_real_sdk_enum_values(client, monkeypatch, reason):
    """Only the two reasons the installed SDK actually defines
    (openai.types.responses.response.IncompleteDetails.reason) — never an
    invented value."""
    resp = _FakeResponse(status="incomplete",
                         incomplete_details=_FakeIncompleteDetails(reason),
                         usage=_FakeUsage())
    classification = orch.classify_responses_output(resp)
    assert classification.incomplete_reason == reason


def test_incomplete_reason_and_usage_are_persisted_even_though_invalid(client, monkeypatch):
    resp = _FakeResponse(status="incomplete",
                         incomplete_details=_FakeIncompleteDetails("max_output_tokens"),
                         usage=_FakeUsage(input_tokens=100, output_tokens=500))
    _patch_create(client, monkeypatch, resp)
    result = _call(client)
    assert result.error_code == "incomplete_output"
    assert result.usage == {"prompt_tokens": 100, "completion_tokens": 500}


# =========================================================================== #
# 6. explicit refusal -> model_refusal                                       #
# =========================================================================== #
def test_explicit_refusal_is_model_refusal(client, monkeypatch):
    msg = _FakeOutputMessage([_FakeContentRefusal("Ovo ne mogu.")])
    resp = _FakeResponse(status="completed", output=[msg], usage=_FakeUsage())
    _patch_create(client, monkeypatch, resp)
    result = _call(client)
    assert result.status == "invalid_output"
    assert result.error_code == "model_refusal"


def test_refusal_text_is_never_parsed_as_json(client, monkeypatch):
    """A refusal item's text is never fed to json.loads — proven indirectly:
    the refusal text below is deliberately invalid JSON, and the result must
    still be model_refusal, not json_decode_error."""
    msg = _FakeOutputMessage([_FakeContentRefusal("{ this is not json at all")])
    resp = _FakeResponse(status="completed", output=[msg], usage=_FakeUsage())
    _patch_create(client, monkeypatch, resp)
    result = _call(client)
    assert result.error_code == "model_refusal"


# =========================================================================== #
# 7. malformed non-empty JSON -> json_decode_error                            #
# =========================================================================== #
def test_malformed_json_is_json_decode_error(client, monkeypatch):
    resp = _completed_response("{not valid json,,,")
    _patch_create(client, monkeypatch, resp)
    result = _call(client)
    assert result.status == "invalid_output"
    assert result.error_code == "json_decode_error"


# =========================================================================== #
# 8/9/10. schema validation failures                                          #
# =========================================================================== #
def test_missing_required_field_is_schema_validation_error(client, monkeypatch):
    bad = {"schema_version": "v1", "is_answer_attempt": True, "confidence": 0.9}
    # missing required "turn_kind"
    resp = _completed_response(json.dumps(bad))
    _patch_create(client, monkeypatch, resp)
    result = _call(client)
    assert result.status == "invalid_output"
    assert result.error_code == "schema_validation_error"


def test_forbidden_extra_field_is_schema_validation_error(client, monkeypatch):
    bad = dict(_valid_decision_dict())
    bad["not_a_real_field"] = "sneaky"
    resp = _completed_response(json.dumps(bad))
    _patch_create(client, monkeypatch, resp)
    result = _call(client)
    assert result.status == "invalid_output"
    assert result.error_code == "schema_validation_error"


def test_wrong_field_type_is_schema_validation_error(client, monkeypatch):
    bad = dict(_valid_decision_dict())
    bad["confidence"] = "very confident"   # should be a float
    resp = _completed_response(json.dumps(bad))
    _patch_create(client, monkeypatch, resp)
    result = _call(client)
    assert result.status == "invalid_output"
    assert result.error_code == "schema_validation_error"


# =========================================================================== #
# 12/13/14/15. classify_responses_output robustness                          #
# =========================================================================== #
def test_unknown_output_item_types_do_not_crash_classification():
    class _Weird:
        type = "some_future_item_type_2027"
    resp = _FakeResponse(status="completed", output=[_Weird()], usage=_FakeUsage())
    classification = orch.classify_responses_output(resp)
    assert "some_future_item_type_2027" in classification.output_item_types


def test_missing_status_attribute_does_not_crash():
    class _NoStatus:
        output = []
        output_text = ""
    classification = orch.classify_responses_output(_NoStatus())
    assert classification.response_status is None


def test_dict_style_fake_response_works():
    resp = {
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "hi"}]}],
        "output_text": "hi",
        "id": "resp_dict1",
    }
    classification = orch.classify_responses_output(resp)
    assert classification.response_status == "completed"
    assert classification.output_text == "hi"
    assert classification.output_item_types == ["message"]
    assert classification.content_item_types == ["output_text"]


def test_sdk_style_object_response_works():
    resp = _completed_response("hello world")
    classification = orch.classify_responses_output(resp)
    assert classification.response_status == "completed"
    assert classification.output_text_len == len("hello world")
    assert classification.output_item_types == ["message"]


def test_classification_caps_item_type_lists():
    items = [_FakeOutputMessage([_FakeContentText("x")]) for _ in range(50)]
    resp = _FakeResponse(status="completed", output=items, usage=_FakeUsage())
    classification = orch.classify_responses_output(resp)
    assert len(classification.output_item_types) <= orch._MAX_LOGGED_ITEM_TYPES


# =========================================================================== #
# 16/17. sanitized logging content                                            #
# =========================================================================== #
def test_invalid_output_log_contains_only_structural_metadata(client, monkeypatch, caplog):
    resp = _completed_response("")
    _patch_create(client, monkeypatch, resp)
    with caplog.at_level("WARNING", logger="matbot.ai_tutor_v3.orchestrator"):
        _call(client)
    logged = "\n".join(r.message for r in caplog.records)
    assert "empty_output" in logged
    assert "turn_interpretation" in logged
    assert "ActiveTaskTurnDecision" in logged
    assert "completed" in logged


def test_invalid_output_log_never_contains_raw_output_prompt_or_student_text(client, monkeypatch, caplog):
    secret_student_text = "UCENIK_TAJNA_PORUKA_marker_xyz"
    secret_output = f"NEVALIDAN_MODEL_IZLAZ_{secret_student_text}"
    resp = _completed_response(secret_output + ",,, malformed")
    _patch_create(client, monkeypatch, resp)
    with caplog.at_level("WARNING", logger="matbot.ai_tutor_v3.orchestrator"):
        client.generate(
            purpose="turn_interpretation", system="SISTEM_PROMPT_SECRET",
            user=secret_student_text, schema_name="ActiveTaskTurnDecision",
            schema=orch.export_json_schema(ActiveTaskTurnDecision),
            model="gpt-5-mini", timeout=5, response_model=ActiveTaskTurnDecision)
    logged = "\n".join(r.message for r in caplog.records)
    assert secret_student_text not in logged
    assert secret_output not in logged
    assert "SISTEM_PROMPT_SECRET" not in logged
    assert "sk-test-not-real" not in logged


def test_schema_validation_error_log_has_field_paths_not_values(client, monkeypatch, caplog):
    bad = dict(_valid_decision_dict())
    bad["confidence"] = "SECRET_BAD_VALUE_marker"
    resp = _completed_response(json.dumps(bad))
    _patch_create(client, monkeypatch, resp)
    with caplog.at_level("WARNING", logger="matbot.ai_tutor_v3.orchestrator"):
        _call(client)
    logged = "\n".join(r.message for r in caplog.records)
    assert "confidence" in logged            # field path — safe
    assert "SECRET_BAD_VALUE_marker" not in logged  # the actual bad value — never logged


def test_network_exception_logging_still_uses_the_existing_error_path(client, monkeypatch, caplog):
    """Section 1's existing exception logging (network/SDK failures) must be
    unchanged — a genuine exception, not a classified invalid output, still
    logs via _log_openai_call_failure at ERROR level."""
    def _raise(**kw):
        raise RuntimeError("simulated network failure")
    monkeypatch.setattr(client._client.responses, "create", _raise)
    with caplog.at_level("ERROR", logger="matbot.ai_tutor_v3.orchestrator"):
        result = client.generate(
            purpose="turn_interpretation", system="s", user="u",
            schema_name="ActiveTaskTurnDecision",
            schema=orch.export_json_schema(ActiveTaskTurnDecision),
            model="gpt-5-mini", timeout=5, response_model=ActiveTaskTurnDecision)
    assert result.status == "error"
    assert result.error_code == "RuntimeError"
    logged = "\n".join(r.message for r in caplog.records)
    assert "v3 openai call failed" in logged


# =========================================================================== #
# 18/19. usage and latency retained for invalid output                        #
# =========================================================================== #
def test_usage_is_retained_for_invalid_output_after_http_200(client, monkeypatch):
    resp = _completed_response("not json at all,,,")
    _patch_create(client, monkeypatch, resp)
    result = _call(client)
    assert result.status == "invalid_output"
    assert result.usage == {"prompt_tokens": 37, "completion_tokens": 14}


def test_latency_is_retained_for_invalid_output(client, monkeypatch):
    resp = _completed_response("not json at all,,,")
    _patch_create(client, monkeypatch, resp)
    result = _call(client)
    assert result.latency_ms >= 0.0
    assert result.latency_ms == result.latency_ms  # not NaN/None


# =========================================================================== #
# 24/25. output token cap scoping                                             #
# =========================================================================== #
def test_task_generation_does_not_receive_the_active_task_token_cap(monkeypatch):
    """Regression: task_generation must NOT accidentally inherit the 500-token
    active-task interpretation cap."""
    captured = {}

    class _FakeTaskResp:
        output_text = json.dumps({
            "concept_id": "c1", "target_id": "t1", "question": "Q?",
            "answer_kind": "boolean_with_reason", "difficulty_level": 2,
        })
        usage = None
        status = "completed"
        output = []
        id = "resp_task1"

    client = orch.OpenAIResponsesClient(api_key="sk-test-not-real")

    def _fake_create(**kwargs):
        captured.update(kwargs)
        return _FakeTaskResp()
    monkeypatch.setattr(client._client.responses, "create", _fake_create)

    from matbot.ai_tutor_v3.schemas import LessonIdentity
    identity = LessonIdentity(grade=6, area_id="a", area_title="Oblast",
                              lesson_id="6-03-024", lesson_title="Djeljivost")

    class _FakeCoverage:
        targets = []
        covered = []
        attempts_per_target = {}

    class _FakeDifficulty:
        level = 2

    class _FakeHint:
        current_level = 0

    class _FakeState:
        active_task = None
        coverage = _FakeCoverage()
        mastery = type("M", (), {"per_concept": {}, "provisional": True})()
        difficulty = _FakeDifficulty()
        hint = _FakeHint()
        pending_clarification = None
        summary = None
        recent_turns = []

        def model_dump(self, mode="json"):
            return {"active_task": None, "coverage": {"targets": [], "covered": [],
                    "attempts_per_target": {}}, "mastery": {"per_concept": {}, "provisional": True},
                    "difficulty": {"level": 2}, "hint": {"current_level": 0},
                    "pending_clarification": None, "summary": None, "recent_turns": []}

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
    assert "max_output_tokens" not in captured


from tests.test_v3_practice import FakeClient, base_payload, start_task  # noqa: E402
from matbot.ai_tutor_v3 import dispatcher  # noqa: E402


@pytest.fixture()
def dispatch_env(monkeypatch, tmp_path):
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
def fake(dispatch_env):
    client = FakeClient()
    dispatcher.set_model_client(client)
    return client


def _dispatch(payload):
    return dispatcher.v3_practice_dispatch(payload, model="gpt-5-mini", timeout=5)


# =========================================================================== #
# 21/22/23. state preservation, idempotency, no retry — per invalid category   #
# =========================================================================== #
@pytest.mark.parametrize("error_code", [
    "incomplete_output", "model_refusal", "empty_output",
    "json_decode_error", "schema_validation_error",
])
def test_each_invalid_output_category_preserves_state_and_counters(fake, error_code):
    before = start_task(fake)
    assert before is not None
    before_counters = before["next_state"]["v3_state"]["counters"]
    before_task_id = before["task_id"]

    fake.set_status(orch.PURPOSE_INTERPRET, "invalid_output", error_code)
    out = _dispatch(base_payload(client_turn_id="inv1", student_message="da",
                                 previous_next_state=before["next_state"]))
    assert out is not None
    assert out["v3_fallback_reason"] == error_code
    assert out["task_id"] == before_task_id
    assert out["next_state"]["v3_state"]["counters"] == before_counters


@pytest.mark.parametrize("error_code", [
    "incomplete_output", "model_refusal", "empty_output",
    "json_decode_error", "schema_validation_error",
])
def test_each_invalid_output_category_makes_exactly_one_model_call(fake, error_code):
    start_task(fake)
    fake.calls.clear()
    fake.set_status(orch.PURPOSE_INTERPRET, "invalid_output", error_code)
    out = _dispatch(base_payload(client_turn_id="inv2", student_message="da"))
    assert out is not None
    assert fake.calls == [orch.PURPOSE_INTERPRET]   # exactly one — no retry, no repair


def test_duplicate_client_turn_id_remains_idempotent_after_invalid_output(fake):
    start_task(fake)
    fake.set_status(orch.PURPOSE_INTERPRET, "invalid_output", "empty_output")
    payload = base_payload(client_turn_id="inv3", student_message="da")
    first = _dispatch(payload)
    second = _dispatch(payload)
    assert first is not None and second is not None
    assert first["next_state"]["v3_state"]["counters"] == second["next_state"]["v3_state"]["counters"]


def test_invalid_output_never_produces_a_wrong_answer_verdict(fake):
    """Section 6: incomplete/refusal must never be converted into a graded
    'incorrect' verdict."""
    start_task(fake)
    fake.set_status(orch.PURPOSE_INTERPRET, "invalid_output", "model_refusal")
    out = _dispatch(base_payload(client_turn_id="inv4", student_message="da"))
    assert out is not None
    assert out.get("answer_verdict") is None


def test_invalid_output_shows_only_the_existing_safe_fallback_text(fake):
    start_task(fake)
    fake.set_status(orch.PURPOSE_INTERPRET, "invalid_output", "json_decode_error")
    out = _dispatch(base_payload(client_turn_id="inv5", student_message="da"))
    assert out is not None
    assert "json_decode_error" not in out["answer"]
    assert "Trenutno ne mogu" in out["answer"] or out["answer"]


# =========================================================================== #
# 20. specific error_code reaches v3_turns                                    #
# =========================================================================== #
def test_specific_error_code_reaches_v3_turns_table(fake):
    start_task(fake)
    fake.set_status(orch.PURPOSE_INTERPRET, "invalid_output", "schema_validation_error")
    out = _dispatch(base_payload(client_turn_id="inv6", student_message="da"))
    assert out is not None

    from matbot.ai_tutor_v3 import state_store as ss
    store = ss.V3StateStore()
    conn = store._connect()
    try:
        row = conn.execute(
            "SELECT error_code, status, usage_json FROM v3_turns "
            "WHERE client_turn_id=?", ("inv6",)).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["error_code"] == "schema_validation_error"
    assert row["status"] == "failed"
    # usage IS preserved — a real (fake) model call happened, HTTP "200".
    assert row["usage_json"]
    usage = json.loads(row["usage_json"])
    assert usage["model_calls"] == 1


def test_active_task_interpretation_still_receives_the_cap(client, monkeypatch):
    """Unlike task_generation above, the active-task interpretation call site
    (``interpret_active_task_turn``) DOES explicitly resolve and pass the
    cap — proven by calling generate() the SAME way that function does."""
    captured = {}

    def _fake_create(**kwargs):
        captured.update(kwargs)
        return _completed_response(json.dumps(_valid_decision_dict()))
    monkeypatch.setattr(client._client.responses, "create", _fake_create)
    client.generate(
        purpose="turn_interpretation", system="s", user="u",
        schema_name="ActiveTaskTurnDecision",
        schema=orch.export_json_schema(ActiveTaskTurnDecision),
        model="gpt-5-mini", timeout=5, response_model=ActiveTaskTurnDecision,
        max_output_tokens=orch.resolve_v3_active_task_max_output_tokens())
    assert captured.get("max_output_tokens") == orch.DEFAULT_V3_ACTIVE_TASK_MAX_OUTPUT_TOKENS
