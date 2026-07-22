# -*- coding: utf-8 -*-
"""SemanticAnswerJudge: a shared, cross-skill hybrid grading layer.

Production kept losing a game of regex whack-a-mole: "da jer je zadnja cifra
0" was rejected, "ne jer je neparan" was rejected, "jer je broj paran" was
routed as a concept question — and every new phrasing needed another local
pattern. Children produce unpredictable language, misspellings, implicit
answers, and partial reasoning; no fixed set of regexes can enumerate it.

This module is the ONE place OpenAI is allowed to INTERPRET a free-form
answer. It never decides correctness and never touches state:

    raw student message
    -> deterministic direct parser/checker (answer_checker.py, unchanged)
    -> if that already produced a confident, checkable result: DONE — the
       model is never even called
    -> otherwise: SemanticAnswerJudge interprets the message into a small,
       schema-validated set of CLAIMS (never a verdict)
    -> a deterministic verifier (``verify_claims`` below) compares those
       claims against VERIFIED FACTS computed by plain arithmetic in
       ``divisibility_facts``/``solution_facts``/the checker
    -> the existing grading policy (engine.py) turns the outcome into a
       verdict and, separately, into counters — the model never sees or
       returns either

The model's structured output is validated field-by-field; anything that
doesn't parse, doesn't match the allowed vocabulary, times out, or comes back
below the confidence floor fails SAFE — to "not understood", never to
"correct". OpenAI cannot mutate task state, streaks, attempts, or the
expected answer: those all remain in ``ActiveTask``/``SessionState``/
``GradingResult``, owned exactly as before.

Shared vs skill-specific (see the class/function docstrings below for detail):
  * SHARED:   the JSON schema, the OpenAI call, response validation, the
              claim-verification ENGINE (how a claim of a given TYPE is
              checked against a fact of the same type).
  * SKILL-SPECIFIC: which facts exist for a task (``build_context``'s
              per-skill branches) and which claims are SUFFICIENT for a
              correct/complete answer (``required_components`` per divisor,
              supplied by ``divisibility_facts``).
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Callable

from matbot.minimal import divisibility_facts as df
from matbot.minimal import solution_facts as sf

ENV_FLAG = "MATBOT_SEMANTIC_GRADING"

#: off (default): unchanged deterministic behaviour, no model call from here.
#: shadow: the judge runs and its outcome is logged (telemetry only) — the
#:         EXISTING verdict is returned unchanged.
#: on: the semantic outcome is used, but ONLY when the direct deterministic
#:     path could not safely understand the message on its own.
_MODES = ("off", "shadow", "on")


def semantic_mode() -> str:
    value = (os.getenv(ENV_FLAG) or "off").strip().lower()
    return value if value in _MODES else "off"


#: Below this, a judgment is treated as if the model had failed outright.
MIN_CONFIDENCE = 0.55
MAX_TOKENS = 400
DEFAULT_TIMEOUT = 8.0

_ALLOWED_RESPONSE_KINDS = {"answer", "explanation", "question", "task_request", "other"}
_ALLOWED_DECISIONS = {"yes", "no", "unknown"}
_ALLOWED_DECISION_SOURCES = {"explicit", "implicit", "none"}
_ALLOWED_RELEVANCE = {"direct", "partial", "unrelated"}
_ALLOWED_COMPLETENESS = {"complete", "partial", "absent"}
_ALLOWED_POLARITY = {"positive", "negative"}
_ALLOWED_CERTAINTY = {"certain", "uncertain"}
_ALLOWED_PRECISION = {"exact", "approximate", "unspecified"}
#: The full vocabulary this module understands. A skill's context narrows
#: this further via ``allowed_claim_types``; "other" is always tolerated but
#: never treated as sufficient evidence for anything.
ALLOWED_CLAIM_TYPES = {
    "last_digit", "digit_sum", "parity", "last_two_digits",
    "divisibility_factor", "equation_step", "rational_result", "other",
}

#: Reusable ANSWER-FAMILY vocabulary (requirement 7) — a skill supplies facts
#: and an acceptance policy; it does not need its own prompt or parser.
ANSWER_FAMILIES = (
    "boolean_with_explanation", "rational", "equation_solution",
    "factorization", "measurement", "set_answer",
)


# --------------------------------------------------------------------------- #
# Structured INPUT — the bounded context the judge is allowed to see          #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GradingContext:
    """Exactly what the model is given. Nothing else — no conversation
    history, no system internals, no hidden expected-answer reasoning beyond
    what a skill has already computed by plain arithmetic."""
    skill_id: str
    answer_type: str                      # one of ANSWER_FAMILIES
    task_text: str
    student_message: str
    expected_answer: str
    verified_facts: dict = field(default_factory=dict)
    required_components: tuple[str, ...] = ()
    allowed_claim_types: tuple[str, ...] = ()
    #: An EXACT task (equation_solution, most rational tasks) must NOT accept
    #: hedged language ("otprilike", "možda") as full credit even when the
    #: value matches — see ``_verify_rational_like``. A measurement-style
    #: skill that genuinely tolerates approximation may opt in here; nothing
    #: currently does, so this defaults closed.
    allows_approximate: bool = False

    def to_payload(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "answer_type": self.answer_type,
            "task_text": self.task_text,
            "student_message": self.student_message,
            "expected_answer": self.expected_answer,
            "verified_facts": self.verified_facts,
            "required_components": list(self.required_components),
            "allowed_claim_types": list(self.allowed_claim_types),
        }


# --------------------------------------------------------------------------- #
# Strict structured OUTPUT                                                    #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Claim:
    type: str
    value: str
    polarity: str
    confidence: float


@dataclass(frozen=True)
class SemanticJudgment:
    """The model's INTERPRETATION only. No field here is a verdict, a state
    change, or a hidden fact not already present in the supplied context."""
    understood: bool
    response_kind: str
    decision: str
    decision_source: str
    final_answer_text: str
    claims: tuple[Claim, ...]
    explanation_present: bool
    relevance: str
    completeness: str
    ambiguity: str
    confidence: float
    #: "certain"/"uncertain" and "exact"/"approximate"/"unspecified" — hedge
    #: words ("otprilike", "možda", "oko") are a LANGUAGE fact the model is
    #: well-placed to recognise; whether that costs full credit is a
    #: DETERMINISTIC policy decision made in ``_verify_rational_like``, never
    #: the model itself.
    certainty: str = "certain"
    precision: str = "unspecified"

    def to_telemetry(self) -> dict:
        """Structured classification only — never chain-of-thought prose."""
        return {
            "response_kind": self.response_kind,
            "decision": self.decision,
            "decision_source": self.decision_source,
            "completeness": self.completeness,
            "certainty": self.certainty,
            "precision": self.precision,
            "confidence": round(self.confidence, 2),
            "claims": [{"type": c.type, "value": c.value, "polarity": c.polarity}
                      for c in self.claims],
        }


@dataclass(frozen=True)
class VerifiedOutcome:
    """The deterministic verifier's result — the ONLY thing ``grading.py`` is
    allowed to act on. Booleans, not prose; no field here came from the model
    directly."""
    checkable: bool
    verdict: str                # correct | partial | incorrect | unverified
    detail: str
    graded_answer: str
    is_attempt: bool
    is_wrong_attempt: bool


def _not_checkable() -> VerifiedOutcome:
    return VerifiedOutcome(checkable=False, verdict="unverified",
                           detail="not_checkable", graded_answer="",
                           is_attempt=False, is_wrong_attempt=False)


# --------------------------------------------------------------------------- #
# The model call — SHARED across every skill and answer family               #
# --------------------------------------------------------------------------- #
_SYSTEM_PROMPT = (
    "Ti si TUMAC (interpreter), ne ocjenjivac. Dobices strukturisan kontekst "
    "zadatka za dijete (razred 6-9, bosanski jezik, latinica) i njegovu poruku.\n"
    "Tvoj JEDINI posao je da protumacis STA je ucenik rekao — ne da odlucis "
    "da li je tacno.\n\n"
    "STROGO ZABRANJENO:\n"
    "- da sam racunas ili provjeravas matematiku; koristi brojeve iz "
    "verified_facts SAMO da prepoznas na sta se ucenik poziva, nikad da "
    "izracunas nesto novo\n"
    "- da vratis konacnu ocjenu tacno/netacno\n"
    "- da izmislis brojeve koji nisu ni u poruci ni u verified_facts\n"
    "- da pises bilo kakav tekst za ucenika\n\n"
    "Vrati ISKLJUCIVO jedan JSON objekat, bez ikakvog drugog teksta, tacno "
    "ovog oblika:\n"
    "{\n"
    '  "understood": true/false,\n'
    '  "response_kind": "answer" | "explanation" | "question" | "task_request" | "other",\n'
    '  "decision": "yes" | "no" | "unknown",\n'
    '  "decision_source": "explicit" | "implicit" | "none",\n'
    '  "final_answer_text": "",\n'
    '  "claims": [{"type": "...", "value": "...", "polarity": "positive"|"negative", "confidence": 0.0}],\n'
    '  "explanation_present": true/false,\n'
    '  "relevance": "direct" | "partial" | "unrelated",\n'
    '  "completeness": "complete" | "partial" | "absent",\n'
    '  "ambiguity": "",\n'
    '  "certainty": "certain" | "uncertain",\n'
    '  "precision": "exact" | "approximate" | "unspecified",\n'
    '  "confidence": 0.0\n'
    "}\n"
    "Svaki claims[].type MORA biti iz allowed_claim_types konteksta. Ako "
    "nisi siguran sta je ucenik mislio, postavi understood=false i nizak "
    "confidence — nemoj nagadjati.\n\n"
    "certainty/precision: ako ucenik koristi rijeci nagadjanja ili "
    "priblizne vrijednosti (npr. otprilike, priblizno, oko, mozda, valjda, "
    "cini mi se), postavi certainty=\"uncertain\" i/ili precision="
    '"approximate", cak i kad je navedena vrijednost brojcano tacna. Kada je '
    "ucenik jasan i konkretan (npr. \"x je jedna polovina\", \"dobio sam "
    'pola\"), postavi certainty="certain", precision="exact". Ovo je jezicka '
    "procjena TVOJA, ne racunska — konacnu politiku primjenjuje sistem, ne ti."
)


def _strip_code_fence(raw: str) -> str:
    text = raw.strip()
    if not text.startswith("```"):
        return text
    text = text.strip("`")
    if text.lower().startswith("json"):
        text = text[4:]
    return text.strip()


def _parse_judgment(raw: str, allowed_claim_types: tuple[str, ...]
                    ) -> SemanticJudgment | None:
    try:
        data = json.loads(_strip_code_fence(raw))
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        understood = bool(data["understood"])
        response_kind = str(data["response_kind"])
        decision = str(data["decision"])
        decision_source = str(data["decision_source"])
        final_answer_text = str(data.get("final_answer_text") or "").strip()[:80]
        explanation_present = bool(data["explanation_present"])
        relevance = str(data["relevance"])
        completeness = str(data["completeness"])
        ambiguity = str(data.get("ambiguity") or "").strip()[:200]
        certainty = str(data.get("certainty") or "certain")
        precision = str(data.get("precision") or "unspecified")
        confidence = float(data["confidence"])
    except (KeyError, TypeError, ValueError):
        return None
    if response_kind not in _ALLOWED_RESPONSE_KINDS:
        return None
    if decision not in _ALLOWED_DECISIONS:
        return None
    if decision_source not in _ALLOWED_DECISION_SOURCES:
        return None
    if relevance not in _ALLOWED_RELEVANCE:
        return None
    if completeness not in _ALLOWED_COMPLETENESS:
        return None
    if certainty not in _ALLOWED_CERTAINTY:
        return None
    if precision not in _ALLOWED_PRECISION:
        return None
    if not (0.0 <= confidence <= 1.0):
        return None

    raw_claims = data.get("claims") or []
    if not isinstance(raw_claims, list):
        return None
    claims: list[Claim] = []
    allowed = set(allowed_claim_types) or ALLOWED_CLAIM_TYPES
    for item in raw_claims[:8]:
        if not isinstance(item, dict):
            return None
        try:
            ctype = str(item["type"])
            value = str(item.get("value") or "").strip()[:40]
            polarity = str(item["polarity"])
            cconf = float(item.get("confidence", 0.5))
        except (KeyError, TypeError, ValueError):
            return None
        if ctype not in ALLOWED_CLAIM_TYPES:
            return None
        if polarity not in _ALLOWED_POLARITY:
            return None
        if not (0.0 <= cconf <= 1.0):
            return None
        if ctype != "other" and ctype not in allowed:
            continue                     # outside this skill's vocabulary
        claims.append(Claim(type=ctype, value=value, polarity=polarity,
                            confidence=cconf))

    return SemanticJudgment(
        understood=understood, response_kind=response_kind, decision=decision,
        decision_source=decision_source, final_answer_text=final_answer_text,
        claims=tuple(claims), explanation_present=explanation_present,
        relevance=relevance, completeness=completeness, ambiguity=ambiguity,
        confidence=confidence, certainty=certainty, precision=precision)


@dataclass(frozen=True)
class CallMetrics:
    """Observability only — never chain-of-thought, never a grading input.
    Populated on every call attempt, success or failure, so latency/token
    measurement never depends on the judgment having parsed."""
    latency_ms: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    def to_telemetry(self) -> dict:
        return {"semantic_latency_ms": round(self.latency_ms, 1),
                "semantic_prompt_tokens": self.prompt_tokens,
                "semantic_completion_tokens": self.completion_tokens}


def _metrics_from(response: Any, started_at: float) -> CallMetrics:
    latency_ms = (time.monotonic() - started_at) * 1000.0
    usage = getattr(response, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
    completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
    return CallMetrics(latency_ms=latency_ms, prompt_tokens=prompt_tokens,
                       completion_tokens=completion_tokens)


def judge(context: GradingContext, *, openai_chat: Callable | None,
         model: str = "", timeout: float | None = None
         ) -> tuple[SemanticJudgment | None, str, CallMetrics]:
    """Interpret the message. Returns ``(judgment, fallback_reason, metrics)``.

    ``judgment`` is ``None`` on ANY failure — invalid JSON, a schema
    mismatch, an exception, a timeout, or confidence below the floor —
    exactly the "fail safe to not_checkable, never to correct" contract.
    ``metrics`` is ALWAYS populated (even on failure) for observability.
    Never raises.
    """
    if openai_chat is None:
        return None, "no_model", CallMetrics(latency_ms=0.0)
    started_at = time.monotonic()
    try:
        response = openai_chat(
            model,
            [{"role": "system", "content": _SYSTEM_PROMPT},
             {"role": "user", "content": json.dumps(context.to_payload(),
                                                    ensure_ascii=False)}],
            timeout=timeout or DEFAULT_TIMEOUT, max_tokens=MAX_TOKENS,
        )
        raw = (response.choices[0].message.content or "").strip()
    except Exception:
        return None, "model_error", CallMetrics(
            latency_ms=(time.monotonic() - started_at) * 1000.0)
    metrics = _metrics_from(response, started_at)
    if not raw:
        return None, "empty_response", metrics
    judgment = _parse_judgment(raw, context.allowed_claim_types)
    if judgment is None:
        return None, "invalid_json", metrics
    if judgment.confidence < MIN_CONFIDENCE:
        return None, "low_confidence", metrics
    return judgment, "", metrics


# --------------------------------------------------------------------------- #
# Context builders — SKILL-SPECIFIC facts, shared shape                       #
# --------------------------------------------------------------------------- #
def build_context(task: Any, student_message: str) -> GradingContext | None:
    """Facts are always plain arithmetic already computed elsewhere in the
    engine — this function never invents a new source of truth."""
    skill_id = getattr(task, "skill_id", "")
    if skill_id == "divisibility":
        return _build_divisibility_context(task, student_message)
    if skill_id in ("linear_equation", "fraction_equation_additive"):
        return _build_equation_context(task, student_message)
    if skill_id in ("fraction_expand", "fraction_add_unlike"):
        return _build_rational_context(task, student_message)
    return None


def _build_divisibility_context(task: Any, student_message: str
                                ) -> GradingContext | None:
    facts = df.resolve_divisibility_facts(task.question)
    if facts is None:
        return None
    factor_results = {str(f): df.satisfies(facts.n, f) for f in facts.factors}
    factor_results[str(facts.divisor)] = facts.holds
    verified = {
        "number": facts.n, "divisor": facts.divisor,
        "correct_decision": facts.holds,
        "last_digit": df.last_digit(facts.n),
        "digit_sum": df.digit_sum(facts.n),
        "last_two_digits": df.last_two_digits(facts.n),
        "factors": list(facts.factors), "factor_results": factor_results,
    }
    # SKILL-SPECIFIC policy: which components are SUFFICIENT, named by factor.
    required = tuple(f"factor_{f}" for f in facts.factors)
    return GradingContext(
        skill_id="divisibility", answer_type="boolean_with_explanation",
        task_text=task.question, student_message=student_message,
        expected_answer=("da" if facts.holds else "ne"),
        verified_facts=verified, required_components=required,
        allowed_claim_types=("last_digit", "digit_sum", "parity",
                            "last_two_digits", "divisibility_factor", "other"))


def _build_equation_context(task: Any, student_message: str) -> GradingContext:
    verified = {"expected_solution": task.expected_display}
    facts = sf.resolve_equation_facts(task.question)
    if facts is not None:
        if facts.subtracted_unknown:
            op = "isolate_subtrahend"
        else:
            op = "add" if facts.removes_by_adding else "subtract"
        verified.update(first_operation=op,
                       intermediate=facts.intermediate_equation)
    return GradingContext(
        skill_id=task.skill_id, answer_type="equation_solution",
        task_text=task.question, student_message=student_message,
        expected_answer=task.expected_display, verified_facts=verified,
        required_components=(),
        allowed_claim_types=("equation_step", "rational_result", "other"))


def _build_rational_context(task: Any, student_message: str) -> GradingContext:
    return GradingContext(
        skill_id=task.skill_id, answer_type="rational",
        task_text=task.question, student_message=student_message,
        expected_answer=task.expected_display,
        verified_facts={"expected_value": task.expected_display},
        required_components=(),
        allowed_claim_types=("rational_result", "other"))


# --------------------------------------------------------------------------- #
# Deterministic claim VERIFICATION — SHARED engine, skill-specific facts      #
# --------------------------------------------------------------------------- #
def _claim_covers_factor(claim: Claim, factor: int, facts: dict) -> bool | None:
    """True/False if this claim's stated value matches reality for
    ``factor``; ``None`` if the claim does not pertain to it at all.

    This is the ONE place a claim TYPE is checked against a fact of the same
    type — reused for every divisor, compound or not, so a claim about
    factor 2 is verified identically whether it is answering "sa 2" directly
    or standing in for half of the divisor-6 rule.
    """
    if claim.type == "divisibility_factor":
        try:
            named = int(claim.value)
        except ValueError:
            return None
        if named != factor:
            return None
        actual = facts["factor_results"].get(str(factor))
        if actual is None:
            return None
        claimed_holds = claim.polarity == "positive"
        return claimed_holds == actual
    if claim.type == "last_digit" and factor in (2, 5, 10):
        try:
            stated = int(claim.value) % 10
        except ValueError:
            return None
        return stated == facts["last_digit"]
    if claim.type == "parity" and factor == 2:
        value = claim.value.strip().lower()
        if value in ("even", "paran", "parno", "parni"):
            wants_even = True
        elif value in ("odd", "neparan", "neparno", "neparni"):
            wants_even = False
        else:
            return None
        return wants_even == (facts["last_digit"] % 2 == 0)
    if claim.type == "digit_sum" and factor in (3, 9):
        try:
            stated = int(claim.value)
        except ValueError:
            return None
        return stated == facts["digit_sum"]
    if claim.type == "last_two_digits" and factor in (4, 25):
        try:
            stated = int(claim.value)
        except ValueError:
            return None
        return stated == facts["last_two_digits"]
    return None


def _factor_status(claims: tuple[Claim, ...], factor: int, facts: dict) -> str:
    """"satisfied" | "contradicted" | "unaddressed" for one factor."""
    contradicted = satisfied = False
    for claim in claims:
        result = _claim_covers_factor(claim, factor, facts)
        if result is True:
            satisfied = True
        elif result is False:
            contradicted = True
    if contradicted:
        return "contradicted"
    return "satisfied" if satisfied else "unaddressed"


def _verify_boolean_with_explanation(context: GradingContext,
                                     judgment: SemanticJudgment) -> VerifiedOutcome:
    if judgment.response_kind not in ("answer", "explanation"):
        return _not_checkable()
    if not judgment.understood or judgment.decision == "unknown":
        return _not_checkable()

    facts = context.verified_facts
    correct_decision = bool(facts["correct_decision"])
    said_yes = judgment.decision == "yes"
    graded_answer = "da" if said_yes else "ne"

    if said_yes != correct_decision:
        return VerifiedOutcome(checkable=True, verdict="incorrect",
                               detail="incorrect", graded_answer=graded_answer,
                               is_attempt=True, is_wrong_attempt=True)

    factors = facts.get("factors") or []
    statuses = {f: _factor_status(judgment.claims, f, facts) for f in factors}
    if any(status == "contradicted" for status in statuses.values()):
        # the DECISION is right but the cited evidence is factually wrong for
        # THIS number ("da jer je zadnja cifra 5" when it is actually 0).
        return VerifiedOutcome(checkable=True, verdict="partial",
                               detail="incorrect_evidence",
                               graded_answer=graded_answer,
                               is_attempt=True, is_wrong_attempt=False)

    if correct_decision:
        sufficient = bool(factors) and all(
            statuses[f] == "satisfied" for f in factors)
    else:
        # one verified FAILING condition is enough to explain a "no".
        sufficient = any(statuses[f] == "satisfied" for f in factors)

    if sufficient:
        return VerifiedOutcome(checkable=True, verdict="correct",
                               detail="correct", graded_answer=graded_answer,
                               is_attempt=True, is_wrong_attempt=False)
    return VerifiedOutcome(checkable=True, verdict="partial",
                           detail="incomplete", graded_answer=graded_answer,
                           is_attempt=True, is_wrong_attempt=False)


#: A declared verdict of "needs_confirmation" — the matching VALUE was found,
#: but hedged ("otprilike", "možda") — must never count as a wrong attempt:
#: the student is not wrong, just not yet committing to an exact answer.
NEEDS_CONFIRMATION = "needs_confirmation"


def _verify_rational_like(context: GradingContext,
                          judgment: SemanticJudgment) -> VerifiedOutcome:
    """Shared for ``rational`` and ``equation_solution``: a single declared
    final value, compared as a Fraction — no claim decomposition needed.

    An EXACT task (the default — ``context.allows_approximate`` is False for
    every family currently wired) does not award full independent-solution
    credit for hedged language ("otprilike pola") merely because the nearby
    value happens to match; it asks for confirmation instead. False
    approximate values are still simply incorrect.
    """
    if judgment.response_kind not in ("answer", "explanation"):
        return _not_checkable()
    if not judgment.understood:
        return _not_checkable()
    candidate = judgment.final_answer_text or next(
        (c.value for c in judgment.claims if c.type == "rational_result"), "")
    if not candidate:
        return _not_checkable()
    try:
        given = Fraction(candidate.replace(" ", ""))
        expected = Fraction(str(context.expected_answer).replace(" ", ""))
    except (ValueError, ZeroDivisionError):
        return _not_checkable()

    value_matches = given == expected
    hedged = (judgment.certainty == "uncertain"
             or judgment.precision == "approximate")
    if hedged and not context.allows_approximate:
        if value_matches:
            return VerifiedOutcome(checkable=True, verdict="partial",
                                   detail=NEEDS_CONFIRMATION,
                                   graded_answer=candidate,
                                   is_attempt=True, is_wrong_attempt=False)
        return VerifiedOutcome(checkable=True, verdict="incorrect",
                               detail="incorrect", graded_answer=candidate,
                               is_attempt=True, is_wrong_attempt=True)

    if value_matches:
        return VerifiedOutcome(checkable=True, verdict="correct",
                               detail="correct", graded_answer=candidate,
                               is_attempt=True, is_wrong_attempt=False)
    return VerifiedOutcome(checkable=True, verdict="incorrect",
                           detail="incorrect", graded_answer=candidate,
                           is_attempt=True, is_wrong_attempt=True)


#: A message that is JUST a value the deterministic parser already handles
#: cleanly — "6", "11/15", "x=4", a bare "da"/"ne" — carries nothing for the
#: judge to interpret. This is the ONE shared bare-token detector every
#: eligibility check (shadow audit, the "incomplete" fallback) reuses, so
#: "zero model calls for a bare answer" is a single rule, not one regex per
#: call site.
_BARE_TOKEN_RE = re.compile(
    r"^\s*(?:x\s*=\s*)?-?\d+(?:\s*/\s*\d+)?\s*[.!]?\s*$"
    r"|^\s*(?:da|ne|jeste|nije|jest|tacno|netacno)\s*[.!]?\s*$",
    re.IGNORECASE)


def is_prose_like(raw: str) -> bool:
    """True when the message carries more than a single bare numeric/boolean
    token — i.e. actual natural language exists for the judge to interpret."""
    text = str(raw or "").strip()
    if not text:
        return False
    return not bool(_BARE_TOKEN_RE.match(text))


#: The taxonomy requested for shadow auditing. "none" = no disagreement worth
#: flagging (includes: judge ran but agrees, or judge could not establish
#: anything different from the deterministic result).
SHADOW_DISAGREEMENT_TYPES = (
    "none", "deterministic_wrong_semantic_correct",
    "deterministic_wrong_semantic_non_answer",
    "deterministic_partial_semantic_correct",
    "deterministic_correct_semantic_disagrees", "semantic_unavailable",
)


def classify_shadow_disagreement(deterministic_verdict: str,
                                 judgment: SemanticJudgment | None,
                                 outcome: VerifiedOutcome | None) -> str:
    """Audit-only classification — NEVER used to change a verdict.

    Compares what the deterministic path decided against what the judge +
    deterministic verifier independently concluded, so shadow evaluation can
    surface false negatives (a confident "incorrect" that was actually a
    non-answer or a correctly-explained answer) without touching production
    behaviour.
    """
    if judgment is None:
        return "semantic_unavailable"
    if judgment.response_kind not in ("answer", "explanation"):
        # NOT_CHECKABLE ("unverified") is the checker giving up, not a real
        # grading decision — a non-answer comment landing there is exactly
        # what the judge SHOULD confirm, so it counts as the same disagreement
        # type as a confident "incorrect" reaching the same conclusion.
        if deterministic_verdict in ("incorrect", "unverified"):
            return "deterministic_wrong_semantic_non_answer"
        return "none"
    if outcome is None or not outcome.checkable:
        return "none"
    if deterministic_verdict == "incorrect" and outcome.verdict == "correct":
        return "deterministic_wrong_semantic_correct"
    if deterministic_verdict == "partial" and outcome.verdict == "correct":
        return "deterministic_partial_semantic_correct"
    if deterministic_verdict == "correct" and outcome.verdict != "correct":
        return "deterministic_correct_semantic_disagrees"
    return "none"


def verify_claims(context: GradingContext,
                  judgment: SemanticJudgment) -> VerifiedOutcome:
    """The single deterministic entry point every answer family goes through.

    Dispatches by ``answer_type`` — the SHARED verification ENGINE
    (``_claim_covers_factor``, the yes/no and sufficiency rules) is reused;
    only the FACTS and the sufficiency policy are skill-specific, and those
    already came from ``build_context``.
    """
    if context.answer_type == "boolean_with_explanation":
        return _verify_boolean_with_explanation(context, judgment)
    if context.answer_type in ("rational", "equation_solution"):
        return _verify_rational_like(context, judgment)
    return _not_checkable()
