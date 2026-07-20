"""Engine V2 — Phase 1: the durable, server-authoritative TaskDefinition.

A ``TaskDefinition`` is the single structured record of an *active* task. It is
built from the SAME validation the legacy path already runs to gate activation
(``_validate_task_activation`` / ``_validate_exam_oblast_task`` →
``_task_answer_metadata``), so it never introduces a second, divergent judgement
about what the task is or whether it is gradeable.

Phase 1 scope (intentionally narrow, fully reversible):
  * The object is EMITTED (``next_state.task`` + ``response.task``) and
    round-tripped, but legacy ``last_tutor_task`` stays authoritative for
    behavior. ``question`` is always the final ``last_tutor_task`` by
    construction, so the two can never disagree.
  * ``solution_plan`` is always ``None`` here — the step engine is Phase 3.
  * No task is represented without its validated ``answer_schema``; a rejected
    validation yields ``validation_status="rejected"`` and the caller does not
    activate it (mirroring the legacy gate).

This module is pure/serializable and imports nothing heavy, so it cannot create
import cycles with ``ai_tutor_service``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_QUESTION_CHARS = 600
MAX_SCHEMA_ITEMS = 20

_VALIDATION_STATES = ("validated", "rejected", "unvalidated")
_SOURCES = ("template", "gpt_generated", "gpt_rubric", "fallback_template",
            "student_task", "unknown")


def _s(value: Any, limit: int = 120) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _schema_item(raw: dict) -> dict:
    """Trim one ``_task_answer_metadata`` item to a compact, JSON-safe schema
    entry. Only structured, non-sensitive fields — never prose beyond the short
    question stem the metadata already carries."""
    def _num(key: str) -> Any:
        v = raw.get(key)
        return v if isinstance(v, (int, float)) else None

    return {
        "n": raw.get("n") if isinstance(raw.get("n"), int) else 1,
        "answer_type": _s(raw.get("answer_type"), 80) or None,
        "expected_answer_display": _s(raw.get("expected_answer_display"), 120) or None,
        "expected_unit": _s(raw.get("expected_unit"), 40) or None,
        "required_form": _s(raw.get("required_form"), 40) or None,
        "required_denominator": _num("required_denominator"),
        "answer_kind": _s(raw.get("answer_kind"), 40) or None,
        "set_operation": _s(raw.get("set_operation"), 40) or None,
        "expected_boolean": (bool(raw.get("expected_boolean"))
                             if raw.get("expected_boolean") is not None else None),
        "divisor": _num("divisor"),
        "grading_method": _s(raw.get("grading_method"), 40) or None,
        "validation_status": _s(raw.get("validation_status"), 20) or "unvalidated",
    }


def build_answer_schema(validation: Any) -> dict:
    """Compact answer schema derived from a validation dict's ``items``."""
    items_raw = []
    if isinstance(validation, dict) and isinstance(validation.get("items"), list):
        items_raw = validation["items"]
    items = [_schema_item(i) for i in items_raw[:MAX_SCHEMA_ITEMS] if isinstance(i, dict)]
    checkable = any(
        i["validation_status"] == "validated" and i["grading_method"] != "structured_gpt"
        for i in items
    )
    return {
        "checkable": bool(checkable),
        "multi_item": len(items) >= 2,
        "items": items,
    }


def derive_skill_id(schema: dict) -> str:
    """Coarse skill label from the primary schema item. This is a best-effort
    tag for telemetry, NOT a curriculum taxonomy (that is Phase 5)."""
    items = schema.get("items") or []
    if not items:
        return "unknown"
    it = items[0]
    if it.get("divisor") is not None and it.get("expected_boolean") is not None:
        return "divisibility"
    if it.get("answer_kind") == "set" or it.get("set_operation"):
        return "set_operation"
    atype = (it.get("answer_type") or "").lower()
    if atype == "prime_factorization":
        return "prime_factorization"
    if atype == "boolean_with_explanation":
        return "divisibility"
    if it.get("grading_method") == "structured_gpt" or atype == "conceptual":
        return "conceptual"
    if atype in ("rational", "integer", "decimal", "measurement", "angle", "percentage"):
        return f"numeric_{atype}"
    return atype or "unknown"


def _derive_source(schema: dict, explicit: str | None) -> str:
    if explicit in _SOURCES:
        return explicit
    items = schema.get("items") or []
    if items and items[0].get("grading_method") == "structured_gpt":
        return "gpt_rubric"
    return "gpt_generated"


@dataclass
class TaskDefinition:
    task_id: str
    grade: Any
    oblast_id: str
    tema_id: str
    skill_id: str
    mode: str
    question: str
    answer_schema: dict
    validation_status: str
    source: str
    solution_plan: Any = None          # Phase 3
    # Topic identity: the RUNTIME id the client sent plus the canonical tema title
    # (tema_id holds the canonical NPP id when it could be resolved).
    runtime_topic_id: str = ""
    tema_title: str = ""
    engine_version: str = "v1-shadow"  # marks this as the Phase-1 shadow record

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "grade": self.grade,
            "oblast_id": self.oblast_id,
            "tema_id": self.tema_id,
            "skill_id": self.skill_id,
            "mode": self.mode,
            "question": self.question,
            "answer_schema": self.answer_schema,
            "solution_plan": self.solution_plan,
            "runtime_topic_id": self.runtime_topic_id,
            "tema_title": self.tema_title,
            "validation_status": self.validation_status,
            "source": self.source,
            "engine_version": self.engine_version,
        }


def build_task_definition(
    *,
    task_id: str | None,
    grade: Any,
    oblast_id: Any,
    tema_id: Any,
    mode: Any,
    question: Any,
    validation: Any,
    source: str | None = None,
    skill_id: str | None = None,
    runtime_topic_id: Any = "",
    tema_title: Any = "",
) -> TaskDefinition | None:
    """Construct a TaskDefinition from an already-computed validation dict.

    Returns ``None`` when there is no active question to represent. The caller
    is responsible for having gated activation on ``validation`` — this function
    only records the outcome; it does not decide activation.
    """
    q = _s(question, MAX_QUESTION_CHARS)
    if not q:
        return None
    schema = build_answer_schema(validation)
    status = "unvalidated"
    if isinstance(validation, dict):
        status = _s(validation.get("validation_status"), 20) or "unvalidated"
    if status not in _VALIDATION_STATES:
        status = "unvalidated"
    return TaskDefinition(
        task_id=_s(task_id, 80) or "",
        grade=grade,
        oblast_id=_s(oblast_id, 80),
        tema_id=_s(tema_id, 80),
        # A template-generated task carries its STABLE template skill_id; only
        # model/student tasks fall back to schema-derived inference.
        skill_id=_s(skill_id, 80) or derive_skill_id(schema),
        mode=_s(mode, 20) or "practice",
        question=q,
        answer_schema=schema,
        validation_status=status,
        source=_derive_source(schema, source),
        runtime_topic_id=_s(runtime_topic_id, 80),
        tema_title=_s(tema_title, 120),
    )


def normalize_task_definition(raw: Any) -> dict | None:
    """Validate a client-provided (round-tripped) task record. Returns ``None``
    for anything not shaped like a TaskDefinition."""
    if not isinstance(raw, dict):
        return None
    q = _s(raw.get("question"), MAX_QUESTION_CHARS)
    if not q:
        return None
    schema_raw = raw.get("answer_schema") if isinstance(raw.get("answer_schema"), dict) else {}
    items = schema_raw.get("items") if isinstance(schema_raw.get("items"), list) else []
    schema = {
        "checkable": bool(schema_raw.get("checkable")),
        "multi_item": bool(schema_raw.get("multi_item")),
        "items": [_schema_item(i) for i in items[:MAX_SCHEMA_ITEMS] if isinstance(i, dict)],
    }
    status = _s(raw.get("validation_status"), 20) or "unvalidated"
    if status not in _VALIDATION_STATES:
        status = "unvalidated"
    source = _s(raw.get("source"), 40)
    return {
        "task_id": _s(raw.get("task_id"), 80),
        "grade": raw.get("grade"),
        "oblast_id": _s(raw.get("oblast_id"), 80),
        "tema_id": _s(raw.get("tema_id"), 80),
        "skill_id": _s(raw.get("skill_id"), 80) or "unknown",
        "mode": _s(raw.get("mode"), 20) or "practice",
        "question": q,
        "answer_schema": schema,
        "solution_plan": None,
        "runtime_topic_id": _s(raw.get("runtime_topic_id"), 80),
        "tema_title": _s(raw.get("tema_title"), 120),
        "validation_status": status,
        "source": source if source in _SOURCES else "unknown",
        "engine_version": _s(raw.get("engine_version"), 20) or "v1-shadow",
    }
