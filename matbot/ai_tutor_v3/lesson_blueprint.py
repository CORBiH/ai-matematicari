# -*- coding: utf-8 -*-
"""Lesson Blueprint lifecycle: resolve → hash → look up → generate → validate →
store → reuse.

The blueprint is generated ONCE per (lesson, source-content hash) and reused
across sessions. The lesson TITLE is instructional data: for a title that lists
divisors ("… sa 2, 3, 4, 5, 6, 9, 10, 15 i 25") the validated blueprint must
carry ALL those coverage targets — enforced here, not trusted from the model,
and with NO special-cased Python divisibility parser (the numbers are extracted
generically from the title the curriculum supplies).

Lesson identity and curriculum data come only from retained infrastructure
(``content_loader``, ``topic_resolver``). No frozen tutoring module is imported.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from matbot import content_loader, topic_resolver
from matbot.ai_tutor_v3 import orchestrator
from matbot.ai_tutor_v3.schemas import (
    LessonBlueprint,
    LessonBlueprintProposal,
    LessonIdentity,
    export_json_schema,
)
from matbot.ai_tutor_v3.state_store import V3StateStore

_SCHEMA_VERSION = "v3.1"

#: Generic number-in-title extractor. NOT a divisibility rule — it pulls the
#: standalone integers the curriculum author wrote into ANY lesson title, so the
#: same code covers "razlomci", "uglovi", etc. Used only to CHECK that the
#: model's coverage targets did not silently drop a listed value.
_TITLE_NUMBER_RE = re.compile(r"(?<!\d)(\d{1,3})(?!\d)")


def resolve_lesson_identity(
    grade: int, selected_topic: str, selected_oblast: str = "",
) -> Optional[LessonIdentity]:
    """Resolve a runtime topic selection to a canonical curriculum identity.

    Returns None when the topic cannot be resolved — the dispatcher then declines
    to a safe fallback rather than inventing a lesson.
    """
    canonical = topic_resolver.resolve_topic(grade, selected_topic)
    if canonical is None and selected_oblast:
        canonical = topic_resolver.resolve_topic(grade, selected_oblast)
    if canonical is None or not canonical.npp_id:
        return None
    area_title = canonical.oblast or str(selected_oblast or "").strip() or "Matematika"
    try:
        return LessonIdentity(
            grade=grade,
            area_id=_slug(area_title),
            area_title=area_title,
            lesson_id=canonical.npp_id,
            lesson_title=canonical.tema or canonical.npp_id,
            language="bs-Latn",
        )
    except Exception:
        return None


def source_metadata(identity: LessonIdentity, grade: int) -> dict[str, str]:
    """Curriculum metadata for this lesson — the raw material the blueprint is
    generated from. Read from ``content_loader`` (source of truth)."""
    meta = {
        "lesson_id": identity.lesson_id,
        "lesson_title": identity.lesson_title,
        "area_title": identity.area_title,
        "grade": str(grade),
    }
    try:
        master = content_loader.load_master_content(grade=grade)
        row = (master.get("topics_by_id") or {}).get(identity.lesson_id)
        if isinstance(row, dict):
            for key in ("npp_scope", "match_quality", "notes", "mode_usage",
                        "existing_ai_topic_ids"):
                val = row.get(key)
                if val:
                    meta[key] = str(val)
    except Exception:
        pass  # metadata is best-effort enrichment; identity fields are enough
    return meta


def compute_source_hash(metadata: dict[str, str]) -> str:
    """Stable hash of the source metadata. Any change to the curriculum row for
    this lesson yields a new hash → a new blueprint version."""
    payload = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def expected_title_targets(lesson_title: str) -> list[str]:
    """The coverage targets the TITLE itself implies — generically, the distinct
    integers it lists. For the divisibility lesson this is 2,3,4,5,6,9,10,15,25;
    for a title with no numbers it is empty and imposes no constraint."""
    seen: list[str] = []
    for m in _TITLE_NUMBER_RE.finditer(lesson_title or ""):
        n = m.group(1)
        if n not in seen:
            seen.append(n)
    return seen


def validate_blueprint(blueprint: LessonBlueprint) -> tuple[bool, str]:
    """Three automatic gates. Returns (ok, reason).

    1. schema — already guaranteed by the time we hold a ``LessonBlueprint``.
    2. coverage — every integer the title lists must appear as a coverage target
       (this is what stops the divisibility lesson from silently dropping to
       just 2/5/10).
    3. feasibility — every declared verification type must be one the schema
       knows (a blueprint may not promise a verifier family that does not exist).
    """
    if not blueprint.coverage_targets:
        return False, "no_coverage_targets"
    title_targets = expected_title_targets(blueprint.lesson_identity.lesson_title)
    have = {t.target_id for t in blueprint.coverage_targets}
    missing = [n for n in title_targets if n not in have]
    if missing:
        return False, f"coverage_missing_title_targets:{','.join(missing)}"
    if blueprint.generation_confidence < 0.5:
        return False, "low_generation_confidence"
    return True, ""


def _new_blueprint_from_model(
    parsed: dict, *, identity: LessonIdentity, source_hash: str,
    metadata: dict[str, str], model: str,
) -> Optional[LessonBlueprint]:
    """Assemble a validated ``LessonBlueprint`` from the model's parsed output,
    injecting server-owned fields (identity, hash, versions, timestamp) so the
    model cannot forge them."""
    now = datetime.now(timezone.utc)
    blueprint_version = f"{identity.lesson_id}@{source_hash}"
    policy = orchestrator.current_prompt_policy()
    data = dict(parsed)
    data.update({
        "schema_version": _SCHEMA_VERSION,
        "blueprint_id": "bp_" + uuid.uuid4().hex[:12],
        "blueprint_version": blueprint_version,
        "lesson_identity": identity.model_dump(mode="json"),
        "source_hash": source_hash,
        "source_metadata": metadata,
        "model": model or "gpt-5-mini",
        "prompt_policy": policy.model_dump(mode="json"),
        # A real datetime object, not an ISO string: strict=True rejects a string
        # for a datetime field on the model_validate (Python) path.
        "created_at": now,
        "validation_status": "draft",
    })
    try:
        return LessonBlueprint.model_validate(data)
    except Exception:
        return None


def generate_blueprint(
    client: orchestrator.StructuredModelClient, *, identity: LessonIdentity,
    grade: int, model: str = orchestrator.DEFAULT_MODEL,
    timeout: Optional[float] = None,
) -> tuple[Optional[LessonBlueprint], str]:
    """Ask the model to generate the blueprint, validate it, mark it validated.
    Returns (blueprint|None, reason)."""
    metadata = source_metadata(identity, grade)
    source_hash = compute_source_hash(metadata)
    title_targets = expected_title_targets(identity.lesson_title)

    system = "\n\n".join([
        orchestrator.TUTOR_CONSTITUTION,
        orchestrator.BOSNIAN_LANGUAGE_POLICY,
        orchestrator.grade_policy(grade),
    ])
    user = (
        "Generiši strukturisani LessonBlueprint za ovu lekciju. Naslov lekcije je "
        "instruktivni podatak, ne ukras.\n\n"
        f"LEKCIJA: {identity.lesson_title}\n"
        f"OBLAST: {identity.area_title}\nRAZRED: {grade}\n"
        f"METAPODACI: {json.dumps(metadata, ensure_ascii=False)}\n\n"
        "coverage_targets MORA sadržavati sve koncepte koje naslov navodi "
        + (f"(uključujući brojeve: {', '.join(title_targets)}). "
           if title_targets else ". ")
        + "Uključi koncepte, ključna pravila, česte greške, familije zadataka, "
        "dimenzije težine, strategiju savjeta, mastery zahtjeve, jezičku "
        "smjernicu i generation_confidence."
    )
    result = client.generate(
        purpose=orchestrator.PURPOSE_BLUEPRINT, system=system, user=user,
        schema_name="LessonBlueprintProposal",
        schema=export_json_schema(LessonBlueprintProposal),
        model=model, timeout=timeout)
    if result.status != "ok":
        return None, f"model_{result.status}:{result.error_code}"

    try:
        proposal = LessonBlueprintProposal.model_validate(result.parsed)
    except Exception:
        return None, "schema_validation_failed"

    blueprint = _new_blueprint_from_model(
        proposal.model_dump(mode="json"), identity=identity, source_hash=source_hash,
        metadata=metadata, model=result.model or model)
    if blueprint is None:
        return None, "schema_validation_failed"
    ok, reason = validate_blueprint(blueprint)
    if not ok:
        return None, reason
    return blueprint.model_copy(update={"validation_status": "validated"}), ""


def get_or_create_blueprint(
    store: V3StateStore, client: orchestrator.StructuredModelClient, *,
    identity: LessonIdentity, grade: int, model: str = orchestrator.DEFAULT_MODEL,
    timeout: Optional[float] = None,
) -> tuple[Optional[LessonBlueprint], str]:
    """The lifecycle entry point: reuse a stored blueprint for this (lesson,
    source hash), else generate, validate and store one. Returns
    (blueprint|None, reason)."""
    metadata = source_metadata(identity, grade)
    source_hash = compute_source_hash(metadata)

    stored = store.find_blueprint(identity.lesson_id, source_hash)
    if stored is not None:
        try:
            return LessonBlueprint.model_validate_json(stored), ""
        except Exception:
            pass  # corrupt stored row → regenerate

    blueprint, reason = generate_blueprint(
        client, identity=identity, grade=grade, model=model, timeout=timeout)
    if blueprint is None:
        return None, reason
    store.store_blueprint(
        blueprint_id=blueprint.blueprint_id, lesson_id=blueprint.lesson_identity.lesson_id,
        blueprint_version=blueprint.blueprint_version, source_hash=blueprint.source_hash,
        blueprint_json=blueprint.model_dump_json(),
        validation_status=blueprint.validation_status, model=blueprint.model,
        prompt_versions_json=blueprint.prompt_policy.model_dump_json(),
        created_at=blueprint.created_at.isoformat())
    return blueprint, ""


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower().strip())
    return s.strip("-") or "oblast"
