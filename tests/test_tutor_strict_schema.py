"""Offline strict-schema and canonical-signature regression coverage."""
import json

import pytest
from pydantic import ValidationError

from matbot.tutor import pipeline as tutor_pipeline
from matbot.tutor.schema import (ReviewerFinal, SignatureParameter, TaskSignature,
                                 TutorDraft)


try:
    from openai.lib._pydantic import to_strict_json_schema
except ImportError:  # pragma: no cover - compatibility with older installed SDKs
    from openai._pydantic import to_strict_json_schema


def _walk_schema(fragment):
    if isinstance(fragment, dict):
        yield fragment
        for value in fragment.values():
            yield from _walk_schema(value)
    elif isinstance(fragment, list):
        for value in fragment:
            yield from _walk_schema(value)


@pytest.mark.parametrize("model", [TutorDraft, ReviewerFinal])
def test_openai_strict_schema_has_only_closed_objects(model):
    schema = to_strict_json_schema(model)
    serialized = json.dumps(schema, sort_keys=True)
    for fragment in _walk_schema(schema):
        if fragment.get("type") == "object":
            assert fragment.get("additionalProperties") is False
            assert fragment.get("additionalProperties") is not True
    assert "Any" not in serialized

    signature = schema["$defs"]["TaskSignature"]
    parameters = signature["properties"]["normalized_parameters"]
    assert parameters["type"] == "array"
    assert parameters["items"] == {"$ref": "#/$defs/SignatureParameter"}
    parameter = schema["$defs"]["SignatureParameter"]
    assert parameter["additionalProperties"] is False
    assert set(parameter["properties"]) == {"name", "value"}


def test_reviewer_independent_evidence_references_closed_difficulty_schema():
    schema = to_strict_json_schema(ReviewerFinal)
    field = schema["properties"]["reviewed_difficulty_evidence"]
    refs = [fragment.get("$ref") for fragment in _walk_schema(field)]
    assert "#/$defs/DifficultyEvidence" in refs
    difficulty = schema["$defs"]["DifficultyEvidence"]
    assert difficulty["additionalProperties"] is False


def _signature(parameters=(), conditions=(), objects=()):
    return TaskSignature(
        task_family="  generic  ", operation_or_relation=" calculation ",
        normalized_parameters=list(parameters), required_conditions=list(conditions),
        relevant_objects=list(objects), answer_type=" multiple_choice ",
    )


def test_signature_digest_ignores_semantically_irrelevant_order():
    first = _signature(
        (SignatureParameter(name="divisor", value="6"),
         SignatureParameter(name="tested_number", value="1350")),
        ("positive integer", "divisible"), ("number", "divisor"),
    )
    reordered = _signature(
        (SignatureParameter(name="tested_number", value="1350"),
         SignatureParameter(name="divisor", value="6")),
        ("divisible", "positive integer"), ("divisor", "number"),
    )
    assert first.digest() == reordered.digest()
    assert first.canonical_json() == reordered.canonical_json()


def test_signature_digest_changes_for_real_mathematical_change():
    six = _signature((SignatureParameter(name="divisor", value="6"),))
    twenty_five = _signature((SignatureParameter(name="divisor", value="25"),))
    assert six.digest() != twenty_five.digest()


def test_closed_parameters_cover_repeated_divisors_and_core_lesson_shapes():
    divisibility = _signature((
        SignatureParameter(name="tested_number", value="1350"),
        SignatureParameter(name="divisor", value="6"),
        SignatureParameter(name="divisor", value="25"),
    ))
    assert [parameter.value for parameter in divisibility.normalized_parameters if parameter.name == "divisor"] == ["6", "25"]

    for parameter in (
        SignatureParameter(name="fraction", value="3/8"),
        SignatureParameter(name="point", value="(2,-3)"),
        SignatureParameter(name="equation", value="2*x+3=7"),
    ):
        assert _signature((parameter,)).digest()

    with pytest.raises(ValidationError):
        SignatureParameter(name="divisor", value="6", arbitrary_metadata="forbidden")


def test_rewording_is_duplicate_but_structural_change_is_not():
    context = type("Context", (), {"topic_id": "6-03-001"})()
    original = _signature((SignatureParameter(name="divisor", value="6"),))
    reworded = _signature((SignatureParameter(name="divisor", value="6"),))
    changed = _signature((SignatureParameter(name="divisor", value="25"),))
    original_task = type("Task", (), {
        "text": "Provjeri djeljivost broja 1350 sa 6.", "task_signature": original,
    })()
    reworded_task = type("Task", (), {
        "text": "Da li je 1350 djeljiv sa 6?", "task_signature": reworded,
    })()
    changed_task = type("Task", (), {
        "text": "Da li je 1350 djeljiv sa 25?", "task_signature": changed,
    })()
    assert original_task.text != reworded_task.text
    prior = [tutor_pipeline._structured_signature_record(original_task, context)]
    reworded_record = tutor_pipeline._structured_signature_record(
        reworded_task, context)
    changed_record = tutor_pipeline._structured_signature_record(
        changed_task, context)
    assert tutor_pipeline._is_duplicate_structured_signature(reworded_record, prior)
    assert not tutor_pipeline._is_duplicate_structured_signature(changed_record, prior)
