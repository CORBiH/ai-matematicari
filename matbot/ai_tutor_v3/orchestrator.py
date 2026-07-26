# -*- coding: utf-8 -*-
"""Model orchestration: versioned prompt layers, an injectable structured-output
model client, and one function per model-call PURPOSE.

The model does the language and pedagogy; it never decides authoritative state.
Every call returns a strict, schema-validated structure — never free-form JSON,
never regex/brace extraction. Tests inject a ``FakeStructuredModelClient``; the
production client uses the OpenAI Responses API with strict Structured Outputs.

Importing this module makes NO OpenAI client and NO network call — the SDK is
imported lazily inside the production client only when it actually runs.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from pydantic import BaseModel, ValidationError

from matbot.ai_tutor_v3.schemas import (
    ActiveTaskTurnContext,
    ActiveTaskTurnDecision,
    NarrationResult,
    PracticeModelAssessment,
    PracticeTurnInterpretation,
    PromptPolicyReference,
    StudentTurnInterpretation,
    TaskSpecification,
    export_json_schema,
)

log = logging.getLogger("matbot.ai_tutor_v3.orchestrator")

DEFAULT_MODEL = "gpt-5-mini"

# The exact off-topic fallback the constitution mandates, verbatim.
OFF_TOPIC_FALLBACK = "Postavi mi pitanje ili zadatak iz matematike."


def _resolve_positive_float(env_name: str, default: float) -> float:
    """A positive float from an env var, or ``default`` when unset, blank,
    non-numeric, or <= 0 — the same fail-safe-to-default shape as
    ``resolve_v3_model``."""
    raw = (os.getenv(env_name) or "").strip()
    try:
        value = float(raw) if raw else 0.0
    except ValueError:
        value = 0.0
    return value if value > 0 else default


#: Per-call timeout default (Phase 8). Smaller than the legacy/general
#: ``AI_TUTOR_TIMEOUT`` (45s) DELIBERATELY: the frontend's own client-side
#: abort fires at a fixed 60s (see templates/index.html) regardless of what
#: the backend does, and a V3 turn can involve more than one sequential model
#: call — so each individual call must leave room for a possible second one.
DEFAULT_V3_CALL_TIMEOUT_S = 20.0

#: Total wall-clock budget for ALL of a turn's model calls combined (Phase 8).
#: Kept under the frontend's fixed 60s abort with margin for the rest of the
#: turn's non-model work (DB commit, rendering, quality gate).
DEFAULT_V3_TURN_BUDGET_S = 40.0

#: Below this many remaining seconds, a further model call is not attempted at
#: all — a call started with only a sliver of budget left is overwhelmingly
#: likely to be aborted anyway, wasting the attempt instead of failing safely.
MIN_V3_CALL_TIMEOUT_S = 3.0


def resolve_v3_call_timeout_s() -> float:
    """Per-call timeout ceiling: ``MATBOT_V3_CALL_TIMEOUT_S`` if a positive
    number, else ``DEFAULT_V3_CALL_TIMEOUT_S``."""
    return _resolve_positive_float("MATBOT_V3_CALL_TIMEOUT_S", DEFAULT_V3_CALL_TIMEOUT_S)


def resolve_v3_turn_budget_s() -> float:
    """Total per-turn model-call budget: ``MATBOT_V3_TURN_BUDGET_S`` if a
    positive number, else ``DEFAULT_V3_TURN_BUDGET_S``."""
    return _resolve_positive_float("MATBOT_V3_TURN_BUDGET_S", DEFAULT_V3_TURN_BUDGET_S)


#: Bounded output-token budget for the compact active-task interpretation call
#: (Section 6). Sized for the SMALL ``ActiveTaskTurnDecision`` schema: a
#: handful of short typed fields plus one short Bosnian narration sentence or
#: two — not the 1500-2000 tokens a generic unbounded call could wander into
#: for a one-sentence student answer. Configurable only because token needs
#: can shift if the schema grows; the default is a deliberate, small ceiling,
#: not a guess — see the measured baseline in tests/test_v3_latency.py.
DEFAULT_V3_ACTIVE_TASK_MAX_OUTPUT_TOKENS = 500


def resolve_v3_active_task_max_output_tokens() -> int:
    raw = (os.getenv("MATBOT_V3_ACTIVE_TASK_MAX_OUTPUT_TOKENS") or "").strip()
    try:
        value = int(raw) if raw else 0
    except ValueError:
        value = 0
    return value if value > 0 else DEFAULT_V3_ACTIVE_TASK_MAX_OUTPUT_TOKENS


def resolve_v3_model() -> str:
    """The model V3 Practice uses for ALL SEVEN call purposes.

    Deliberately independent of the legacy/general ``OPENAI_MODEL_TEXT``
    configuration: that value is chosen for the old engine and must never
    silently override V3's own default. ``MATBOT_V3_MODEL`` overrides
    ``DEFAULT_MODEL`` when non-empty; unset or blank/whitespace-only falls
    back to ``DEFAULT_MODEL``. This is the single resolution point — callers
    must not re-implement this fallback logic elsewhere.
    """
    value = os.getenv("MATBOT_V3_MODEL", "").strip()
    return value or DEFAULT_MODEL

# --------------------------------------------------------------------------- #
# Versioned prompt policy                                                      #
# --------------------------------------------------------------------------- #
#: Bump a version string when the corresponding layer's TEXT changes. The active
#: versions are recorded in the blueprint, the session state and every audit row.
PROMPT_POLICY_VERSIONS = {
    "constitution_version": "constitution@2026-07-25-v2",
    "bosnian_language_policy_version": "bs-lang@2026-07-25-v2",
    "math_notation_policy_version": "math-notation@2026-07-25-v2",
    "grade_policy_version": "grade@2026-07-25-v2",
    "mode_policy_version": "practice-mode@2026-07-25-v2",
    "lesson_blueprint_version": "blueprint-policy@2026-07-25-v2",
}


def current_prompt_policy(blueprint_version: str = "") -> PromptPolicyReference:
    """A ``PromptPolicyReference`` for the active layer versions. When a concrete
    blueprint version is known it overrides the policy-layer default."""
    data = dict(PROMPT_POLICY_VERSIONS)
    if blueprint_version:
        data["lesson_blueprint_version"] = blueprint_version
    return PromptPolicyReference(**data)


TUTOR_CONSTITUTION = (
    "Ti si asistent za matematiku za učenike osnovne škole u Bosni i Hercegovini. "
    "Odgovaraš samo na pitanja i zadatke iz osnovnoškolske matematike. "
    "Govoriš prirodnim bosanskim jezikom, latinicom, ijekavicom, kao dobar "
    "nastavnik u učionici — nikad kao priručnik ili tehnički dokument. Jasan si, "
    "strpljiv i nikoga ne posramljuješ. Objašnjenja prilagođavaš razredu. Pratiš "
    "izabrani razred i lekciju. Razlikuješ internu kanonsku vrijednost od "
    "prikazane notacije. Ne izmišljaš činjenice o lekciji koje Blueprint ne "
    "podržava. Tokom aktivnog zadatka, ako učenik pita o konceptu, odgovaraš na "
    "pitanje ali čuvaš aktivni zadatak. Kad je poruka nejasna, tražiš pojašnjenje "
    "umjesto da kažnjavaš. Za jasno nematematički zahtjev odgovaraš tačno: "
    f"'{OFF_TOPIC_FALLBACK}'. Taj odgovor NE koristiš za pozdrave, pitanja o "
    "korištenju tutora, pojašnjenja aktivnog zadatka ni frustraciju oko zadatka.\n"
    "Nikad ne koristiš i ne spominješ interne pojmove sistema pred učenikom: "
    "'shema', 'JSON', 'Blueprint', 'verifikator', 'reducer', 'model', "
    "'pouzdanost'/'confidence', 'stanje'/'state', 'verifikacija', 'schema', "
    "'promt'. Ne tvrdiš da je nešto matematički dokazano ili sigurno "
    "provjereno — govoriš prirodno, kao nastavnik koji gleda rad učenika, ne "
    "kao sistem koji izvještava o statusu provjere. Ne dodaješ nepotreban uvod "
    "ili pozdrav prije svakog zadatka ili savjeta. Ne ponavljaš cijelu "
    "definiciju koncepta u svakom zadatku. Izbjegavaj uvijek istu formulaciju "
    "pohvale — variraj je i veži je za ono što je učenik konkretno uradio. "
    "Koristiš jednu jasnu uputu po rečenici kad god je to prirodno."
)

BOSNIAN_LANGUAGE_POLICY = (
    "Koristi bosanske ijekavske oblike, jednostavan i prirodan školski jezik i "
    "ispravnu matematičku terminologiju — ni djetinjast, ni birokratski, ni "
    "akademski kad obična školska riječ dovoljno kaže. Razumij izostavljene "
    "dijakritike, pravopisne greške, nepotpunu gramatiku, kolokvijalni dječiji "
    "jezik, implicitne odgovore, promijenjene odgovore, nesigurnost (možda, "
    "valjda, otprilike) i pitanja koja sadrže brojeve a nisu predani odgovori. "
    "Ne traži tačne fraze. Preferiraj: jednakokraki trougao, zbir, stepenovanje."
)

MATH_NOTATION_POLICY = (
    "Interne kanonske vrijednosti i prikaz su odvojeni. Interno: egzaktni "
    "razlomci kao '1/2'. Prikaz učeniku: SVAKU matematičku formulu, razlomak, "
    "korijen, stepen ili jednačinu piši u LaTeX obliku UNUTAR MathJax "
    "graničnika \\( ... \\) za formulu u rečenici (npr. 'Proširi razlomak "
    "\\( \\frac{3}{4} \\) brojem 2.') ili \\[ ... \\] za izdvojen prikaz "
    "rješenja. Razlomak nikad ne pišeš kao goli '\\frac{3}{4}' bez tih "
    "graničnika — bez njih se formula ne prikazuje ispravno. Koristi "
    "decimalni zarez, · za množenje, : za dijeljenje. Ne prikazuj sirovo "
    "* ili ^ van LaTeX graničnika. Svaki smisleni korak rješenja u novom redu. "
    "Mješoviti broj bez riječi 'i'."
)

# Grade policy is provided per active grade only. Each entry encodes both the
# curriculum scope AND the expected sentence complexity/tone for that grade —
# the same clarity standard applies to every mathematical area (divisibility,
# fractions, equations, percentages, geometry, roots, powers, functions,
# polynomials, systems, …), never hardcoded per topic.
GRADE_POLICIES = {
    6: ("6. razred: cijeli brojevi i razlomci primjereni programu; jednačine "
        "kroz odnose operacija gdje se traži; školski NZD/NZS; bez naprednih "
        "algebarskih metoda. Jezik: kratke, direktne rečenice; obično jedna "
        "ili dvije upute po zadatku; poznate školske riječi; konkretan "
        "sljedeći korak; minimalna apstrakcija osim ako lekcija traži više."),
    7: ("7. razred: prebacivanje članova uz promjenu znaka gdje je programski "
        "primjereno. Jezik: sažet, uz malo više matematičkog objašnjenja; "
        "algebarske pojmove uvodi samo gdje ih program traži; rečenice drži "
        "jednostavnim."),
    8: ("8. razred: Pitagorina teorema, proporcije i procenti; školsko "
        "proporcionalno rasuđivanje i pravilo trojno. Jezik: dozvoljeno "
        "višekoračno rasuđivanje; objasni vezu između pravila; svaki korak "
        "vizuelno odvoji (novi red)."),
    9: ("9. razred: funkcije, polinomi i sistemi; biraj najjednostavniju "
        "programski odobrenu metodu; koordinatna geometrija po odobrenoj "
        "politici. Jezik: precizna algebarska i funkcijska terminologija, ali "
        "bez nepotrebno formalnog, fakultetskog izraza; uvijek biraj "
        "najjednostavniju odobrenu školsku metodu."),
}

PRACTICE_MODE_POLICY = (
    "Vježba: jedan aktivan zadatak u datom trenutku; zadatak ostaje dok se ne "
    "riješi, preskoči, otkrije ili izričito zamijeni; razumij prirodne odgovore; "
    "daj progresivne savjete; ne otkrivaj odmah cijelo rješenje; na pitanje o "
    "konceptu odgovori bez gubljenja zadatka i podsjeti na zadatak; nejasnu "
    "poruku tretiraj kao ne-odgovor; razlikuj samostalno rješavanje od "
    "potpomognutog; prilagodi težinu na osnovu dokaza; s vremenom pokrij cijelu "
    "lekciju; ne ponavljaj stalno jednu podtemu; sljedeći koncept biraj po "
    "Blueprint pokrivenosti i mastery stanju."
)

#: The task-writing standard (Phase 3.A): normally one or two SHORT sentences,
#: state exactly what to do, never embed the full concept definition, never a
#: hidden/expected answer, never several unrelated asks in one task. Generic —
#: the same standard applies to every lesson, not hardcoded per topic.
TASK_WRITING_STANDARD = (
    "Zadatak piši kratko: obično jedna do dvije kratke rečenice. Reci tačno "
    "šta učenik treba da uradi. Ne ugrađuj punu definiciju koncepta kad je "
    "dovoljna kratka uputa. Ne otkrivaj očekivani odgovor. Ne traži nekoliko "
    "nepovezanih stvari u jednom zadatku. Ne koristi formalnu frazu samo zato "
    "što zvuči matematički sofisticirano — piši onako kako bi nastavnik "
    "prirodno rekao učeniku u razredu."
)


def grade_policy(grade: int) -> str:
    return GRADE_POLICIES.get(int(grade), GRADE_POLICIES[6])


# --------------------------------------------------------------------------- #
# Model client protocol                                                        #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ModelCallResult:
    """The outcome of one structured model call. Never raises for content
    problems — a timeout/SDK error/invalid output is reported via ``status`` so
    the caller can fail safe."""
    status: str            # "ok" | "invalid_output" | "error"
    parsed: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)
    latency_ms: float = 0.0
    model: str = ""
    purpose: str = ""
    error_code: str = ""


class StructuredModelClient(Protocol):
    """The one seam every model call goes through. Production wires an OpenAI
    Responses-API implementation; tests wire a deterministic fake."""

    def generate(
        self, *, purpose: str, system: str, user: str, schema_name: str,
        schema: dict, model: str, timeout: Optional[float],
        response_model: type[BaseModel], max_output_tokens: Optional[int] = None,
    ) -> ModelCallResult:
        ...


# Call purposes (distinct, for audit + fake routing).
PURPOSE_BLUEPRINT = "blueprint_generation"
PURPOSE_INTERPRET = "turn_interpretation"
PURPOSE_TASK = "task_generation"
PURPOSE_HINT = "hint_generation"
PURPOSE_CONCEPT = "concept_explanation"
PURPOSE_NARRATION = "narration"
PURPOSE_REVEAL = "solution_reveal"
#: The ONE bounded quality-gate repair call (Phase 5) — never more than one
#: per rejected text; see ``matbot.ai_tutor_v3.quality_gate.MAX_REPAIR_ATTEMPTS``.
PURPOSE_REPAIR = "quality_repair"


class StrictSchemaError(ValueError):
    """A Pydantic-exported schema cannot be safely prepared for OpenAI's strict
    Structured Outputs mode without silently discarding a real constraint.

    Raised BEFORE any network call — an unsupported schema construct must fail
    locally, not as a 400 from the API."""


#: Sentinel distinguishing "no default key present" from "default is exactly
#: the value None" (a bare ``None`` default is a legitimate dict value).
_NO_DEFAULT = object()


def prepare_openai_strict_schema(schema: dict) -> dict:
    """Transform a Pydantic-exported JSON Schema into OpenAI strict Structured
    Outputs form, WITHOUT mutating the caller's schema.

    Grounded in the installed SDK's OWN reference conversion
    (``openai.lib._pydantic._ensure_strict_json_schema``, verified by reading
    ``.venv/Lib/site-packages/openai/lib/_pydantic.py``) rather than guessed:
    every object gets ``additionalProperties: false``; every property name is
    added to ``required`` (a formerly-optional field stays semantically
    optional because Pydantic already exported its type as nullable —
    ``anyOf: [<type>, {"type": "null"}]`` — this only changes what OpenAI is
    told to always INCLUDE in the response, never what our OWN re-validation
    of that response, via the untouched Pydantic model, still treats as
    optional); ``$defs``/``definitions`` are processed recursively; a ``$ref``
    with sibling keys is inlined; a bare ``default: null`` is dropped (matching
    the SDK exactly — a non-null default, e.g. ``require_reduced_form: false``,
    is left as-is, since the reference implementation does not strip it either).

    Beyond the SDK's own transform, this also converts a discriminated union
    (Pydantic's ``oneOf`` + ``discriminator``, used by ``VerificationRequest``)
    into a plain ``anyOf`` and drops the non-standard ``discriminator`` key —
    but ONLY after confirming the variants are mutually exclusive by their own
    literal discriminator value, so nothing is actually lost: at-most-one can
    ever match either way, so "exactly one must match" (``oneOf``) and "at
    least one must match" (``anyOf``) are behaviourally identical here.

    Raises ``StrictSchemaError`` — before any API call — for a construct this
    function cannot safely transform (e.g. an explicit
    ``additionalProperties: true``, ``patternProperties``, or a discriminated
    union whose variants are not provably disjoint).
    """
    root = copy.deepcopy(schema)
    return _strictify(root, root)


def _resolve_ref(root: dict, ref: str) -> Any:
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise StrictSchemaError(f"unsupported $ref format: {ref!r}")
    node: Any = root
    for key in ref[2:].split("/"):
        if not isinstance(node, dict) or key not in node:
            raise StrictSchemaError(f"could not resolve $ref {ref!r}")
        node = node[key]
    return node


def _check_disjoint_discriminated_variants(
    variants: list, discriminator: Any, root: dict,
) -> None:
    prop_name = discriminator.get("propertyName") if isinstance(discriminator, dict) else None
    if not prop_name:
        raise StrictSchemaError(
            "oneOf without a usable discriminator.propertyName cannot be "
            "safely converted to anyOf")
    seen_consts: list = []
    for variant in variants:
        resolved = variant
        if isinstance(variant, dict) and "$ref" in variant and len(variant) == 1:
            resolved = _resolve_ref(root, variant["$ref"])
        if not isinstance(resolved, dict):
            raise StrictSchemaError("oneOf variant is not an object schema")
        props = resolved.get("properties") or {}
        field_schema = props.get(prop_name) or {}
        const = field_schema.get("const", _NO_DEFAULT)
        if const is _NO_DEFAULT:
            raise StrictSchemaError(
                f"oneOf variant lacks a literal const for discriminator "
                f"property {prop_name!r}; cannot prove mutual exclusivity")
        seen_consts.append(const)
    if len(set(seen_consts)) != len(seen_consts):
        raise StrictSchemaError(
            "oneOf/discriminator variants are not mutually exclusive "
            f"(duplicate discriminator values: {seen_consts!r})")


def _strictify(node: Any, root: dict) -> Any:
    if isinstance(node, bool):
        raise StrictSchemaError(f"boolean schema node is not supported: {node!r}")
    if not isinstance(node, dict):
        raise StrictSchemaError(
            f"expected an object schema node, got {type(node).__name__}")

    for defs_key in ("$defs", "definitions"):
        defs = node.get(defs_key)
        if isinstance(defs, dict):
            for name, sub in list(defs.items()):
                defs[name] = _strictify(sub, root)

    # A $ref alongside sibling keys (e.g. a field-level description attached to
    # a referenced type) is inlined, then reprocessed as a plain object.
    ref = node.get("$ref")
    if ref is not None and len(node) > 1:
        resolved = _resolve_ref(root, ref)
        if not isinstance(resolved, dict):
            raise StrictSchemaError(f"$ref {ref!r} did not resolve to an object")
        merged = {**resolved, **{k: v for k, v in node.items() if k != "$ref"}}
        return _strictify(merged, root)

    if "patternProperties" in node:
        raise StrictSchemaError(
            "patternProperties is not supported in strict Structured Outputs schemas")

    properties = node.get("properties")
    if isinstance(properties, dict):
        if node.get("additionalProperties") is True:
            raise StrictSchemaError(
                "additionalProperties=True is not allowed for OpenAI strict "
                "Structured Outputs; the object schema must stay explicitly closed")
        node["additionalProperties"] = False
        node["required"] = list(properties.keys())
        node["properties"] = {
            key: _strictify(val, root) for key, val in properties.items()}
    elif node.get("type") == "object" and "additionalProperties" not in node:
        node["additionalProperties"] = False

    items = node.get("items")
    if items is not None:
        node["items"] = _strictify(items, root)

    any_of = node.get("anyOf")
    one_of = node.get("oneOf")
    if any_of is not None and one_of is not None:
        raise StrictSchemaError("a schema node cannot mix anyOf and oneOf")
    if one_of is not None:
        _check_disjoint_discriminated_variants(
            one_of, node.get("discriminator"), root)
        node["anyOf"] = [_strictify(v, root) for v in one_of]
        del node["oneOf"]
        node.pop("discriminator", None)
    elif any_of is not None:
        node["anyOf"] = [_strictify(v, root) for v in any_of]

    all_of = node.get("allOf")
    if all_of is not None:
        if len(all_of) == 1:
            merged = _strictify(all_of[0], root)
            node.update(merged)
            del node["allOf"]
        else:
            node["allOf"] = [_strictify(v, root) for v in all_of]

    if node.get("default", _NO_DEFAULT) is None:
        node.pop("default", None)

    return node


class StrictSchemaInvariantError(StrictSchemaError):
    """Raised by ``validate_openai_strict_schema`` when a FINAL, supposedly-
    prepared schema still violates OpenAI's strict Structured Outputs
    invariant. This is the gate that catches an ordering bug that
    ``prepare_openai_strict_schema`` alone cannot: e.g. code that projects
    (removes) a property from a schema AFTER strict preparation, without also
    removing it from ``required`` — exactly the shape of the production
    incident where 'source_metadata' remained in ``required`` after being
    dropped from ``properties``. Always raised BEFORE any network call, with a
    diagnostic naming the purpose, schema name, JSON path, and the exact extra
    / missing required keys — never prompt or student content.
    """


def _invariant_violation(purpose: str, schema_name: str, path: str, detail: str) -> "StrictSchemaInvariantError":
    return StrictSchemaInvariantError(
        f"[{purpose}/{schema_name}] {detail} at {path!r}")


def _walk_strict_invariant(node: Any, path: str, *, purpose: str, schema_name: str,
                           root: dict) -> None:
    if isinstance(node, bool):
        raise _invariant_violation(purpose, schema_name, path, "boolean schema node")
    if not isinstance(node, dict):
        raise _invariant_violation(
            purpose, schema_name, path,
            f"expected an object schema node, got {type(node).__name__}")

    ref = node.get("$ref")
    if ref is not None:
        resolved = _resolve_ref(root, ref)
        if len(node) > 1:
            merged = {**resolved, **{k: v for k, v in node.items() if k != "$ref"}}
            _walk_strict_invariant(merged, path, purpose=purpose,
                                   schema_name=schema_name, root=root)
        return

    for defs_key in ("$defs", "definitions"):
        defs = node.get(defs_key)
        if isinstance(defs, dict):
            for name, sub in defs.items():
                _walk_strict_invariant(sub, f"{path}/{defs_key}/{name}",
                                       purpose=purpose, schema_name=schema_name, root=root)

    properties = node.get("properties")
    if isinstance(properties, dict):
        prop_keys = set(properties.keys())
        required = node.get("required")
        if not isinstance(required, list) or any(not isinstance(r, str) for r in required):
            raise _invariant_violation(
                purpose, schema_name, path, f"malformed 'required' value: {required!r}")
        required_keys = set(required)
        extra_required = sorted(required_keys - prop_keys)
        missing_required = sorted(prop_keys - required_keys)
        if extra_required or missing_required:
            raise _invariant_violation(
                purpose, schema_name, path,
                "required != properties "
                f"(extra_required={extra_required}, missing_required={missing_required})")
        if node.get("additionalProperties") is not False:
            raise _invariant_violation(
                purpose, schema_name, path,
                f"additionalProperties must be false, got {node.get('additionalProperties')!r}")
        for key, val in properties.items():
            _walk_strict_invariant(val, f"{path}/properties/{key}", purpose=purpose,
                                   schema_name=schema_name, root=root)
    elif node.get("type") == "object":
        if node.get("additionalProperties") is not False:
            raise _invariant_violation(
                purpose, schema_name, path,
                "object without 'properties' must have additionalProperties=false "
                f"(a free-form map is not supported), got {node.get('additionalProperties')!r}")

    items = node.get("items")
    if items is not None:
        _walk_strict_invariant(items, f"{path}/items", purpose=purpose,
                               schema_name=schema_name, root=root)

    if "oneOf" in node:
        raise _invariant_violation(purpose, schema_name, path, "unconverted 'oneOf' remains")

    for key in ("anyOf", "allOf"):
        branches = node.get(key)
        if branches is not None:
            for i, branch in enumerate(branches):
                _walk_strict_invariant(branch, f"{path}/{key}/{i}", purpose=purpose,
                                       schema_name=schema_name, root=root)


def validate_openai_strict_schema(schema: dict, *, purpose: str, schema_name: str) -> None:
    """Final recursive invariant gate — run AFTER ``prepare_openai_strict_schema``
    (and after any server-side projection of model-owned fields) and BEFORE any
    network call.

    Recursively asserts, for every object schema node reachable from ``schema``
    (via ``$defs``/``definitions``, ``properties``, ``items``, ``anyOf``,
    ``allOf``, and resolved ``$ref``\\ s):

      * ``set(required) == set(properties.keys())`` — no extra, no missing key
      * ``additionalProperties is False``
      * every local ``$ref`` resolves
      * no residual ``oneOf`` (must already be converted to ``anyOf``)
      * no boolean schema node

    Raises ``StrictSchemaInvariantError`` locally — never lets a malformed
    schema reach the API and come back as a 400. The diagnostic names the
    purpose, schema_name, and exact JSON path, plus the extra/missing required
    key names — never prompt or student content.
    """
    _walk_strict_invariant(schema, "$", purpose=purpose, schema_name=schema_name, root=schema)


# --------------------------------------------------------------------------- #
# Safe error diagnostics — server-side log only, never in the response         #
# --------------------------------------------------------------------------- #
_MAX_LOGGED_MESSAGE_CHARS = 300
_KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{10,}")
_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def _sanitize_error_message(message: Any) -> str:
    """Bound and scrub an upstream error message before it is EVER logged.

    OpenAI Structured Outputs schema-validation errors describe SCHEMA
    problems (e.g. "'required' is required to include every key in
    properties") — never prompt or student content — but this still never
    trusts that: any API-key- or email-shaped substring is redacted and the
    result is length-capped regardless.
    """
    text = _KEY_PATTERN.sub("[redacted-key]", str(message or ""))
    text = _EMAIL_PATTERN.sub("[redacted-email]", text)
    return text[:_MAX_LOGGED_MESSAGE_CHARS]


def _log_openai_call_failure(exc: BaseException, *, purpose: str, model: str,
                             schema_name: str) -> None:
    """Structured, SANITIZED diagnostic — server-side log only.

    Fields come directly from the installed SDK's own exception attributes
    (verified in ``openai/_exceptions.py``: ``APIError.message``/``.code``,
    ``APIStatusError.status_code``/``.request_id``) — never from re-parsing a
    raw response body, and never including the system/user prompt, an API key,
    request headers, or any student/parent identifying text.
    """
    diagnostic = {
        "exception_class": type(exc).__name__,
        "http_status": getattr(exc, "status_code", None),
        "openai_error_code": getattr(exc, "code", None),
        "request_id": getattr(exc, "request_id", None),
        "message": _sanitize_error_message(getattr(exc, "message", None) or str(exc)),
        "model": model,
        "purpose": purpose,
        "schema_name": schema_name,
    }
    log.error("v3 openai call failed: %s", json.dumps(diagnostic, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# Responses API output classification (safe structural inspection only)       #
# --------------------------------------------------------------------------- #
#: Bound on how many item-type strings get logged/carried — never an
#: unbounded list, even for a pathological response.
_MAX_LOGGED_ITEM_TYPES = 20


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """``getattr``/``dict.get`` uniformly — the SAME classification code must
    work against real installed-SDK objects AND dict-style fakes used in
    tests, without guessing which shape it received."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


@dataclass(frozen=True)
class ResponsesOutputClassification:
    """Only safe, structural metadata extracted from a completed HTTP 200
    Responses API result — never the actual output text content beyond its
    length, never a prompt, never student text. See ``classify_responses_output``.
    """
    response_status: Optional[str] = None
    incomplete_reason: Optional[str] = None
    output_text: str = ""
    output_text_len: int = 0
    output_item_types: list = field(default_factory=list)
    content_item_types: list = field(default_factory=list)
    has_refusal: bool = False


def classify_responses_output(resp: Any) -> ResponsesOutputClassification:
    """Extract only safe structural metadata from a Responses API result.

    Grounded in the installed SDK's own real types (verified by reading
    ``openai/types/responses/response.py``): ``Response.status`` is
    ``Optional[Literal["completed","failed","in_progress","cancelled",
    "queued","incomplete"]]``; ``Response.incomplete_details.reason`` is
    ``Optional[Literal["max_output_tokens","content_filter"]]`` — these are
    the ONLY two reasons the installed SDK defines, never invented here.
    ``Response.output`` is a list of typed items; a ``message`` item's
    ``content`` list holds ``ResponseOutputText`` (``type="output_text"``) or
    ``ResponseOutputRefusal`` (``type="refusal"``) entries — a refusal is a
    CONTENT item, not a separate output item type.

    Works against real SDK objects (attribute access), dict-style fakes
    (``.get``), missing optional attributes, and unknown/future item types
    (recorded by name, never raising) — never touches private SDK internals.
    """
    response_status = _attr(resp, "status")
    incomplete_details = _attr(resp, "incomplete_details")
    incomplete_reason = _attr(incomplete_details, "reason") if incomplete_details is not None else None

    output_item_types: list = []
    content_item_types: list = []
    has_refusal = False
    for item in (_attr(resp, "output") or []):
        item_type = _attr(item, "type")
        if item_type is not None:
            output_item_types.append(str(item_type))
        for content in (_attr(item, "content") or []):
            content_type = _attr(content, "type")
            if content_type is not None:
                content_item_types.append(str(content_type))
            if content_type == "refusal":
                has_refusal = True

    output_text = _attr(resp, "output_text", "") or ""
    if not isinstance(output_text, str):
        output_text = ""

    return ResponsesOutputClassification(
        response_status=str(response_status) if response_status is not None else None,
        incomplete_reason=str(incomplete_reason) if incomplete_reason is not None else None,
        output_text=output_text,
        output_text_len=len(output_text),
        output_item_types=output_item_types[:_MAX_LOGGED_ITEM_TYPES],
        content_item_types=content_item_types[:_MAX_LOGGED_ITEM_TYPES],
        has_refusal=has_refusal,
    )


def _validation_error_summary(exc: "ValidationError") -> list:
    """A SANITIZED summary of a Pydantic validation failure: field paths and
    error types only — never the invalid value itself (which could echo
    student-adjacent model output), capped to a small count."""
    out = []
    for err in exc.errors()[:5]:
        loc = ".".join(str(p) for p in err.get("loc", ()) or ())
        out.append({"loc": loc, "type": err.get("type")})
    return out


def _log_invalid_output(
    classification: ResponsesOutputClassification, *, purpose: str, model: str,
    schema_name: str, error_code: str, request_id: Optional[str] = None,
    validation_summary: Optional[list] = None,
) -> None:
    """Structured, SANITIZED diagnostic for an invalid-but-HTTP-200 output —
    server-side log only. Never the raw output_text, never a prompt, never
    student text, never a full response dump, never a stack trace (this is
    an expected/classified case, not an unhandled exception)."""
    diagnostic = {
        "purpose": purpose,
        "schema_name": schema_name,
        "model": model,
        "response_status": classification.response_status,
        "incomplete_reason": classification.incomplete_reason,
        "output_text_len": classification.output_text_len,
        "output_item_types": classification.output_item_types,
        "content_item_types": classification.content_item_types,
        "error_code": error_code,
        "request_id": request_id,
    }
    if validation_summary is not None:
        diagnostic["validation_summary"] = validation_summary
    # NOT ``_sanitize_error_message``: that helper's 300-char cap is sized for
    # a raw, unstructured exception message. This diagnostic is already
    # bounded field-by-field (item-type lists and validation_summary are each
    # capped above) and a blind length slice here would risk truncating
    # structured JSON mid-field. Still redact key/email-shaped substrings —
    # defense in depth — but only length-cap generously (pathological input
    # only), never in a way that could cut a normal diagnostic in half.
    text = _KEY_PATTERN.sub("[redacted-key]", json.dumps(diagnostic, ensure_ascii=False))
    text = _EMAIL_PATTERN.sub("[redacted-email]", text)
    log.warning("v3 openai output invalid: %s", text[:2000])


class OpenAIResponsesClient:
    """Production client: OpenAI Responses API + strict Structured Outputs.

    Never constructed at import time. The SDK is imported inside ``__init__`` so
    that merely importing the V3 package (isolation test #3) starts no client.

    ``max_retries=0`` is DELIBERATE and load-bearing, not an oversight: the
    installed SDK's own default (``openai._constants.DEFAULT_MAX_RETRIES``,
    confirmed by reading the installed package: 2) means an unconfigured
    client silently retries a slow/timed-out request up to twice MORE inside
    the SDK before ever raising — exactly the production symptom ("Retrying
    request to /responses ..." followed eventually by ``APITimeoutError``).
    Each retry re-spends up to the same per-call timeout, so what looks like
    one bounded V3 model call could actually take up to 3x as long — blowing
    through the turn budget (Phase 8) and the frontend's fixed ~60s abort
    without ever showing up as "3 calls" in our own audit. V3 has its own
    turn-budget/no-retry-loop discipline (dispatcher._TurnBudget); a hidden
    SDK-level retry defeats it. One V3 model call must be one network
    attempt — this is scoped to the V3 client ONLY, never the legacy/general
    OpenAI client in app.py, which keeps its own (unrelated) retry policy.
    """

    def __init__(self, api_key: Optional[str] = None) -> None:
        from openai import OpenAI  # lazy: no import-time SDK dependency
        kwargs = {"api_key": api_key} if api_key else {}
        self._client = OpenAI(max_retries=0, **kwargs)

    def generate(
        self, *, purpose, system, user, schema_name, schema, model, timeout,
        response_model: type[BaseModel], max_output_tokens: Optional[int] = None,
    ) -> ModelCallResult:
        started = time.monotonic()
        try:
            strict_schema = prepare_openai_strict_schema(schema)
            validate_openai_strict_schema(strict_schema, purpose=purpose, schema_name=schema_name)
        except StrictSchemaError as exc:
            # Fails LOCALLY — no network call — for a construct we cannot
            # safely transform. Sanitized: only the exception message (schema
            # shape only, never prompt/student content) is logged.
            log.error(
                "v3 strict schema preparation failed: purpose=%s schema_name=%s "
                "model=%s error=%s", purpose, schema_name, model,
                _sanitize_error_message(str(exc)))
            return ModelCallResult(
                status="error", model=model, purpose=purpose,
                error_code="strict_schema_incompatible")
        create_kwargs: dict[str, Any] = dict(
            model=model,
            input=[{"role": "system", "content": system},
                   {"role": "user", "content": user}],
            text={"format": {"type": "json_schema", "name": schema_name,
                             "schema": strict_schema, "strict": True}},
            timeout=timeout,
        )
        if max_output_tokens is not None:
            create_kwargs["max_output_tokens"] = max_output_tokens
        try:
            resp = self._client.responses.create(**create_kwargs)
        except Exception as exc:  # timeout, SDK error, network — fail safe
            _log_openai_call_failure(exc, purpose=purpose, model=model,
                                     schema_name=schema_name)
            return ModelCallResult(
                status="error", latency_ms=(time.monotonic() - started) * 1000.0,
                model=model, purpose=purpose,
                error_code=type(exc).__name__)
        latency = (time.monotonic() - started) * 1000.0
        usage = {}
        u = getattr(resp, "usage", None)
        if u is not None:
            usage = {"prompt_tokens": getattr(u, "input_tokens", 0) or 0,
                     "completion_tokens": getattr(u, "output_tokens", 0) or 0}

        # A successful HTTP response is NOT automatically a usable one — the
        # production incident this classification fixes: an HTTP 200 whose
        # output was incomplete/refused/empty/malformed/schema-invalid used
        # to collapse into one undifferentiated "invalid_json". Every branch
        # below is checked BEFORE ever attempting json.loads.
        classification = classify_responses_output(resp)
        request_id = getattr(resp, "id", None)

        def _invalid(error_code: str, *, validation_summary: Optional[list] = None) -> ModelCallResult:
            _log_invalid_output(
                classification, purpose=purpose, model=model, schema_name=schema_name,
                error_code=error_code, request_id=request_id,
                validation_summary=validation_summary)
            return ModelCallResult(status="invalid_output", usage=usage,
                                   latency_ms=latency, model=model,
                                   purpose=purpose, error_code=error_code)

        if classification.response_status == "incomplete":
            return _invalid("incomplete_output")
        if classification.has_refusal:
            return _invalid("model_refusal")
        if not classification.output_text.strip():
            return _invalid("empty_output")
        try:
            # A strict json_schema response is guaranteed valid JSON — this is a
            # structured parse, not regex/brace extraction of free-form text.
            parsed = json.loads(classification.output_text)
        except (ValueError, TypeError):
            return _invalid("json_decode_error")
        try:
            validated = response_model.model_validate(parsed)
        except ValidationError as exc:
            return _invalid("schema_validation_error",
                           validation_summary=_validation_error_summary(exc))
        return ModelCallResult(status="ok", parsed=validated.model_dump(mode="json"),
                               usage=usage, latency_ms=latency, model=model, purpose=purpose)


# --------------------------------------------------------------------------- #
# Prompt assembly                                                              #
# --------------------------------------------------------------------------- #
def _blueprint_projection(blueprint) -> str:
    """A compact, lossless-enough projection of the blueprint for turn prompts."""
    bp = blueprint.model_dump(mode="json")
    keep = {k: bp[k] for k in (
        "lesson_identity", "concepts", "coverage_targets", "key_rules",
        "allowed_methods", "common_misconceptions", "task_families",
        "hint_strategy", "language_guidance") if k in bp}
    return json.dumps(keep, ensure_ascii=False)


def _session_projection(state) -> str:
    st = state.model_dump(mode="json")
    keep = {k: st[k] for k in (
        "active_task", "coverage", "mastery", "difficulty", "hint",
        "pending_clarification", "summary", "recent_turns") if k in st}
    return json.dumps(keep, ensure_ascii=False)


def build_system_prompt(grade: int, blueprint) -> str:
    """The stable, cacheable layers 1-5 + the lesson blueprint (layer 6)."""
    return "\n\n".join([
        TUTOR_CONSTITUTION,
        BOSNIAN_LANGUAGE_POLICY,
        MATH_NOTATION_POLICY,
        grade_policy(grade),
        PRACTICE_MODE_POLICY,
        "LEKCIJA (Blueprint):\n" + _blueprint_projection(blueprint),
    ])


def _validate_into(model_cls: type[BaseModel], parsed: dict):
    """Validate a parsed dict into a schema, or return None on failure."""
    try:
        return model_cls.model_validate(parsed)
    except ValidationError:
        return None


# --------------------------------------------------------------------------- #
# One function per purpose                                                     #
# --------------------------------------------------------------------------- #
def interpret_turn(
    client: StructuredModelClient, *, grade: int, blueprint, state,
    student_message: str, model: str, timeout: Optional[float],
) -> tuple[Optional[PracticeTurnInterpretation], ModelCallResult]:
    system = build_system_prompt(grade, blueprint)
    user = (
        "STANJE SESIJE:\n" + _session_projection(state) + "\n\n"
        "PORUKA UČENIKA:\n" + student_message + "\n\n"
        "Protumači poruku po značenju: turn_kind, da li je pokušaj odgovora, "
        "kratko normalizovano značenje, tvrdnje (claims), sigurnost i preciznost. "
        "Ako je pokušaj odgovora, dodaj provizornu procjenu (assessment): "
        "predloženi verdikt, pedagoška akcija, eventualne misconception kodove, "
        "da li je riješeno samostalno. NE odlučuješ konačan ishod. Ako poruka "
        "nije jasna, clarification_question neka bude prirodno i konkretno "
        "pitanje o TOME šta je nejasno — ne generička fraza. Ako je ovo pokušaj "
        "odgovora, dodaj i narration_proposal: prijedlog kratke povratne "
        "informacije primjerene TVOM predloženom verdiktu (server možda "
        "odluči drugačije — ipak piši povratnu informaciju kao da je tvoj "
        "predloženi verdikt konačan). Prati standard: tačno=koncizno bez "
        "ponavljanja iste fraze; netačno=prvo prizanj tvrdnju pa objasni "
        "problem pa sljedeći korak, zadatak ostaje; djelimično=navedi šta je "
        "tačno pa šta nedostaje. Nikad ne otkrivaj konačan odgovor ako zadatak "
        "ostaje aktivan."
    )
    result = client.generate(
        purpose=PURPOSE_INTERPRET, system=system, user=user,
        schema_name="PracticeTurnInterpretation",
        schema=export_json_schema(PracticeTurnInterpretation),
        model=model, timeout=timeout, response_model=PracticeTurnInterpretation)
    if result.status != "ok":
        return None, result
    parsed = _validate_into(PracticeTurnInterpretation, result.parsed)
    if parsed is None:
        return None, ModelCallResult(
            status="invalid_output", usage=result.usage,
            latency_ms=result.latency_ms, model=result.model,
            purpose=result.purpose, error_code="schema_validation_error")
    return parsed, result


# --------------------------------------------------------------------------- #
# Compact active-task interpretation (latency pass)                          #
#                                                                              #
# The DOMINANT V3 Practice turn: a free-form student message while a task is  #
# already active. ``interpret_turn``/``PracticeTurnInterpretation`` above     #
# remain for the rarer no-active-task path, which genuinely needs the fuller  #
# session/Blueprint picture. This path never sends the full Blueprint or the  #
# full durable session — see ``build_active_task_context``.                  #
# --------------------------------------------------------------------------- #
#: A single compact paragraph replacing the five full policy layers + full
#: Blueprint dump for THIS purpose only. Every rule here is still traceable to
#: the full versioned policies above (bumping THOSE bumps behavior for every
#: purpose; this is a deliberately narrower excerpt, not a fork of the rules).
ACTIVE_TASK_INTERPRETATION_POLICY = (
    "Ti si nastavnik matematike za bosanske osnovce, {grade}. razred. Tumačiš "
    "JEDNU poruku učenika tokom aktivnog zadatka u vježbi. Razumij prirodni "
    "bosanski: izostavljene dijakritike, kolokvijalni jezik, implicitne i "
    "promijenjene odgovore, nesigurnost, i brojeve u komentaru koji NISU "
    "predani odgovor. Razlikuj: odgovor na zadatak / pitanje o konceptu / "
    "traženje pomoći / komentar / traženje novog zadatka / promjenu težine / "
    "traženje rješenja / potvrdu / nejasnu poruku. Nejasna poruka NIJE "
    "pogrešan odgovor — traži pojašnjenje, nikad kazna. Pitanje o konceptu ili "
    "traženje pomoći NE mijenja aktivni zadatak. Tvoja procjena odgovora je "
    "SAMO prijedlog — server odlučuje konačno. Ako je ovo pokušaj odgovora, "
    "predloži i kratku, prirodnu, djetinjastu ali ne infantilnu bosansku "
    "povratnu poruku (narration_proposal): tačno=koncizno, ne uvijek ista "
    "fraza; netačno=prizanaj tvrdnju pa objasni problem pa sljedeći korak, "
    "zadatak ostaje; djelimično=šta je tačno pa šta nedostaje. Ne otkrivaj "
    "konačan odgovor ako zadatak ostaje aktivan. Nikad ne koristiš internu "
    "terminologiju (shema, JSON, Blueprint, model, pouzdanost, stanje) niti "
    "pišeš razmišljanje naglas — samo traženi strukturisani izlaz."
)


def build_active_task_context(
    *, grade: int, blueprint, state, student_message: str,
) -> ActiveTaskTurnContext:
    """Only what the model needs for ONE active-task turn — never the full
    Blueprint, never the full durable session. ``state.active_task`` must be
    set; callers only use this path when it is."""
    task = state.active_task
    recent = state.recent_turns[-2:] if state.recent_turns else []
    recent_lines = [f"{t.role}: {t.text[:200]}" for t in recent]
    return ActiveTaskTurnContext(
        grade=grade,
        lesson_id=blueprint.lesson_identity.lesson_id,
        lesson_title=blueprint.lesson_identity.lesson_title,
        concept_id=task.concept_id,
        target_id=task.target_id,
        task_id=task.task_id,
        task_text=task.question,
        answer_kind=task.answer_kind,
        expected_internal=task.expected_internal,
        planned_verification_type=task.planned_verification_type,
        key_rules=[r.statement for r in blueprint.key_rules[:2]],
        allowed_methods=list(blueprint.allowed_methods[:3]),
        relevant_misconceptions=[
            m.description for m in blueprint.common_misconceptions[:2]],
        hint_level=state.hint.current_level,
        solution_revealed=task.solution_revealed,
        difficulty_level=state.difficulty.level,
        recent_turns=recent_lines,
        student_message=student_message,
    )


def interpret_active_task_turn(
    client: StructuredModelClient, *, grade: int, blueprint, state,
    student_message: str, model: str, timeout: Optional[float],
) -> tuple[Optional[ActiveTaskTurnDecision], ModelCallResult]:
    """The compact counterpart to ``interpret_turn`` for the dominant case: a
    free-form message while a task is already active. Smaller system prompt,
    smaller context, smaller output schema, bounded output tokens."""
    context = build_active_task_context(
        grade=grade, blueprint=blueprint, state=state,
        student_message=student_message)
    system = ACTIVE_TASK_INTERPRETATION_POLICY.format(grade=grade)
    user = context.model_dump_json()
    result = client.generate(
        purpose=PURPOSE_INTERPRET, system=system, user=user,
        schema_name="ActiveTaskTurnDecision",
        schema=export_json_schema(ActiveTaskTurnDecision),
        model=model, timeout=timeout, response_model=ActiveTaskTurnDecision,
        max_output_tokens=resolve_v3_active_task_max_output_tokens())
    if result.status != "ok":
        return None, result
    parsed = _validate_into(ActiveTaskTurnDecision, result.parsed)
    if parsed is None:
        return None, ModelCallResult(
            status="invalid_output", usage=result.usage,
            latency_ms=result.latency_ms, model=result.model,
            purpose=result.purpose, error_code="schema_validation_error")
    return parsed, result


def decision_to_interpretation_and_assessment(
    decision: ActiveTaskTurnDecision,
) -> tuple[StudentTurnInterpretation, Optional[PracticeModelAssessment]]:
    """Adapts the compact ``ActiveTaskTurnDecision`` into the existing
    ``StudentTurnInterpretation``/``PracticeModelAssessment`` shape the
    reducer already consumes — the reducer itself is UNCHANGED by this
    latency pass; only what is asked of the model got smaller. Fields the
    reducer never reads (claims, stated_reasoning_components,
    visible_misconception_codes, precision, proposed_pedagogical_action,
    suggested_next_concept, solved_independently, used_assistance — verified
    by direct inspection of reducer.py/dispatcher.py) get safe, inert
    defaults, never invented model content.
    """
    interpretation = StudentTurnInterpretation(
        schema_version=decision.schema_version,
        turn_kind=decision.turn_kind,
        is_answer_attempt=decision.is_answer_attempt,
        normalized_meaning=decision.issue_summary or decision.turn_kind,
        claims=[],
        certainty="certain" if decision.confidence >= 0.5 else "uncertain",
        precision="unspecified",
        confidence=decision.confidence,
        clarification_question=decision.clarification_question,
        requested_action=decision.requested_action,
    )
    assessment = None
    if decision.is_answer_attempt and decision.proposed_verdict is not None:
        assessment = PracticeModelAssessment(
            schema_version=decision.schema_version,
            is_answer_attempt=True,
            proposed_verdict=decision.proposed_verdict,
            proposed_pedagogical_action="continue",
            confidence=decision.confidence,
            difficulty_suggestion=decision.difficulty_suggestion,
        )
    elif decision.difficulty_suggestion is not None:
        # difficulty_change carries no verdict but still needs an assessment
        # object for the reducer's _apply_difficulty to read the suggestion.
        assessment = PracticeModelAssessment(
            schema_version=decision.schema_version,
            is_answer_attempt=False,
            proposed_verdict="not_checkable",
            proposed_pedagogical_action="continue",
            confidence=decision.confidence,
            difficulty_suggestion=decision.difficulty_suggestion,
        )
    return interpretation, assessment


def generate_task(
    client: StructuredModelClient, *, grade: int, blueprint, state,
    target_id: Optional[str], model: str, timeout: Optional[float],
) -> tuple[Optional[TaskSpecification], ModelCallResult]:
    system = build_system_prompt(grade, blueprint)
    user = (
        "STANJE SESIJE:\n" + _session_projection(state) + "\n\n"
        + (f"Traženi coverage target: {target_id}\n" if target_id else "")
        + f"Trenutni nivo težine: {state.difficulty.level}\n\n"
        "Generiši JEDAN zadatak za vježbu vjeran ovoj lekciji i ovom targetu. "
        "Vrati TaskSpecification: concept_id, target_id (iz Blueprint coverage), "
        "pitanje, answer_kind, internu očekivanu vrijednost ako je poznata, "
        "nivo težine. Zadatak mora pripadati izabranoj lekciji. " + TASK_WRITING_STANDARD
        + " Pogledaj nedavne zadatke (recent_turns) i izbjegavaj skoro identičan "
        "zadatak — variraj brojeve, formulaciju ili podkoncept unutar lekcije."
    )
    result = client.generate(
        purpose=PURPOSE_TASK, system=system, user=user,
        schema_name="TaskSpecification",
        schema=export_json_schema(TaskSpecification),
        model=model, timeout=timeout, response_model=TaskSpecification)
    if result.status != "ok":
        return None, result
    parsed = _validate_into(TaskSpecification, result.parsed)
    if parsed is None:
        return None, ModelCallResult(
            status="invalid_output", usage=result.usage,
            latency_ms=result.latency_ms, model=result.model,
            purpose=result.purpose, error_code="schema_validation_error")
    return parsed, result


def _narration_call(
    client, *, purpose, grade, blueprint, state, instruction, model, timeout,
) -> tuple[Optional[NarrationResult], ModelCallResult]:
    system = build_system_prompt(grade, blueprint)
    user = ("STANJE SESIJE:\n" + _session_projection(state) + "\n\n" + instruction)
    result = client.generate(
        purpose=purpose, system=system, user=user,
        schema_name="NarrationResult",
        schema=export_json_schema(NarrationResult),
        model=model, timeout=timeout, response_model=NarrationResult)
    if result.status != "ok":
        return None, result
    parsed = _validate_into(NarrationResult, result.parsed)
    if parsed is None:
        return None, ModelCallResult(
            status="invalid_output", usage=result.usage,
            latency_ms=result.latency_ms, model=result.model,
            purpose=result.purpose, error_code="schema_validation_error")
    return parsed, result


#: Verdict-specific feedback standard (Phase 3.D/E/H) — the SAME generic
#: instruction set for every lesson; nothing here names a specific topic.
_FEEDBACK_STANDARD_BY_VERDICT = {
    "correct": (
        "Odgovor je tačan. Budi koncizan. Kad je korisno, kratko objasni ZAŠTO "
        "rješenje funkcioniše. Ne piši dugo predavanje poslije jednostavnog "
        "tačnog odgovora. Ne koristi uvijek istu formulaciju pohvale — veži je "
        "za ono što je učenik konkretno uradio."),
    "incorrect": (
        "Odgovor NIJE tačan. Prvo priznaj šta je učenik konkretno tvrdio, zatim "
        "objasni tačno šta je problem, pa daj sljedeći koristan korak. Zadatak "
        "ostaje aktivan. Ne ponavljaj odmah cijelo rješenje."),
    "partial": (
        "Odgovor je djelimično tačan. Eksplicitno navedi šta je tačno, pa reci "
        "šta još nedostaje. Ne tretiraj djelimično tačno rasuđivanje kao potpuno "
        "pogrešno. Zadatak ostaje aktivan."),
}


def narrate_feedback(client, *, grade, blueprint, state, verdict, model, timeout):
    standard = _FEEDBACK_STANDARD_BY_VERDICT.get(verdict, (
        "Napiši kratku, prirodnu bosansku povratnu informaciju primjerenu "
        "ishodu. Ne otkrivaj konačan odgovor ako zadatak nije riješen."))
    instruction = (
        f"Autoritativni ishod je '{verdict}' (server je odlučio; ti NE mijenjaš "
        f"ishod ni brojače). {standard}")
    return _narration_call(client, purpose=PURPOSE_NARRATION, grade=grade,
                           blueprint=blueprint, state=state,
                           instruction=instruction, model=model, timeout=timeout)


def generate_hint(client, *, grade, blueprint, state, model, timeout):
    level = state.hint.current_level
    if level <= 1:
        standard = (
            "Ovo je PRVI savjet za ovaj zadatak. Daj SAMO jedan sljedeći korak — "
            "zatraži od učenika da primijeti ili izračuna nešto konkretno "
            "korisno. Ne otkrivaj postupak niti konačan odgovor.")
    else:
        standard = (
            f"Ovo je savjet nivoa {level} — učenik je već dobio raniji savjet i "
            "još nije riješio zadatak. Budi KONKRETNIJI nego prošli put — možeš "
            "pokazati dio postupka — ali ostavi učeniku da sam završi zadnji "
            "korak. Ne daj konačan odgovor.")
    instruction = (
        f"Učenik traži pomoć. Daj JEDAN progresivan savjet za aktivni zadatak, "
        f"prema hint_strategy iz Blueprinta. {standard} Na kraju podsjeti na "
        "aktivni zadatak.")
    return _narration_call(client, purpose=PURPOSE_HINT, grade=grade,
                           blueprint=blueprint, state=state,
                           instruction=instruction, model=model, timeout=timeout)


def explain_concept(client, *, grade, blueprint, state, student_message, model, timeout):
    instruction = (
        "Učenik je postavio pitanje o konceptu tokom aktivnog zadatka: "
        f"'{student_message}'. Odgovori na TO pitanje, koristeći Blueprint. "
        "Objasni u malim logičkim koracima primjerenim razredu; kad je korisno, "
        "dodaj jednostavan primjer. Zatim KRATKO podsjeti učenika na trenutni "
        "aktivni zadatak (ne mijenjaj ga, ne otkrivaj mu odgovor).")
    return _narration_call(client, purpose=PURPOSE_CONCEPT, grade=grade,
                           blueprint=blueprint, state=state,
                           instruction=instruction, model=model, timeout=timeout)


def reveal_solution(client, *, grade, blueprint, state, model, timeout):
    instruction = (
        "Učenik traži rješenje. Prikaži jasno, školski formatirano rješenje "
        "aktivnog zadatka odobrenom školskom metodom, korak po korak, svaki "
        "smisleni korak u novom redu, s kratkim objašnjenjem ZAŠTO se taj korak "
        "radi. Ovo je potpomognut rad, NE samostalno rješavanje — ne predstavljaj "
        "ga kao učenikov vlastiti uspjeh.")
    return _narration_call(client, purpose=PURPOSE_REVEAL, grade=grade,
                           blueprint=blueprint, state=state,
                           instruction=instruction, model=model, timeout=timeout)


def acknowledge(client, *, grade, blueprint, state, student_message, model, timeout):
    instruction = (
        f"Učenik je napisao komentar: '{student_message}'. Odgovori prirodno i "
        "kratko, ostani fokusiran na matematiku, i podsjeti na aktivni zadatak "
        "ako postoji. Ne ocjenjuj ovo kao odgovor.")
    return _narration_call(client, purpose=PURPOSE_NARRATION, grade=grade,
                           blueprint=blueprint, state=state,
                           instruction=instruction, model=model, timeout=timeout)


# --------------------------------------------------------------------------- #
# Bounded quality-gate repair (Phase 5) — ONE call, minimal context            #
# --------------------------------------------------------------------------- #
def _compact_blueprint_constraints(blueprint) -> str:
    """A SMALL constraint summary for the repair call — never the full
    Blueprint dump and never raw conversation history, per the bounded-repair
    contract."""
    return json.dumps({
        "allowed_methods": blueprint.allowed_methods,
        "language_register": blueprint.language_guidance.language_register,
    }, ensure_ascii=False)


def repair_student_text(
    client: StructuredModelClient, *, grade: int, rejected_text: str,
    failure_categories: list[str], response_type: str, blueprint, model: str,
    timeout: Optional[float],
) -> tuple[Optional[NarrationResult], ModelCallResult]:
    """The ONE allowed bounded repair call for text that failed the quality
    gate. Deliberately minimal context: the rejected text, why it failed, the
    grade, the response type, and a compact Blueprint constraint summary —
    never the full session/conversation history."""
    system = "\n\n".join([
        TUTOR_CONSTITUTION, BOSNIAN_LANGUAGE_POLICY, MATH_NOTATION_POLICY,
        grade_policy(grade),
    ])
    user = (
        f"Sljedeći tekst za učenika je ODBIJEN iz razloga: "
        f"{', '.join(failure_categories)}.\n"
        f"Vrsta odgovora: {response_type}.\n"
        f"Ograničenja lekcije: {_compact_blueprint_constraints(blueprint)}\n\n"
        f"ODBIJENI TEKST:\n{rejected_text}\n\n"
        "Napiši ISPRAVLJENU verziju koja rješava navedene probleme, poštujući "
        "sva pravila jezika i notacije iznad. Ne dodaj informacije van "
        "navedenih ograničenja."
    )
    result = client.generate(
        purpose=PURPOSE_REPAIR, system=system, user=user,
        schema_name="NarrationResult", schema=export_json_schema(NarrationResult),
        model=model, timeout=timeout, response_model=NarrationResult)
    if result.status != "ok":
        return None, result
    parsed = _validate_into(NarrationResult, result.parsed)
    if parsed is None:
        return None, ModelCallResult(
            status="invalid_output", usage=result.usage,
            latency_ms=result.latency_ms, model=result.model,
            purpose=result.purpose, error_code="schema_validation_error")
    return parsed, result
