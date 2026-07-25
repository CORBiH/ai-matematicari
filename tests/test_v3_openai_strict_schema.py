# -*- coding: utf-8 -*-
"""Production incident: OpenAI POST /v1/responses returned HTTP 400.

Root cause (confirmed by reading ``export_json_schema`` output directly, not
guessed): our raw Pydantic-exported schemas set ``additionalProperties: false``
(true already, from ``extra="forbid"``) but leave every Optional/default-valued
field OUT of ``required`` — e.g. ``TaskSpecification`` has 8 properties but only
5 in ``required``. OpenAI's strict Structured Outputs mode requires EVERY
property name to appear in ``required``, with optionality expressed only via a
nullable type. A schema that violates this is rejected by the API before any
generation happens.

These tests exercise ``orchestrator.prepare_openai_strict_schema`` (the fix)
against the REAL schemas the seven call purposes actually use, plus the safe
error-diagnostics path. No test makes a live OpenAI call — ``FakeClient``/direct
function calls only, and the "fails before any API call" test proves it
structurally by making a real network attempt an assertion failure.
"""
from __future__ import annotations

import json

import pytest

from matbot.ai_tutor_v3 import orchestrator as orch
from matbot.ai_tutor_v3.schemas import (
    LessonBlueprint,
    LessonBlueprintProposal,
    NarrationResult,
    PracticeTurnInterpretation,
    TaskSpecification,
    export_json_schema,
)

# The exact schemas the seven call purposes use (NarrationResult covers
# narration, hint_generation, concept_explanation and solution_reveal — they
# share one schema, see orchestrator._narration_call). ``blueprint_generation``
# uses ``LessonBlueprintProposal`` — the model-owned subset of
# ``LessonBlueprint`` — never the full ``LessonBlueprint`` (which carries
# server-owned fields like ``source_metadata`` the model must never be asked
# to invent; see the production-incident tests further below).
REAL_SCHEMAS = {
    orch.PURPOSE_BLUEPRINT: LessonBlueprintProposal,
    orch.PURPOSE_INTERPRET: PracticeTurnInterpretation,
    orch.PURPOSE_TASK: TaskSpecification,
    orch.PURPOSE_NARRATION: NarrationResult,
    orch.PURPOSE_HINT: NarrationResult,
    orch.PURPOSE_CONCEPT: NarrationResult,
    orch.PURPOSE_REVEAL: NarrationResult,
}


def _walk_objects(node, path=""):
    """Yield every (path, node) whose node is an object schema with properties."""
    if isinstance(node, dict):
        if isinstance(node.get("properties"), dict):
            yield path, node
        for key in ("$defs", "definitions"):
            for name, sub in (node.get(key) or {}).items():
                yield from _walk_objects(sub, f"{path}.{key}.{name}")
        props = node.get("properties")
        if isinstance(props, dict):
            for name, sub in props.items():
                yield from _walk_objects(sub, f"{path}.properties.{name}")
        items = node.get("items")
        if items is not None:
            yield from _walk_objects(items, f"{path}.items")
        for key in ("anyOf", "oneOf", "allOf"):
            for i, sub in enumerate(node.get(key) or []):
                yield from _walk_objects(sub, f"{path}.{key}[{i}]")


# --------------------------------------------------------------------------- #
# The real, currently-used schemas: audited one by one                        #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("purpose,model", REAL_SCHEMAS.items())
def test_real_schema_raw_export_has_missing_required_before_fix(purpose, model):
    """Pin the ACTUAL bug: the raw export (what was sent to OpenAI before this
    fix) has properties absent from required — this is the reproducible 400
    cause, not a guess."""
    raw = export_json_schema(model)
    missing_anywhere = False
    for _, node in _walk_objects(raw):
        props = set(node["properties"].keys())
        required = set(node.get("required") or [])
        if props - required:
            missing_anywhere = True
    # Every one of our real Practice schemas has at least one Optional field
    # somewhere, so the bug is present in all seven purposes.
    assert missing_anywhere, f"{purpose}: expected the raw export to reproduce the gap"


@pytest.mark.parametrize("purpose,model", REAL_SCHEMAS.items())
def test_real_schema_prepared_has_every_property_in_required(purpose, model):
    raw = export_json_schema(model)
    strict = orch.prepare_openai_strict_schema(raw)
    for path, node in _walk_objects(strict):
        props = set(node["properties"].keys())
        required = set(node.get("required") or [])
        assert props == required, f"{purpose} at {path}: required != properties"


@pytest.mark.parametrize("purpose,model", REAL_SCHEMAS.items())
def test_real_schema_prepared_additional_properties_false_recursively(purpose, model):
    raw = export_json_schema(model)
    strict = orch.prepare_openai_strict_schema(raw)
    for path, node in _walk_objects(strict):
        assert node.get("additionalProperties") is False, f"{purpose} at {path}"


@pytest.mark.parametrize("purpose,model", REAL_SCHEMAS.items())
def test_real_schema_prepared_no_oneof_or_discriminator_remains(purpose, model):
    raw = export_json_schema(model)
    strict = orch.prepare_openai_strict_schema(raw)
    blob = json.dumps(strict)
    assert "oneOf" not in blob, purpose
    assert "discriminator" not in blob, purpose


@pytest.mark.parametrize("purpose,model", REAL_SCHEMAS.items())
def test_real_schema_prepared_does_not_mutate_original(purpose, model):
    raw = export_json_schema(model)
    before = json.loads(json.dumps(raw))   # independent deep copy for comparison
    orch.prepare_openai_strict_schema(raw)
    assert raw == before, f"{purpose}: caller's schema was mutated"


@pytest.mark.parametrize("purpose,model", REAL_SCHEMAS.items())
def test_real_schema_prepared_transform_is_idempotent(purpose, model):
    raw = export_json_schema(model)
    once = orch.prepare_openai_strict_schema(raw)
    twice = orch.prepare_openai_strict_schema(once)
    assert once == twice, purpose
    assert json.dumps(once, sort_keys=True) == json.dumps(twice, sort_keys=True)


# --------------------------------------------------------------------------- #
# Optional-vs-required semantics                                              #
# --------------------------------------------------------------------------- #
def test_optional_fields_become_required_but_nullable():
    strict = orch.prepare_openai_strict_schema(export_json_schema(TaskSpecification))
    for optional_field in ("expected_internal", "planned_verification_type", "rationale"):
        assert optional_field in strict["required"]
        field_schema = strict["properties"][optional_field]
        types_present = {branch.get("type") for branch in field_schema.get("anyOf", [])}
        assert "null" in types_present, (
            f"{optional_field} must remain nullable via anyOf null branch")


def test_required_non_null_fields_remain_non_null():
    strict = orch.prepare_openai_strict_schema(export_json_schema(TaskSpecification))
    for genuinely_required in ("concept_id", "target_id", "question", "answer_kind"):
        field_schema = strict["properties"][genuinely_required]
        assert "anyOf" not in field_schema, (
            f"{genuinely_required} was never optional and must not be wrapped nullable")
        assert field_schema.get("type") in ("string",)


def test_nested_defs_are_transformed():
    strict = orch.prepare_openai_strict_schema(export_json_schema(LessonBlueprint))
    concept_def = strict["$defs"]["ConceptBlueprint"]
    assert concept_def["additionalProperties"] is False
    assert set(concept_def["properties"].keys()) == set(concept_def["required"])


def test_arrays_of_nested_objects_are_transformed():
    strict = orch.prepare_openai_strict_schema(export_json_schema(LessonBlueprint))
    concepts_prop = strict["properties"]["concepts"]
    assert concepts_prop["type"] == "array"
    item_ref = concepts_prop["items"]["$ref"]
    def_name = item_ref.rsplit("/", 1)[-1]
    item_def = strict["$defs"][def_name]
    assert item_def["additionalProperties"] is False
    assert set(item_def["properties"].keys()) == set(item_def["required"])


def test_discriminated_union_converts_to_anyof_and_stays_structurally_valid():
    strict = orch.prepare_openai_strict_schema(
        export_json_schema(PracticeTurnInterpretation))
    claim_def = strict["$defs"]["TypedClaim"]
    vreq = claim_def["properties"]["verification_request"]
    # Optional[VerificationRequest]: anyOf[ {anyOf:[variant refs...]}, null ]
    branches = vreq["anyOf"]
    assert any(b.get("type") == "null" for b in branches)
    union_branch = next(b for b in branches if b.get("type") != "null")
    assert "anyOf" in union_branch
    assert "oneOf" not in union_branch and "discriminator" not in union_branch
    variant_refs = [v["$ref"].rsplit("/", 1)[-1] for v in union_branch["anyOf"]]
    assert set(variant_refs) == {
        "RationalEqualityVerificationRequest",
        "DivisibilityVerificationRequest",
        "EquationSubstitutionVerificationRequest",
    }
    for name in variant_refs:
        variant_def = strict["$defs"][name]
        assert variant_def["additionalProperties"] is False
        assert set(variant_def["properties"].keys()) == set(variant_def["required"])
        # The discriminating "type" const survives — that's what still proves
        # mutual exclusivity even after dropping the discriminator keyword.
        assert "const" in variant_def["properties"]["type"]


# --------------------------------------------------------------------------- #
# Rejected constructs — fail LOCALLY, never reach the network                  #
# --------------------------------------------------------------------------- #
def test_additional_properties_true_is_rejected():
    with pytest.raises(orch.StrictSchemaError):
        orch.prepare_openai_strict_schema(
            {"type": "object", "properties": {"a": {"type": "string"}},
             "additionalProperties": True})


def test_pattern_properties_is_rejected():
    with pytest.raises(orch.StrictSchemaError):
        orch.prepare_openai_strict_schema(
            {"type": "object", "patternProperties": {"^x": {"type": "string"}}})


def test_boolean_schema_node_is_rejected():
    with pytest.raises(orch.StrictSchemaError):
        orch.prepare_openai_strict_schema(True)


def test_non_disjoint_discriminated_union_is_rejected():
    schema = {
        "properties": {"u": {
            "discriminator": {"propertyName": "type",
                              "mapping": {"a": "#/$defs/A", "b": "#/$defs/B"}},
            "oneOf": [{"$ref": "#/$defs/A"}, {"$ref": "#/$defs/B"}],
        }},
        "$defs": {
            "A": {"type": "object", "properties": {
                "type": {"const": "dup", "type": "string"},
                "x": {"type": "integer"}}},
            "B": {"type": "object", "properties": {
                "type": {"const": "dup", "type": "string"},
                "y": {"type": "string"}}},
        },
    }
    with pytest.raises(orch.StrictSchemaError):
        orch.prepare_openai_strict_schema(schema)


def test_unsupported_construct_fails_before_any_api_call(monkeypatch):
    """A broken schema must never reach responses.create."""
    client = orch.OpenAIResponsesClient(api_key="sk-test-not-real")

    def _blocked(*args, **kwargs):
        raise AssertionError("must not reach the network for a rejected schema")
    monkeypatch.setattr(client._client.responses, "create", _blocked)

    broken_schema = {"type": "object",
                     "properties": {"a": {"type": "string"}},
                     "additionalProperties": True}
    result = client.generate(
        purpose="turn_interpretation", system="s", user="u",
        schema_name="Broken", schema=broken_schema, model="gpt-5-mini", timeout=5)
    assert result.status == "error"
    assert result.error_code == "strict_schema_incompatible"


# --------------------------------------------------------------------------- #
# Safe, sanitized error diagnostics                                           #
# --------------------------------------------------------------------------- #
def _fake_bad_request_error(message, *, status_code=400, code="invalid_json_schema",
                            request_id="req_abc123"):
    import httpx
    import openai
    req = httpx.Request("POST", "https://api.openai.com/v1/responses")
    resp = httpx.Response(status_code, request=req,
                          headers={"x-request-id": request_id},
                          json={"error": {"message": message,
                                          "type": "invalid_request_error",
                                          "code": code}})
    return openai.BadRequestError(
        message, response=resp,
        body={"message": message, "type": "invalid_request_error", "code": code})


def test_sanitized_logging_contains_status_code_and_request_id_no_secrets(caplog):
    message = ("Invalid schema for response_format 'PracticeTurnInterpretation': "
              "'required' is required to include every key in properties. "
              "Missing 'expected_internal'.")
    exc = _fake_bad_request_error(message)
    with caplog.at_level("ERROR", logger="matbot.ai_tutor_v3.orchestrator"):
        orch._log_openai_call_failure(
            exc, purpose="turn_interpretation", model="gpt-5-mini",
            schema_name="PracticeTurnInterpretation")
    logged = "\n".join(r.message for r in caplog.records)
    assert "400" in logged
    assert "invalid_json_schema" in logged
    assert "req_abc123" in logged
    assert "turn_interpretation" in logged
    assert "PracticeTurnInterpretation" in logged


@pytest.mark.parametrize("secret,pattern", [
    ("sk-abcdEFGH1234567890", "redacted-key"),
    ("roditelj@example.com", "redacted-email"),
])
def test_sanitized_logging_redacts_key_and_email_shaped_substrings(secret, pattern):
    exc = _fake_bad_request_error(f"error near token {secret} in payload")
    sanitized = orch._sanitize_error_message(getattr(exc, "message", str(exc)))
    assert secret not in sanitized
    assert pattern in sanitized


def test_sanitized_logging_never_contains_system_or_user_prompt_markers(caplog):
    """The diagnostic is built ONLY from exception attributes — the system and
    user prompt strings are never passed to the logger at all."""
    system_marker = "TAJNI_SISTEM_PROMPT_MARKER_XYZ"
    user_marker = "UCENIK_TAJNA_PORUKA_MARKER_XYZ"
    exc = _fake_bad_request_error("Invalid schema.")
    with caplog.at_level("ERROR", logger="matbot.ai_tutor_v3.orchestrator"):
        orch._log_openai_call_failure(
            exc, purpose="turn_interpretation", model="gpt-5-mini",
            schema_name="PracticeTurnInterpretation")
    logged = "\n".join(r.message for r in caplog.records)
    assert system_marker not in logged
    assert user_marker not in logged
    assert "sk-" not in logged.lower().replace("redacted", "")


def test_logged_message_is_length_bounded():
    huge = "x" * 5000
    sanitized = orch._sanitize_error_message(huge)
    assert len(sanitized) <= orch._MAX_LOGGED_MESSAGE_CHARS


def test_generate_logs_sanitized_diagnostic_on_api_error(monkeypatch, caplog):
    """End-to-end through OpenAIResponsesClient.generate: a real (fake-raised)
    BadRequestError never reaches the frontend-facing ModelCallResult.error_code
    beyond the stable class name, but the sanitized diagnostic IS logged."""
    client = orch.OpenAIResponsesClient(api_key="sk-test-not-real")
    exc = _fake_bad_request_error("Invalid schema: missing required key.")

    def _raise(*args, **kwargs):
        raise exc
    monkeypatch.setattr(client._client.responses, "create", _raise)

    with caplog.at_level("ERROR", logger="matbot.ai_tutor_v3.orchestrator"):
        result = client.generate(
            purpose="turn_interpretation", system="SISTEM_PROMPT_TEKST", user="UCENIK_PORUKA",
            schema_name="PracticeTurnInterpretation",
            schema=export_json_schema(PracticeTurnInterpretation),
            model="gpt-5-mini", timeout=5)

    assert result.status == "error"
    assert result.error_code == "BadRequestError"     # stable, unchanged shape
    logged = "\n".join(r.message for r in caplog.records)
    assert "400" in logged and "invalid_json_schema" in logged and "req_abc123" in logged
    assert "SISTEM_PROMPT_TEKST" not in logged
    assert "UCENIK_PORUKA" not in logged
    assert "sk-test-not-real" not in logged


# --------------------------------------------------------------------------- #
# Production incident #2: LessonBlueprintProposal excludes server-owned       #
# fields (including 'source_metadata') from the schema sent to the model      #
# --------------------------------------------------------------------------- #
#: The exact server-owned LessonBlueprint fields the model must NEVER be asked
#: to invent — determined from ``lesson_blueprint._new_blueprint_from_model``'s
#: injected keys, not assumed.
_SERVER_OWNED_BLUEPRINT_FIELDS = {
    "schema_version", "blueprint_id", "blueprint_version", "lesson_identity",
    "source_hash", "source_metadata", "model", "prompt_policy", "created_at",
    "validation_status",
}


def test_lesson_blueprint_proposal_excludes_source_metadata_from_properties_and_required():
    raw = export_json_schema(LessonBlueprintProposal)
    assert "source_metadata" not in raw["properties"]
    assert "source_metadata" not in (raw.get("required") or [])
    strict = orch.prepare_openai_strict_schema(raw)
    assert "source_metadata" not in strict["properties"]
    assert "source_metadata" not in strict["required"]


def test_lesson_blueprint_proposal_excludes_every_server_owned_field():
    raw = export_json_schema(LessonBlueprintProposal)
    for field_name in _SERVER_OWNED_BLUEPRINT_FIELDS:
        assert field_name not in raw["properties"], (
            f"{field_name} is server-owned and must not be in the model-facing schema")
        assert field_name not in (raw.get("required") or [])


def test_lesson_blueprint_proposal_rejects_server_owned_fields_from_model_output():
    """The model cannot smuggle an authoritative id/hash/metadata/timestamp/
    model-identity/validation-status through the proposal — extra="forbid"."""
    from pydantic import ValidationError
    good = {
        "learning_objectives": [], "prerequisites": [], "concepts": [],
        "coverage_targets": [], "key_rules": [], "allowed_methods": [],
        "common_misconceptions": [], "task_families": [],
        "difficulty_dimensions": [], "hint_strategy": [],
        "mastery_requirements": {"min_concepts_mastered": 1, "min_independent_solves": 1},
        "language_guidance": {"language_register": "grade_6"},
        "supported_verification_types": [], "generation_confidence": 0.9,
    }
    LessonBlueprintProposal.model_validate(good)  # sanity: valid on its own
    for field_name in _SERVER_OWNED_BLUEPRINT_FIELDS:
        bad = dict(good)
        bad[field_name] = "attempted-forgery"
        with pytest.raises(ValidationError):
            LessonBlueprintProposal.model_validate(bad)


def test_blueprint_server_owned_fields_are_injected_by_the_server_not_the_model():
    from matbot.ai_tutor_v3 import lesson_blueprint as lb
    from matbot.ai_tutor_v3.schemas import LessonIdentity

    identity = LessonIdentity(grade=6, area_id="a", area_title="Oblast",
                              lesson_id="6-03-024", lesson_title="Djeljivost")
    proposal_dict = {
        "learning_objectives": ["x"], "prerequisites": [], "concepts": [],
        "coverage_targets": [], "key_rules": [], "allowed_methods": [],
        "common_misconceptions": [], "task_families": [],
        "difficulty_dimensions": [], "hint_strategy": [],
        "mastery_requirements": {"min_concepts_mastered": 1, "min_independent_solves": 1},
        "language_guidance": {"language_register": "grade_6"},
        "supported_verification_types": [], "generation_confidence": 0.9,
    }
    blueprint = lb._new_blueprint_from_model(
        proposal_dict, identity=identity, source_hash="deadbeef",
        metadata={"lesson_id": "6-03-024"}, model="gpt-5-mini")
    assert blueprint is not None
    assert blueprint.source_metadata == {"lesson_id": "6-03-024"}
    assert blueprint.source_hash == "deadbeef"
    assert blueprint.lesson_identity.lesson_id == "6-03-024"
    assert blueprint.blueprint_id.startswith("bp_")
    assert blueprint.validation_status == "draft"


# --------------------------------------------------------------------------- #
# validate_openai_strict_schema — the final recursive invariant gate           #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("purpose,model", REAL_SCHEMAS.items())
def test_all_seven_purpose_schemas_pass_the_final_validator(purpose, model):
    strict = orch.prepare_openai_strict_schema(export_json_schema(model))
    orch.validate_openai_strict_schema(strict, purpose=purpose, schema_name=model.__name__)


def test_validator_accepts_a_correctly_prepared_schema():
    strict = orch.prepare_openai_strict_schema(export_json_schema(TaskSpecification))
    orch.validate_openai_strict_schema(strict, purpose="task_generation", schema_name="TaskSpecification")


def test_validator_detects_projection_after_strict_preparation():
    """Reproduces the EXACT shape of the production incident: strict
    preparation ran FIRST (so 'required' already lists every property,
    including 'source_metadata'), then a naive projection removed the
    property from 'properties' WITHOUT also removing it from 'required' —
    leaving an extra required key with no matching property."""
    strict = orch.prepare_openai_strict_schema(
        {"type": "object",
         "properties": {"a": {"type": "string"}, "source_metadata": {"type": "string"}}})
    del strict["properties"]["source_metadata"]   # buggy projection: required untouched
    with pytest.raises(orch.StrictSchemaInvariantError) as excinfo:
        orch.validate_openai_strict_schema(strict, purpose="blueprint_generation",
                                           schema_name="LessonBlueprint")
    message = str(excinfo.value)
    assert "source_metadata" in message
    assert "blueprint_generation" in message
    assert "LessonBlueprint" in message


def test_validator_detects_missing_required_property():
    broken = {"type": "object", "additionalProperties": False,
             "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
             "required": ["a"]}
    with pytest.raises(orch.StrictSchemaInvariantError) as excinfo:
        orch.validate_openai_strict_schema(broken, purpose="p", schema_name="s")
    assert "missing_required=['b']" in str(excinfo.value)


def test_validator_detects_extra_required_property():
    broken = {"type": "object", "additionalProperties": False,
             "properties": {"a": {"type": "string"}},
             "required": ["a", "ghost"]}
    with pytest.raises(orch.StrictSchemaInvariantError) as excinfo:
        orch.validate_openai_strict_schema(broken, purpose="p", schema_name="s")
    assert "extra_required=['ghost']" in str(excinfo.value)


def test_validator_reports_correct_json_path_for_nested_mismatch():
    broken = {
        "type": "object", "additionalProperties": False,
        "properties": {"outer": {"$ref": "#/$defs/Inner"}},
        "required": ["outer"],
        "$defs": {"Inner": {
            "type": "object", "additionalProperties": False,
            "properties": {"x": {"type": "string"}}, "required": ["x", "ghost"]}},
    }
    with pytest.raises(orch.StrictSchemaInvariantError) as excinfo:
        orch.validate_openai_strict_schema(broken, purpose="p", schema_name="s")
    assert "$/$defs/Inner" in str(excinfo.value)


def test_validator_detects_dangling_local_ref():
    broken = {"type": "object", "additionalProperties": False,
             "properties": {"outer": {"$ref": "#/$defs/DoesNotExist"}},
             "required": ["outer"]}
    with pytest.raises(orch.StrictSchemaError):
        orch.validate_openai_strict_schema(broken, purpose="p", schema_name="s")


def test_validator_rejects_free_form_additional_properties_map():
    """A dict[str, str]-shaped field (arbitrary-key map) is not representable
    in OpenAI strict Structured Outputs — the validator must catch it even if
    ``prepare_openai_strict_schema`` left it untouched (it only forces
    ``additionalProperties: false`` when the key is ABSENT, never overwrites an
    existing map-shaped value, since silently discarding a real constraint is
    exactly what this whole fix must not do)."""
    schema = {"type": "object", "additionalProperties": False,
             "properties": {"meta": {"type": "object",
                                     "additionalProperties": {"type": "string"}}},
             "required": ["meta"]}
    with pytest.raises(orch.StrictSchemaInvariantError):
        orch.validate_openai_strict_schema(schema, purpose="p", schema_name="s")


def test_generate_runs_final_validator_before_any_network_call(monkeypatch):
    """``prepare_openai_strict_schema`` deliberately never OVERWRITES an
    existing ``additionalProperties`` value (that would silently discard a
    real ``dict[str, str]`` constraint the same way weakening strict mode
    would) — so a free-form map field like the old ``source_metadata`` passes
    ``prepare_openai_strict_schema`` unchanged. ``validate_openai_strict_schema``
    is what catches it, exercised end-to-end through the real client, before
    any network call."""
    client = orch.OpenAIResponsesClient(api_key="sk-test-not-real")

    def _blocked(*args, **kwargs):
        raise AssertionError("must not reach the network for an invariant violation")
    monkeypatch.setattr(client._client.responses, "create", _blocked)

    schema_with_free_form_map = {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "meta": {"type": "object", "additionalProperties": {"type": "string"}},
        },
    }
    result = client.generate(
        purpose="blueprint_generation", system="s", user="u",
        schema_name="LessonBlueprint", schema=schema_with_free_form_map,
        model="gpt-5-mini", timeout=5)
    assert result.status == "error"
    assert result.error_code == "strict_schema_incompatible"


# --------------------------------------------------------------------------- #
# V3 model resolution — independent of legacy OPENAI_MODEL_TEXT               #
# --------------------------------------------------------------------------- #
def test_resolve_v3_model_defaults_to_gpt5_mini(monkeypatch):
    monkeypatch.delenv("MATBOT_V3_MODEL", raising=False)
    assert orch.resolve_v3_model() == "gpt-5-mini"
    assert orch.resolve_v3_model() == orch.DEFAULT_MODEL


def test_resolve_v3_model_honours_override(monkeypatch):
    monkeypatch.setenv("MATBOT_V3_MODEL", "gpt-5")
    assert orch.resolve_v3_model() == "gpt-5"


def test_resolve_v3_model_blank_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("MATBOT_V3_MODEL", "   ")
    assert orch.resolve_v3_model() == "gpt-5-mini"


def test_resolve_v3_model_ignores_legacy_openai_model_text(monkeypatch):
    monkeypatch.delenv("MATBOT_V3_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_MODEL_TEXT", "gpt-4o-mini")
    assert orch.resolve_v3_model() == "gpt-5-mini"
