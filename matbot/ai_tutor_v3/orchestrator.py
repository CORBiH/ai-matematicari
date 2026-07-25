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
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from pydantic import BaseModel, ValidationError

from matbot.ai_tutor_v3.schemas import (
    NarrationResult,
    PracticeTurnInterpretation,
    PromptPolicyReference,
    TaskSpecification,
    export_json_schema,
)

log = logging.getLogger("matbot.ai_tutor_v3.orchestrator")

DEFAULT_MODEL = "gpt-5-mini"

# The exact off-topic fallback the constitution mandates, verbatim.
OFF_TOPIC_FALLBACK = "Postavi mi pitanje ili zadatak iz matematike."

# --------------------------------------------------------------------------- #
# Versioned prompt policy                                                      #
# --------------------------------------------------------------------------- #
#: Bump a version string when the corresponding layer's TEXT changes. The active
#: versions are recorded in the blueprint, the session state and every audit row.
PROMPT_POLICY_VERSIONS = {
    "constitution_version": "constitution@2026-07-25",
    "bosnian_language_policy_version": "bs-lang@2026-07-25",
    "math_notation_policy_version": "math-notation@2026-07-25",
    "grade_policy_version": "grade@2026-07-25",
    "mode_policy_version": "practice-mode@2026-07-25",
    "lesson_blueprint_version": "blueprint-policy@2026-07-25",
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
    "Govoriš prirodnim bosanskim jezikom, latinicom, ijekavicom. "
    "Jasan si, strpljiv i nikoga ne posramljuješ. Objašnjenja prilagođavaš razredu. "
    "Pratiš izabrani razred i lekciju. Razlikuješ internu kanonsku vrijednost od "
    "prikazane notacije. Ne izmišljaš činjenice o lekciji koje Blueprint ne "
    "podržava. Tokom aktivnog zadatka, ako učenik pita o konceptu, odgovaraš na "
    "pitanje ali čuvaš aktivni zadatak. Kad je poruka nejasna, tražiš pojašnjenje "
    "umjesto da kažnjavaš. Za jasno nematematički zahtjev odgovaraš tačno: "
    f"'{OFF_TOPIC_FALLBACK}'. Taj odgovor NE koristiš za pozdrave, pitanja o "
    "korištenju tutora, pojašnjenja aktivnog zadatka ni frustraciju oko zadatka."
)

BOSNIAN_LANGUAGE_POLICY = (
    "Koristi bosanske ijekavske oblike, jednostavan školski jezik i ispravnu "
    "matematičku terminologiju. Razumij izostavljene dijakritike, pravopisne "
    "greške, nepotpunu gramatiku, kolokvijalni dječiji jezik, implicitne "
    "odgovore, promijenjene odgovore, nesigurnost (možda, valjda, otprilike) i "
    "pitanja koja sadrže brojeve a nisu predani odgovori. Ne traži tačne fraze. "
    "Preferiraj: jednakokraki trougao, zbir, stepenovanje."
)

MATH_NOTATION_POLICY = (
    "Interne kanonske vrijednosti i prikaz su odvojeni. Interno: egzaktni "
    "razlomci kao '1/2'. Prikaz učeniku: koristi MathJax/LaTeX za razlomke "
    "(\\frac{1}{2}), decimalni zarez, · za množenje, : za dijeljenje, √ ili "
    "MathJax za korijene, eksponente/MathJax za stepene. Ne prikazuj sirovo "
    "* ili ^. Svaki smisleni korak rješenja u novom redu. Mješoviti broj bez "
    "riječi 'i'."
)

# Grade policy is provided per active grade only.
GRADE_POLICIES = {
    6: ("6. razred: cijeli brojevi i razlomci primjereni programu; jednačine "
        "kroz odnose operacija gdje se traži; školski NZD/NZS; bez naprednih "
        "algebarskih metoda."),
    7: ("7. razred: prebacivanje članova uz promjenu znaka gdje je programski "
        "primjereno."),
    8: ("8. razred: Pitagorina teorema, proporcije i procenti; školsko "
        "proporcionalno rasuđivanje i pravilo trojno."),
    9: ("9. razred: funkcije, polinomi i sistemi; biraj najjednostavniju "
        "programski odobrenu metodu; koordinatna geometrija po odobrenoj "
        "politici."),
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


class OpenAIResponsesClient:
    """Production client: OpenAI Responses API + strict Structured Outputs.

    Never constructed at import time. The SDK is imported inside ``__init__`` so
    that merely importing the V3 package (isolation test #3) starts no client.
    """

    def __init__(self, api_key: Optional[str] = None) -> None:
        from openai import OpenAI  # lazy: no import-time SDK dependency
        self._client = OpenAI(api_key=api_key) if api_key else OpenAI()

    def generate(
        self, *, purpose, system, user, schema_name, schema, model, timeout,
    ) -> ModelCallResult:
        started = time.monotonic()
        try:
            strict_schema = prepare_openai_strict_schema(schema)
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
        try:
            resp = self._client.responses.create(
                model=model,
                input=[{"role": "system", "content": system},
                       {"role": "user", "content": user}],
                text={"format": {"type": "json_schema", "name": schema_name,
                                 "schema": strict_schema, "strict": True}},
                timeout=timeout,
            )
        except Exception as exc:  # timeout, SDK error, network — fail safe
            _log_openai_call_failure(exc, purpose=purpose, model=model,
                                     schema_name=schema_name)
            return ModelCallResult(
                status="error", latency_ms=(time.monotonic() - started) * 1000.0,
                model=model, purpose=purpose,
                error_code=type(exc).__name__)
        latency = (time.monotonic() - started) * 1000.0
        text = getattr(resp, "output_text", "") or ""
        usage = {}
        u = getattr(resp, "usage", None)
        if u is not None:
            usage = {"prompt_tokens": getattr(u, "input_tokens", 0) or 0,
                     "completion_tokens": getattr(u, "output_tokens", 0) or 0}
        try:
            # A strict json_schema response is guaranteed valid JSON — this is a
            # structured parse, not regex/brace extraction of free-form text.
            parsed = json.loads(text)
        except (ValueError, TypeError):
            return ModelCallResult(status="invalid_output", usage=usage,
                                   latency_ms=latency, model=model,
                                   purpose=purpose, error_code="invalid_json")
        return ModelCallResult(status="ok", parsed=parsed, usage=usage,
                               latency_ms=latency, model=model, purpose=purpose)


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
        "da li je riješeno samostalno. NE odlučuješ konačan ishod."
    )
    result = client.generate(
        purpose=PURPOSE_INTERPRET, system=system, user=user,
        schema_name="PracticeTurnInterpretation",
        schema=export_json_schema(PracticeTurnInterpretation),
        model=model, timeout=timeout)
    if result.status != "ok":
        return None, result
    parsed = _validate_into(PracticeTurnInterpretation, result.parsed)
    if parsed is None:
        return None, ModelCallResult(
            status="invalid_output", usage=result.usage,
            latency_ms=result.latency_ms, model=result.model,
            purpose=result.purpose, error_code="schema_validation_failed")
    return parsed, result


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
        "nivo težine. Zadatak mora pripadati izabranoj lekciji."
    )
    result = client.generate(
        purpose=PURPOSE_TASK, system=system, user=user,
        schema_name="TaskSpecification",
        schema=export_json_schema(TaskSpecification),
        model=model, timeout=timeout)
    if result.status != "ok":
        return None, result
    parsed = _validate_into(TaskSpecification, result.parsed)
    if parsed is None:
        return None, ModelCallResult(
            status="invalid_output", usage=result.usage,
            latency_ms=result.latency_ms, model=result.model,
            purpose=result.purpose, error_code="schema_validation_failed")
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
        model=model, timeout=timeout)
    if result.status != "ok":
        return None, result
    parsed = _validate_into(NarrationResult, result.parsed)
    if parsed is None:
        return None, ModelCallResult(
            status="invalid_output", usage=result.usage,
            latency_ms=result.latency_ms, model=result.model,
            purpose=result.purpose, error_code="schema_validation_failed")
    return parsed, result


def narrate_feedback(client, *, grade, blueprint, state, verdict, model, timeout):
    instruction = (
        f"Autoritativni ishod je '{verdict}' (server je odlučio; ti NE mijenjaš "
        "ishod ni brojače). Napiši kratku, prirodnu bosansku povratnu "
        "informaciju primjerenu ishodu. Za netačno/djelimično: reci šta nedostaje "
        "bez ponavljanja cijelog rješenja. Ne otkrivaj konačan odgovor ako "
        "zadatak nije riješen.")
    return _narration_call(client, purpose=PURPOSE_NARRATION, grade=grade,
                           blueprint=blueprint, state=state,
                           instruction=instruction, model=model, timeout=timeout)


def generate_hint(client, *, grade, blueprint, state, model, timeout):
    level = state.hint.current_level
    instruction = (
        f"Učenik traži pomoć. Daj JEDAN progresivan savjet (nivo {level}) za "
        "aktivni zadatak, prema hint_strategy iz Blueprinta. Ne otkrivaj konačan "
        "odgovor. Na kraju podsjeti na aktivni zadatak.")
    return _narration_call(client, purpose=PURPOSE_HINT, grade=grade,
                           blueprint=blueprint, state=state,
                           instruction=instruction, model=model, timeout=timeout)


def explain_concept(client, *, grade, blueprint, state, student_message, model, timeout):
    instruction = (
        "Učenik je postavio pitanje o konceptu tokom aktivnog zadatka: "
        f"'{student_message}'. Objasni koncept kratko i primjereno razredu, "
        "koristeći Blueprint. Zatim PODSJETI učenika na trenutni aktivni zadatak "
        "(ne mijenjaj ga).")
    return _narration_call(client, purpose=PURPOSE_CONCEPT, grade=grade,
                           blueprint=blueprint, state=state,
                           instruction=instruction, model=model, timeout=timeout)


def reveal_solution(client, *, grade, blueprint, state, model, timeout):
    instruction = (
        "Učenik traži rješenje. Prikaži jasno, školski formatirano rješenje "
        "aktivnog zadatka, korak po korak, svaki korak u novom redu. Ovo se NE "
        "računa kao samostalno rješavanje.")
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
