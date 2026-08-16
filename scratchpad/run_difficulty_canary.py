"""Isolated, manual-only real-model canary for the Universal Difficulty
Controller (Phase A).

WHAT THIS IS
------------
A standalone script that exercises the REAL, unmodified Practice entrypoint
(`matbot.practice.run_practice_turn`), the REAL OpenAI client adapter
(`matbot.llm.OpenAIPracticeLLM`), the REAL in-memory session store
(`matbot.session_store.SessionStore`), REAL curriculum data
(`matbot.topics`, `matbot.contracts.registry`), and the REAL response
schemas — nothing here is a parallel/fake reimplementation of the Practice
pipeline. It only ADDS: a thin SDK-call-counting wrapper around the LLM
object, a log-capture handler for failure classification, and a fixed,
small scenario list.

This script makes REAL OpenAI API calls and therefore real spend. It is
NEVER imported by the application and NEVER run automatically — it must be
launched manually, on purpose, by a human who has already exported
OPENAI_API_KEY in their own shell session.

REVISION 2 (after the 14-call initial campaign) — what changed and why
----------------------------------------------------------------------
The first campaign spent 14 calls, ran 9 turns, published 6 and rejected 3.
The difficulty controller itself showed ZERO failures (no state, direction,
Reviewer, lesson or call-budget failure). Three defects were in THIS RUNNER,
not in the controller, and are fixed here:

  1. NO FAILURE CLASSIFICATION. All three rejections were OpenAI
     `APITimeoutError` at the 30 s `AI_TUTOR_TIMEOUT` boundary
     (`category=llm_timeout`, `latency_ms=30016`), but the JSON recorded
     `error: null` and only the generic student-facing safe message, making
     an infrastructure timeout indistinguishable from a real controller
     rejection. Fixed by `_LogCapture` + `_classify_failure` (§1 below).

  2. SCENARIO NAME OVERRODE ACTUAL STATE. The turn named
     `contract_level3_boundary_harder` actually ran a 2→3 transition,
     because the preceding 2→3 turn had timed out and the session was still
     committed at Level 2. The transition fields were recorded correctly
     (previous_level=2, target_level=3), but the intended Level-3 BOUNDARY
     case was silently never tested. Fixed by declarative
     `requires_committed_level` prerequisites that consume ZERO SDK calls
     when unmet (§2 below).

  3. CONSOLE ENCODING KILLED THE VERDICT. Printing a published task
     containing 'č' (U+010D) raised `UnicodeEncodeError` on the cp1252
     console AFTER the JSON had been written, so the run exited non-zero
     with no verdict line despite fully successful result persistence.
     Fixed by UTF-8 stream reconfiguration + `_safe_print` + a
     persistence-first ordering that guarantees the verdict always prints
     (§3 below).

Also added: non-blocking output-quality flags (§5) — these are recorded and
printed for human review but NEVER change the verdict, because sentence
naturalness and LaTeX spacing are output-quality concerns, not difficulty-
controller correctness, unless they render the task ambiguous or wrong.

HARD SAFETY PROPERTIES
-----------------------
- Refuses to start unless OPENAI_API_KEY is PRESENT (boolean check only —
  this script never reads, prints, logs, hashes, or persists the value).
- Refuses to start unless MATBOT_PRACTICE_DIFFICULTY_LEVELS=enabled (exact
  match, same normalization as matbot.practice._difficulty_levels_enabled).
- Refuses to start if MATBOT_PRACTICE_PIPELINE=universal_two_call is active.
- Hard ceiling on actual SDK model-call entries, counted at the real
  invocation boundary (inside the LLM wrapper's practice_turn /
  lesson_fidelity_turn methods — i.e. immediately before each
  client.responses.parse() call), never estimated from turn/task type.
- Stops BEFORE starting any turn that could exceed the remaining budget.
- No retries beyond the application's own existing behaviour (none added
  here); no repair call; no third call — enforced by construction, since
  this script never calls the LLM directly, only through
  matbot.practice.run_practice_turn, which already enforces this. A
  scenario that fails is NEVER retried or replaced.
- Keeps the application's existing 30 s timeout (`AI_TUTOR_TIMEOUT`)
  untouched; a timeout is CLASSIFIED as `api_timeout`, never silently
  folded into `unknown_rejection`.
- Never prints secrets, request headers, full environment dumps, API
  credentials, or stack traces. Captured diagnostics come from the
  application's own already-scrubbed log lines (`matbot.llm._scrub`).

USAGE (manual only)
--------------------
    From the repository root, in a PowerShell process where
    OPENAI_API_KEY is already exported:

        $env:MATBOT_PRACTICE_DIFFICULTY_LEVELS = "enabled"
        .venv\\Scripts\\python.exe scratchpad\\run_difficulty_canary.py

    Optional campaign selector (default is the 10-call follow-up):

        --campaign followup            10 SDK calls max (DEFAULT)
        --campaign initial             14 SDK calls max (the original campaign)
        --campaign divisibility-final   4 SDK calls max (answer_kind fix canary)

    Do NOT set MATBOT_PRACTICE_PIPELINE — leave it unset so the active
    legacy_single_call path is used, exactly like production.

OUTPUT
------
    scratchpad/difficulty_canary_results.json   (machine-readable)
    stdout                                       (concise human report,
                                                   ending in exactly one of
                                                   the three verdict lines)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = Path(__file__).resolve().parent / "difficulty_canary_results.json"
RECHECKED_RESULTS_PATH = Path(__file__).resolve().parent / "difficulty_canary_results_rechecked.json"

MAX_NON_CONTRACT_CALLS_PER_TURN = 2   # Tutor + selective Lesson Fidelity Reviewer
MAX_CONTRACT_CALLS_PER_TURN = 1       # deterministic skeleton + one prose call

# Mirrors matbot.schema.NewTask.answer_kind. This runner must reject malformed
# metadata even when a visible correct answer is descriptive and therefore
# cannot be mechanically classified by detected_answer_kind().
VALID_ANSWER_KINDS = frozenset({
    "integer", "decimal", "fraction", "ordered_pair", "expression",
    "formula", "option_label", "short_text",
})


# ---------------------------------------------------------------------------
# §3 — UTF-8 SAFE REPORTING (installed first, before anything can print)
#
# The Windows console defaults to cp1252, which cannot encode Bosnian
# diacritics (č/ć/ž/š/đ). In the first campaign this raised
# UnicodeEncodeError *after* the JSON had already been persisted, producing
# an unrelated non-zero exit and no verdict line. Three layers of defence:
#   1. reconfigure stdout/stderr to UTF-8 with errors="replace";
#   2. _safe_print() re-encodes defensively if a write still fails;
#   3. main() persists JSON BEFORE the human report, and always prints the
#      verdict, so a console problem can never change or hide the result.
# ---------------------------------------------------------------------------

def _install_utf8_streams() -> bool:
    ok = True
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            ok = False
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            ok = False
    return ok


_UTF8_STREAMS_OK = _install_utf8_streams()


def _safe_print(text: str = "") -> None:
    """Print that can never raise on a narrow console. Last-resort fallback
    escapes non-encodable characters instead of losing the line."""
    try:
        print(text)
        return
    except UnicodeEncodeError:
        pass
    except Exception:
        pass
    try:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        sys.stdout.write(text.encode(encoding, "backslashreplace").decode(encoding, "replace") + "\n")
    except Exception:
        try:
            sys.stdout.write(text.encode("ascii", "backslashreplace").decode("ascii") + "\n")
        except Exception:
            pass  # reporting must never abort the run


# ---------------------------------------------------------------------------
# Precondition checks — plain os.environ only. Called from __main__ (never at
# import time), so a static/import check never fails on an unconfigured env.
# ---------------------------------------------------------------------------

class PreconditionError(RuntimeError):
    pass


def _check_preconditions() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        raise PreconditionError(
            "OPENAI_API_KEY is not present in this process's environment. "
            "Export it in THIS PowerShell session before running this script. "
            "(This script only ever checks presence — it never reads the value.)"
        )
    difficulty_flag = (os.environ.get("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "") or "").strip().lower()
    if difficulty_flag != "enabled":
        raise PreconditionError(
            "MATBOT_PRACTICE_DIFFICULTY_LEVELS must be exactly 'enabled' for this "
            f"canary (got {difficulty_flag!r}). Set it in THIS process only:\n"
            '    $env:MATBOT_PRACTICE_DIFFICULTY_LEVELS = "enabled"'
        )
    pipeline_flag = (os.environ.get("MATBOT_PRACTICE_PIPELINE", "") or "").strip().lower()
    if pipeline_flag == "universal_two_call":
        raise PreconditionError(
            "MATBOT_PRACTICE_PIPELINE=universal_two_call is active. This canary is "
            "for the active legacy_single_call + selective Reviewer path only — "
            "unset MATBOT_PRACTICE_PIPELINE and re-run."
        )


# Safe to import the application — none of this makes a model call.
sys.path.insert(0, str(ROOT))

from matbot import (config, difficulty_level, feedback, lesson_fidelity, mathsafe,
                    mcq_integrity, practice)  # noqa: E402
from matbot.contracts import registry as contract_registry  # noqa: E402
from matbot.llm import LLMError, OpenAIPracticeLLM, safe_failure_diagnostics  # noqa: E402
from matbot.mathcheck import find_numeric_inconsistencies  # noqa: E402
from matbot.mathsegments import DISPLAY, INLINE, TEXT, tokenize_math  # noqa: E402
from matbot.session_store import SessionStore  # noqa: E402
from matbot.answer_kind import (  # noqa: E402
    canonical_answer_kind, detected_answer_kind,
)
from matbot.topics import lesson_info  # noqa: E402
from matbot.tutor import lesson_context as tutor_lesson_context  # noqa: E402
from matbot.tutor import pipeline as tutor_pipeline  # noqa: E402
from matbot.tutor import pipeline as tutor_pipeline_module  # noqa: E402
from matbot.tutor.schema import difficulty_evidence_errors  # noqa: E402


# ---------------------------------------------------------------------------
# §1 — FAILURE CLASSIFICATION
#
# run_practice_turn deliberately returns only the canned safe message to the
# caller (that is the product contract and must not change). The real reason
# is already emitted on the `matbot` logger, ALREADY SCRUBBED of secrets by
# matbot.llm._scrub and length-bounded by matbot.practice._clip_for_log.
# Capturing those records is therefore the only non-invasive way to classify,
# and it exposes nothing the application does not already log itself.
# ---------------------------------------------------------------------------

FAILURE_CLASSES = (
    "api_timeout",
    "api_error",
    "schema_or_parse_error",
    "llm_schema_parse_error",
    "llm_empty_output",
    "llm_incomplete_max_output_tokens",
    "llm_refusal",
    "llm_invalid_output_unknown",
    "tutor_payload_rejection",
    "reviewer_payload_rejection",
    "reviewer_final_mcq_integrity_rejection",
    "reviewer_fail_closed_rejection",
    "publication_validation_rejection",
    "reviewer_rejection",
    "deterministic_validation_rejection",
    "target_profile_rejection",
    "duplicate_rejection",
    "family_contract_rejection",
    "unmet_prerequisite",
    "reporting_error",
    "unknown_rejection",
)

# Only these log-line prefixes are ever copied into the report. Everything the
# application logs under them is already bounded and scrubbed.
_SAFE_LOG_PREFIXES = (
    "practice_choice ", "practice_contract_rejected ",
    "lesson_fidelity ", "practice_plan ", "practice_duplicate_options ",
    "practice_system_verification ", "practice_difficulty_label_mismatch ",
    "tutor_rejected ",
    # Serverski nalazi o Tutorovom nacrtu: samo kodovi i ID-jevi opcija, nikad
    # tekst zadatka, opcije ni rješenje. Vidljivi su i kad turn USPIJE, pa se u
    # izvještaju vidi je li recenzent stvarno popravio prijavljeni defekt.
    "tutor_draft_preflight ",
)
_MAX_DIAGNOSTIC_CHARS = 400


class _LogCapture(logging.Handler):
    """Collects `matbot.*` log messages for the duration of one turn."""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.messages: list[str] = []

    def emit(self, record):  # noqa: D102
        try:
            self.messages.append(record.getMessage())
        except Exception:
            pass  # diagnostics must never break the campaign

    def reset(self) -> None:
        self.messages = []

    def safe_diagnostics(self) -> list[str]:
        out = []
        for message in self.messages:
            if not message.startswith(_SAFE_LOG_PREFIXES):
                continue
            out.append(message[:_MAX_DIAGNOSTIC_CHARS])
        return out


def _classify_failure(messages: list[str]) -> str:
    """Map captured application log lines to one FAILURE_CLASSES value.

    Order is specific → generic. A timeout is matched FIRST and explicitly,
    so infrastructure flakiness can never be misreported as a controller
    rejection (the exact defect this revision fixes)."""
    blob = " || ".join(messages)

    if "category=llm_timeout" in blob or "APITimeoutError" in blob:
        return "api_timeout"
    if "category=llm_sdk_error" in blob:
        return "api_error"
    for category in ("llm_schema_parse_error", "llm_empty_output",
                     "llm_incomplete_max_output_tokens", "llm_refusal",
                     "llm_invalid_output_unknown"):
        if f"category={category}" in blob:
            return "schema_or_parse_error"
    if "target_profile_mismatch" in blob:
        return "target_profile_rejection"
    if "family_contract_mismatch" in blob:
        return "family_contract_rejection"
    if "ponovljen tekst zadatka" in blob or "pedagogical_shape_repeat" in blob:
        return "duplicate_rejection"
    if "lesson_fidelity:" in blob:
        return "reviewer_rejection"
    # Recenzentov KONAČAN paket je i dalje nosio dokazan MCQ defekt. Mora se
    # razlikovati od obične završne validacije: krivac je drugi poziv, ne objava.
    # Provjerava se PRIJE `stage=publication`, jer objava ostaje odbrana u dubini
    # i isti turn može ostaviti oba reda u logu.
    if "reviewer_final_mcq_integrity_rejection" in blob:
        return "reviewer_final_mcq_integrity_rejection"
    if "stage=publication" in blob:
        return "publication_validation_rejection"
    if "stage=reviewer_fail_closed" in blob:
        return "reviewer_fail_closed_rejection"
    if "category=invalid_output" in blob:
        return "deterministic_validation_rejection"
    if "stage=tutor_draft" in blob:
        return "tutor_payload_rejection"
    if "stage=reviewer_" in blob:
        return "reviewer_payload_rejection"
    return "unknown_rejection"


def _classify_llm_failure(category: Optional[str]) -> str:
    """Keep adapter categories visible instead of collapsing them to unknown."""
    return category or "unknown_rejection"


# Classes that are INFRASTRUCTURE, not difficulty-controller behaviour. These
# are reported separately and never counted as controller defects.
_INFRASTRUCTURE_CLASSES = frozenset({"api_timeout", "api_error", "llm_timeout", "llm_sdk_error"})


class SDKCallBudgetExceeded(RuntimeError):
    pass


class CountingLLM:
    """Thin wrapper around the REAL OpenAIPracticeLLM. Adds exactly one
    behavioural thing: a hard, exact SDK-call counter checked at the real
    invocation boundary. Does not change retry/repair/call-budget behaviour —
    matbot.practice already enforces "no third call"; this wrapper observes
    and, as an extra safety net, refuses to let a call through once the
    canary's own ceiling would be exceeded.

    Also captures the last raw structured output per call kind so the canary
    can report the Tutor-declared label / Reviewer decision & checks without
    any change to matbot/practice.py."""

    def __init__(self, inner: OpenAIPracticeLLM, ceiling: int):
        self._inner = inner
        self.ceiling = ceiling
        self.call_count = 0
        self.call_log: list[str] = []
        self.last_tutor_output = None
        self.last_reviewer_output = None
        self.last_failure = None

    def _count(self, method_name: str) -> None:
        if self.call_count + 1 > self.ceiling:
            raise SDKCallBudgetExceeded(
                f"refusing SDK call #{self.call_count + 1} ({method_name}): "
                f"hard ceiling of {self.ceiling} would be exceeded"
            )
        self.call_count += 1
        self.call_log.append(method_name)


    def fast_turn(self, instructions, input_text, timeout_s=None):
        # BRZA RUTA SE BROJI KAO I SVAKA DRUGA. Bez ovog omotača `fast_turn` bi
        # prošao pored brojača (klasa nema `__getattr__`), pa bi zvanična kapija
        # ili pukla na AttributeError ili — gore — mjerila arhitekturu koju
        # produkcija ne izvršava. Plafon smije imati samo jedna vrata.
        self._count("fast_turn")
        result = self._call("tutor", self._inner.fast_turn, instructions, input_text,
                            timeout_s=timeout_s)
        self.last_tutor_output = result.output
        return result


    # Defensive pass-throughs for the inactive universal_two_call path —
    # unreachable (refused at precondition check), counted anyway so the
    # ceiling can never be silently bypassed if that ever changes.
    # Candidate structured Tutor/Reviewer calls are counted inside the same
    # hard ceiling when the release gate explicitly selects that runtime.
    def tutor_turn(self, instructions, input_text):
        self._count("tutor_turn")
        result = self._call("tutor", self._inner.tutor_turn, instructions, input_text)
        self.last_tutor_output = result.output
        return result

    def reviewer_turn(self, instructions, input_text, timeout_s=None,
                      model=None, reasoning_effort=None):
        # Faza 4H: pipeline prosljeđuje SUŽEN rok ostatka turna; brojanje i
        # klasifikacija poziva se ne mijenjaju.
        self._count("reviewer_turn")
        result = self._call("reviewer", self._inner.reviewer_turn, instructions,
                            input_text, timeout_s=timeout_s)
        self.last_reviewer_output = result.output
        return result

    def kontrolni_turn(self, instructions, input_text, timeout_s=None):
        # „Sutra imam kontrolni“ (v1): batch poziv se broji kroz ISTA vrata kao
        # svaki drugi — plafon kapije ne smije imati sporedni ulaz.
        # `timeout_s` (2026-08-16): suženi rok popravke se prosljeđuje
        # netaknut; brojanje se ne mijenja.
        self._count("kontrolni_turn")
        return self._call("kontrolni", self._inner.kontrolni_turn,
                          instructions, input_text, timeout_s=timeout_s)

    def explain_turn(self, instructions, input_text):
        # „Objasni mi“ (proširenje pokrivenosti kapije): isti brojač, isti
        # plafon. Bez ovog omotača Explain scenario bi pukao na AttributeError
        # ili — gore — prošao pored jedinih vrata do plafona.
        self._count("explain_turn")
        return self._call("explain", self._inner.explain_turn,
                          instructions, input_text)

    def quick_turn(self, instructions, input_text, image=None):
        # „Samo rezultat“: NOVA slika ide na Sol, tekst na Lunu. Ime faze ih
        # razdvaja da kapija može dokazati da u JEDNOM turnu nikad ne rade oba.
        self._count("quick_image_turn" if image is not None else "quick_turn")
        return self._call("quick", self._inner.quick_turn, instructions,
                          input_text, image=image)

    def _call(self, stage, method, instructions, input_text, **kwargs):
        try:
            return method(instructions, input_text, **kwargs)
        except LLMError as error:
            # The adapter already owns the scrubbed allow-list.  Retain only
            # that data so a rejected turn can be classified after Practice
            # deliberately hides the implementation error from the student.
            self.last_failure = {
                "stage": stage,
                "category": getattr(error, "category", "llm_error"),
                "diagnostics": safe_failure_diagnostics(error),
            }
            raise


# ---------------------------------------------------------------------------
# §5 — NON-BLOCKING OUTPUT-QUALITY FLAGS
#
# Recorded and printed for human review. NEVER affect the verdict: sentence
# naturalness and LaTeX spacing are output-quality concerns, not difficulty-
# controller correctness, unless they make the task ambiguous or wrong (which
# a human decides from the printed text, not this script).
# ---------------------------------------------------------------------------

# Characters that should never sit directly against an OPENING '$'. Covers
# both patterns named in review: a letter/digit ("sa$a=7$") and trailing
# punctuation ("Izračunaj:$x$"). Whitespace and openers like '(' are fine.
_TIGHT_BEFORE_MATH_RE = re.compile(r"[0-9A-Za-zČĆŽŠĐčćžšđ:,;.!?]")

# Imperatives are matched with and without diacritics — a model writing
# "Izracunaj" must not raise a spurious "no question" flag.
_IMPERATIVE_RE = re.compile(
    r"(?i)\b(izra[čc]unaj|odredi|dopuni|rije[šs]i|pro[šs]iri|skrati|koliko|koji|koja|je\s+li|da\s+li)\b"
)


def _content_quality_flags(task_text: str, expected_answer: Optional[str]) -> list[str]:
    flags: list[str] = []
    text = task_text or ""

    # Missing whitespace immediately before an OPENING math delimiter
    # (e.g. "sa$a=7$" or "Izračunaj:$x$"). Uses the shared tokenizer so a
    # CLOSING '$' is never mistaken for an opening one.
    segments = tokenize_math(text)
    rendered = ""
    for kind, content in segments:
        if kind in (INLINE, DISPLAY) and rendered:
            if _TIGHT_BEFORE_MATH_RE.match(rendered[-1]):
                flags.append(f"missing_space_before_math:…{rendered[-12:]!r}")
                break
        rendered += content if kind == TEXT else f"${content}$"

    # Visibly malformed LaTeX. A published task already passed the sanitizer,
    # so a non-empty result here would indicate real drift worth reporting.
    try:
        issues = mathsafe.find_unsafe_math_issues(text)
        if issues:
            flags.append(f"malformed_latex:{issues[:3]}")
    except Exception:
        flags.append("malformed_latex_check_failed")

    # Narrow, mechanical answer/task sanity checks — deliberately NOT an
    # attempt to verify the mathematics (that stays a human judgement here).
    if not (expected_answer or "").strip():
        flags.append("empty_expected_answer")
    if "?" not in text and not _IMPERATIVE_RE.search(text):
        flags.append("no_visible_question_or_imperative")

    return flags


@dataclass
class TurnResult:
    scenario: str
    lesson_id: str
    lesson_title: str
    path: str                       # "contract" | "non_contract"
    grade: int
    request_type: str               # "" | "harder" | "easier"
    attempted: bool = False
    previous_level: Optional[int] = None
    target_level: Optional[int] = None
    level_changed: Optional[bool] = None
    boundary_reason: Optional[str] = None
    generation_changed: Optional[bool] = None
    tutor_declared_label: Optional[str] = None
    tutor_declared_answer_kind: Optional[str] = None
    effective_server_label: Optional[str] = None
    reviewer_decision: Optional[str] = None
    reviewer_checks: Optional[dict] = None
    reviewer_corrected_task_answer_kind: Optional[str] = None
    final_task_answer_kind_source: Optional[str] = None
    tutor_proposed_target_level: Optional[int] = None
    reviewer_final_target_level: Optional[int] = None
    canonical_context_lesson_id: Optional[str] = None
    canonical_context_lesson_title: Optional[str] = None
    tutor_returned_lesson_id: Optional[str] = None
    tutor_title_matched_canonical: Optional[bool] = None
    reviewer_final_lesson_id: Optional[str] = None
    reviewer_final_title_matched_canonical: Optional[bool] = None
    title_canonicalized: Optional[bool] = None
    publication_validation_category: Optional[str] = None
    final_structured_package_source: Optional[str] = None
    final_difficulty_evidence: Optional[dict] = None
    tutor_difficulty_evidence: Optional[dict] = None
    reviewer_difficulty_evidence: Optional[dict] = None
    difficulty_evidence_matched: Optional[bool] = None
    difficulty_evidence_differing_fields: list = field(default_factory=list)
    difficulty_evidence_corrected: Optional[bool] = None
    final_difficulty_evidence_source: Optional[str] = None
    final_difficulty_target_level: Optional[int] = None
    final_difficulty_validator_errors: list = field(default_factory=list)
    final_task_signature: Optional[dict] = None
    final_task_signature_canonical: Optional[str] = None
    structured_package_validation_passed: Optional[bool] = None
    structured_package_validation_errors: list = field(default_factory=list)
    committed_task_signature_matches_final: Optional[bool] = None
    final_declared_answer_kind: Optional[str] = None
    final_canonical_answer_kind: Optional[str] = None
    final_detected_answer_kind: Optional[str] = None
    answer_metadata_consistent: Optional[bool] = None
    answer_kind_diagnostics: list = field(default_factory=list)
    tutor_proposed_task_text: Optional[str] = None
    reviewer_corrected_task_text: Optional[str] = None
    derived_semantic_requirement: Optional[str] = None
    deterministic_semantic_rejection_reason: Optional[str] = None
    sdk_calls_before_turn: int = 0
    sdk_calls_after_turn: int = 0
    sdk_calls_this_turn: int = 0
    # Imena SDK faza baš ovog turna. Broj poziva ne razlikuje recenzentski
    # POPRAVAK od skrivenog ponavljanja tutorskog poziva; sastav razlikuje.
    sdk_call_stages: list = field(default_factory=list)
    published: bool = False
    answer_text: Optional[str] = None
    published_task_text: Optional[str] = None
    expected_answer: Optional[str] = None
    next_state_options: list = field(default_factory=list)
    next_state_options_match_session: Optional[bool] = None
    visible_correct_option_value: Optional[str] = None
    model_marked_option_value: Optional[str] = None
    answer_verdict: Optional[str] = None
    internal_correct_option_id_before: Optional[str] = None
    internal_correct_option_id_after: Optional[str] = None
    internal_correct_option_value: Optional[str] = None
    task_completed_after: Optional[bool] = None
    revealed_correct_option_id: Optional[str] = None
    effective_topic: Optional[str] = None
    session_lesson_id_after: Optional[str] = None
    session_lesson_title_after: Optional[str] = None
    lesson_preserved_signal: Optional[str] = None
    level_appropriate_signal: Optional[str] = None
    direction_correct_signal: Optional[str] = None
    intro_actual: Optional[str] = None
    intro_expected: Optional[str] = None
    intro_truthful: Optional[bool] = None
    session_level_before: Optional[int] = None
    session_level_after: Optional[int] = None
    session_unchanged_after_rejection: Optional[bool] = None
    failure_class: Optional[str] = None
    failure_is_infrastructure: Optional[bool] = None
    llm_failure_stage: Optional[str] = None
    llm_failure_category: Optional[str] = None
    llm_failure_diagnostics: dict = field(default_factory=dict)
    student_facing_response: Optional[str] = None
    diagnostics: list = field(default_factory=list)
    content_flags: list = field(default_factory=list)
    strict_validation_errors: list = field(default_factory=list)
    prerequisite: Optional[str] = None
    stop_triggered: Optional[str] = None


@dataclass
class CanaryReport:
    campaign: str
    started_at: str
    finished_at: Optional[str] = None
    model: Optional[str] = None
    reasoning_effort: Optional[str] = None
    timeout_seconds: Optional[float] = None
    sdk_call_ceiling: int = 0
    total_sdk_calls: int = 0
    total_task_turns_attempted: int = 0
    published_count: int = 0
    rejected_count: int = 0
    skipped_unmet_prerequisite: int = 0
    utf8_streams_ok: bool = True
    stopped_early: bool = False
    stop_reason: Optional[str] = None
    turns: list = field(default_factory=list)


@dataclass(frozen=True)
class Scenario:
    name: str
    lesson_id: str
    grade: int
    path: str                 # "contract" | "non_contract"
    request_type: str         # "" | "harder" | "easier"
    session_id: str
    student_message: str
    # §2 — declarative prerequisite. The scenario runs ONLY when the session
    # is already COMMITTED at this level. Otherwise it is recorded as
    # unmet_prerequisite and consumes ZERO SDK calls. The scenario NAME never
    # overrides the actual stored state.
    requires_committed_level: Optional[int] = None
    # Non-generation interactions use the same real entrypoint, but have the
    # legacy pipeline's one-call budget instead of Tutor + Reviewer.
    interaction_kind: str = "task_generation"
    intent: str = ""


# ---------------------------------------------------------------------------
# CAMPAIGNS
# ---------------------------------------------------------------------------

DIVISIBILITY = ("6-03-004", 6)          # the reported bug's own lesson
DECIMAL_COMPARE = ("6-05-006", 6)
FRACTION_WORD_PROBLEM = ("6-04-015", 6)
RECTANGLE_AREA = ("7-05-019", 7)        # geometry / measurement
SYSTEM_WORD = ("9-05-013", 9)           # equation / system
CONTRACT_LESSON = ("6-04-009", 6)       # deterministic contract fraction lesson


def _non_contract(name, lesson, request_type, session_id, message, requires=None,
                  *, interaction_kind="task_generation", intent=""):
    return Scenario(name, lesson[0], lesson[1], "non_contract", request_type,
                    session_id, message, requires, interaction_kind, intent)


def _contract(name, lesson, request_type, session_id, message, requires=None):
    return Scenario(name, lesson[0], lesson[1], "contract", request_type,
                    session_id, message, requires)


# §4 — 10-CALL FOLLOW-UP CAMPAIGN (DEFAULT)
#
#   A. Non-contract divisibility ......... max 4 calls (2 turns x 2)
#   B. Fresh equation/system lesson ...... max 2 calls (1 turn x 2)
#   C. Contract lesson ................... max 4 calls (4 turns x 1)
#                                          ---------------------------
#                                          hard ceiling: 10 SDK calls
#
# Decimal comparison is deliberately OMITTED from this focused follow-up to
# stay inside the 10-call ceiling. It remains covered deterministically by
# tests/test_practice_difficulty_levels.py and may get a later single smoke
# test if wanted. Its one real-model attempt in the initial campaign failed
# on an API timeout, not on controller behaviour.
FOLLOWUP_CAMPAIGN = (
    # A — divisibility: fresh Level 1, then a real 1→2 harder step.
    _non_contract("followup_divisibility_fresh_level1", DIVISIBILITY, "",
                  "fu-div", "Daj mi zadatak."),
    _non_contract("followup_divisibility_harder_1_to_2", DIVISIBILITY, "harder",
                  "fu-div", "Daj mi teži zadatak."),
    # B — equation/system category, untested by real model so far.
    _non_contract("followup_system_word_problem_fresh_level1", SYSTEM_WORD, "",
                  "fu-sys", "Daj mi zadatak."),
    # C — contract lesson: full 1 → 2 → 3 → boundary chain.
    _contract("followup_contract_fresh_level1", CONTRACT_LESSON, "",
              "fu-contract", "Daj mi zadatak."),
    _contract("followup_contract_level1_to_2_capability_limited", CONTRACT_LESSON, "harder",
              "fu-contract", "Daj mi teži zadatak."),
    _contract("followup_contract_level2_to_3_measurable", CONTRACT_LESSON, "harder",
              "fu-contract", "Daj mi teži zadatak."),
    # Runs ONLY if the session is genuinely committed at Level 3 — the exact
    # case the initial campaign silently mislabelled.
    _contract("followup_contract_level3_boundary_harder", CONTRACT_LESSON, "harder",
              "fu-contract", "Daj mi teži zadatak.", requires=3),
)
FOLLOWUP_CEILING = 10

# FINAL 4-CALL DIVISIBILITY CANARY
#
# This is deliberately its own campaign rather than a shorter alias for
# followup: the two turns share one fresh session, each gets the legacy
# pipeline's Tutor + Lesson Fidelity Reviewer budget, and the wrapper refuses
# a fifth invocation at the SDK boundary.  It is the post-fix confirmation for
# answer_kind canonicalization on the exact lesson that exposed the defect.
DIVISIBILITY_FINAL_CAMPAIGN = (
    _non_contract("divisibility_final_fresh_level1", DIVISIBILITY, "",
                  "divisibility-final-session", "Daj mi zadatak."),
    _non_contract("divisibility_final_harder_level1_to_2", DIVISIBILITY, "harder",
                  "divisibility-final-session", "Daj mi teži zadatak.", requires=1),
)
DIVISIBILITY_FINAL_CEILING = 4

# PRODUCTION SMOKE FINAL -- the exact five-step production trace.  All turns
# use the real legacy Practice pipeline and one shared session.  Generation
# turns receive its two-call Tutor + Reviewer budget; the correct click, hint,
# and full-solution frontend interactions each receive their normal one call.
PRODUCTION_SMOKE_FINAL_CAMPAIGN = (
    _non_contract("production_smoke_final_fresh_level1", DIVISIBILITY, "",
                  "production-smoke-final-session", "Daj mi zadatak."),
    _non_contract("production_smoke_final_correct_choice", DIVISIBILITY, "",
                  "production-smoke-final-session", "", requires=1,
                  interaction_kind="correct_choice"),
    _non_contract("production_smoke_final_harder_level1_to_2", DIVISIBILITY, "harder",
                  "production-smoke-final-session", "Daj mi teži zadatak.", requires=1),
    _non_contract("production_smoke_final_first_hint", DIVISIBILITY, "",
                  "production-smoke-final-session", "Ne znam.", requires=2,
                  interaction_kind="hint", intent="hint_request"),
    _non_contract("production_smoke_final_full_solution", DIVISIBILITY, "",
                  "production-smoke-final-session", "Uradi ga ti.", requires=2,
                  interaction_kind="full_solution", intent="solution_request"),
)
PRODUCTION_SMOKE_FINAL_CEILING = 7

# The original 14-call campaign, kept for reference/repeatability.
INITIAL_CAMPAIGN = (
    _non_contract("fresh_divisibility_level1", DIVISIBILITY, "", "div-session", "Daj mi zadatak."),
    _non_contract("harder_divisibility_level2", DIVISIBILITY, "harder", "div-session", "Daj mi teži zadatak."),
    _non_contract("fresh_decimal_comparison_level1", DECIMAL_COMPARE, "", "dec-session", "Daj mi zadatak."),
    _non_contract("fresh_fraction_word_problem_level1", FRACTION_WORD_PROBLEM, "", "frac-session", "Daj mi zadatak."),
    _non_contract("fresh_geometry_measurement_level1", RECTANGLE_AREA, "", "geo-session", "Daj mi zadatak."),
    _contract("contract_fresh_level1", CONTRACT_LESSON, "", "contract-session", "Daj mi zadatak."),
    _contract("contract_level1_to_2_capability_limited", CONTRACT_LESSON, "harder", "contract-session", "Daj mi teži zadatak."),
    _contract("contract_level2_to_3_measurable", CONTRACT_LESSON, "harder", "contract-session", "Daj mi teži zadatak."),
    _contract("contract_level3_boundary_harder", CONTRACT_LESSON, "harder", "contract-session", "Daj mi teži zadatak.", requires=3),
)
INITIAL_CEILING = 14

CAMPAIGNS = {
    "followup": (FOLLOWUP_CAMPAIGN, FOLLOWUP_CEILING),
    "initial": (INITIAL_CAMPAIGN, INITIAL_CEILING),
    "divisibility-final": (DIVISIBILITY_FINAL_CAMPAIGN, DIVISIBILITY_FINAL_CEILING),
    "production-smoke-final": (
        PRODUCTION_SMOKE_FINAL_CAMPAIGN, PRODUCTION_SMOKE_FINAL_CEILING,
    ),
}


def _turn_payload(scenario: Scenario, *, selected_option_id: str = "",
                  last_tutor_task: str = ""):
    student_message = scenario.student_message
    if scenario.interaction_kind == "correct_choice":
        student_message = f"Izabrana opcija {selected_option_id.upper()}."
    return {
        "session_id": scenario.session_id, "grade": scenario.grade,
        "selected_topic": scenario.lesson_id, "selected_oblast": "",
        "student_message": student_message, "intent": scenario.intent,
        "difficulty_request": scenario.request_type,
        "interaction_phase": (
            "practice_help" if scenario.intent == "hint_request" else ""
        ),
        "last_tutor_task": last_tutor_task,
        "interaction_type": (
            "choice_answer" if scenario.interaction_kind == "correct_choice"
            else "student_question"
        ),
        "selected_option_id": selected_option_id,
        "client_turn_id": (
            "production-smoke-final-correct-choice"
            if scenario.interaction_kind == "correct_choice" else ""
        ),
    }


def _tutor_pipeline_intros():
    """Uvodi JEDINOG motora (`matbot/tutor/pipeline.py`).

    Ranije su postojale DVIJE serverske tabele uvoda — legacy u
    `matbot/practice.py` i ova. Legacy motor je povučen (2026-08-14), pa je
    ostala tačno jedna i kapija više ne mora da ih spaja.

    NAPOMENA UZ IMENA: ranija verzija je tražila `_AT_MAXIMUM_INTRO` /
    `_AT_MINIMUM_INTRO` / `_SAME_LEVEL_INTRO`, kojih u aktivnom modulu NEMA —
    prave konstante su `INTRO_AT_HARDEST_LEVEL` i `INTRO_AT_EASIEST_LEVEL`.
    Zato su granični uvodi kapiji ranije izgledali kao „nikakav uvod“."""
    from matbot.tutor import pipeline as tutor_pipeline

    values = set(tutor_pipeline._NEW_TASK_INTRO.values())
    values.add(tutor_pipeline.INTRO_AT_EASIEST_LEVEL)
    values.add(tutor_pipeline.INTRO_AT_HARDEST_LEVEL)
    return frozenset(values)


_INTRO_PREFIXES = tuple(sorted(_tutor_pipeline_intros(), key=len, reverse=True))


# Zahtjev iz UI-ja → namjera koju aktivni birač uvoda razumije.
_INTENT_FOR_REQUEST = {"harder": "harder_task", "easier": "easier_task",
                       "": "generate_task"}


def _expected_intro(request_type, transition):
    """Očekivan uvod = ONO ŠTO BI AKTIVNI SERVER IZABRAO, ne kopija pravila."""
    from matbot.tutor import pipeline as tutor_pipeline

    intent = _INTENT_FOR_REQUEST.get((request_type or "").strip().lower(),
                                     "generate_task")
    if transition is None:
        return tutor_pipeline._intro_for(intent, None, None)
    return tutor_pipeline._intro_for(intent, transition.previous_level,
                                     transition.target_level)


def _actual_intro(answer_text: Optional[str]) -> Optional[str]:
    if not answer_text:
        return None
    for prefix in _INTRO_PREFIXES:
        if answer_text.startswith(prefix):
            return prefix
    return None


def _published_task_text(answer_text: Optional[str]) -> Optional[str]:
    if not answer_text:
        return None
    marker = "\n\nZadatak: "
    index = answer_text.find(marker)
    return answer_text[index + len(marker):] if index >= 0 else None


def _record_lesson_identity_diagnostics(result: TurnResult, llm) -> None:
    """Persist only bounded identity facts, never task prose or prompts."""
    context = tutor_lesson_context.build(result.grade, result.lesson_id)
    if context is None:
        return
    result.canonical_context_lesson_id = context.topic_id
    result.canonical_context_lesson_title = context.title
    tutor_task = getattr(getattr(llm, "last_tutor_output", None), "new_task", None)
    reviewer_output = getattr(llm, "last_reviewer_output", None)
    reviewer_task = getattr(getattr(reviewer_output, "final", None), "new_task", None)
    # Faza 4H (kompaktno odobrenje): vidi _record_answer_metadata — na `approve`
    # bez eha recenzentov konačan paket je upravo odobreni nacrt.
    if (reviewer_task is None
            and getattr(reviewer_output, "decision", None) == "approve"):
        reviewer_task = tutor_task

    canonicalized = False
    if tutor_task is not None:
        result.tutor_returned_lesson_id = getattr(tutor_task, "selected_lesson_id", None)
        result.tutor_title_matched_canonical = (
            getattr(tutor_task, "selected_lesson_title", None) == context.title
        )
        canonicalized |= bool(result.tutor_returned_lesson_id == context.topic_id
                              and result.tutor_title_matched_canonical is False)
    if reviewer_task is not None:
        result.reviewer_final_lesson_id = getattr(reviewer_task, "selected_lesson_id", None)
        result.reviewer_final_title_matched_canonical = (
            getattr(reviewer_task, "selected_lesson_title", None) == context.title
        )
        canonicalized |= bool(result.reviewer_final_lesson_id == context.topic_id
                              and result.reviewer_final_title_matched_canonical is False)
    result.title_canonicalized = canonicalized


def _record_difficulty_evidence_diagnostics(result: TurnResult, task, evidence=None,
                                            source: Optional[str] = None) -> None:
    """Persist only closed DifficultyEvidence fields and shared validator codes.

    Faza F5G: dijagnostika mora mjeriti ISTIM lekcijski-relativnim profilom
    kojim server stvarno validira — inače artefakt lažno prijavljuje kodove
    globalne rubrike za objavljen valjan paket."""
    if task is None or not hasattr(task, "difficulty_evidence"):
        return
    from matbot import difficulty_profiles

    try:
        profile = difficulty_profiles.resolve_for_context(
            tutor_lesson_context.build(result.grade, result.lesson_id))
    except Exception:
        profile = None
    evidence = evidence or task.difficulty_evidence
    result.final_difficulty_evidence = evidence.model_dump()
    result.final_difficulty_evidence_source = source
    result.final_difficulty_target_level = task.target_difficulty_level
    result.final_difficulty_validator_errors = list(difficulty_evidence_errors(
        evidence, task.target_difficulty_level, profile=profile,
    ))


def _record_cross_evidence_diagnostics(result: TurnResult, llm) -> object:
    """Record only closed evidence models and their bounded field differences."""
    tutor_task = getattr(getattr(llm, "last_tutor_output", None), "new_task", None)
    reviewer_output = getattr(llm, "last_reviewer_output", None)
    tutor_evidence = getattr(tutor_task, "difficulty_evidence", None)
    reviewer_evidence = getattr(reviewer_output, "reviewed_difficulty_evidence", None)
    if tutor_evidence is not None:
        result.tutor_difficulty_evidence = tutor_evidence.model_dump()
    if reviewer_evidence is not None:
        result.reviewer_difficulty_evidence = reviewer_evidence.model_dump()
        result.final_difficulty_evidence_source = "reviewer"
    if tutor_evidence is not None and reviewer_evidence is not None:
        tutor_data = tutor_evidence.model_dump()
        reviewer_data = reviewer_evidence.model_dump()
        differing = sorted(
            name for name in tutor_data if tutor_data[name] != reviewer_data[name]
        )
        result.difficulty_evidence_differing_fields = differing
        result.difficulty_evidence_matched = not differing
        result.difficulty_evidence_corrected = bool(differing)
    return reviewer_evidence


def _record_answer_metadata(result: TurnResult, response, after_session, llm) -> None:
    """Record the real pipeline's answer metadata without changing it.

    `current_options` and `correct_option_id` are the server-owned post-shuffle
    values.  The response's `next_state.task.options` is what the student can
    actually see, so recording both proves that every visible option came
    through the public response while the correct value remains internally
    identifiable for this isolated audit.
    """
    tutor_task = getattr(getattr(llm, "last_tutor_output", None), "new_task", None)
    reviewer_output = getattr(llm, "last_reviewer_output", None)
    corrected_task = getattr(reviewer_output, "corrected_task", None)
    reviewer_final_task = getattr(getattr(reviewer_output, "final", None), "new_task", None)
    # Faza 4H (kompaktno odobrenje): na `approve` recenzent NE vraća eho
    # paketa — server objavljuje UPRAVO odobreni nacrt. Recenzentov konačan
    # paket za dijagnostiku je tada taj nacrt; stariji eho-oblik (final uz
    # approve) ostaje podržan nepromijenjeno.
    if (reviewer_final_task is None and corrected_task is None
            and getattr(reviewer_output, "decision", None) == "approve"):
        reviewer_final_task = tutor_task
    final_task = corrected_task or reviewer_final_task or tutor_task
    reviewer_evidence = _record_cross_evidence_diagnostics(result, llm)
    if final_task is not None and reviewer_evidence is not None:
        final_task = final_task.model_copy(update={
            "difficulty_evidence": reviewer_evidence,
        })
    _record_lesson_identity_diagnostics(result, llm)

    if tutor_task is not None:
        result.tutor_declared_answer_kind = getattr(tutor_task, "answer_kind", None)
        result.tutor_proposed_target_level = getattr(tutor_task, "target_difficulty_level", None)
    if corrected_task is not None:
        result.reviewer_corrected_task_answer_kind = getattr(corrected_task, "answer_kind", None)
        result.final_task_answer_kind_source = "reviewer_corrected_task"
        result.final_structured_package_source = "reviewer_corrected_task"
    elif reviewer_final_task is not None:
        result.final_task_answer_kind_source = "reviewer_final_task"
        result.final_structured_package_source = "reviewer_final_task"
    elif final_task is not None:
        result.final_task_answer_kind_source = "tutor_task"
        result.final_structured_package_source = "tutor_task"

    if reviewer_final_task is not None:
        result.reviewer_final_target_level = getattr(reviewer_final_task, "target_difficulty_level", None)
    if final_task is not None and hasattr(final_task, "difficulty_evidence"):
        _record_difficulty_evidence_diagnostics(
            result, final_task, source=("reviewer" if reviewer_evidence is not None else None),
        )
        result.final_task_signature = final_task.task_signature.model_dump()
        result.final_task_signature_canonical = final_task.task_signature.canonical_json()
        context = tutor_lesson_context.build(result.grade, result.lesson_id)
        try:
            tutor_pipeline.validate_task_package(final_task, context, result.target_level)
        except Exception as exc:  # records the same production gate without publishing anything
            result.structured_package_validation_passed = False
            result.structured_package_validation_errors = [str(exc)]
        else:
            result.structured_package_validation_passed = True

    next_state = response.get("next_state") or {}
    task_state = next_state.get("task") or {}
    raw_options = task_state.get("options") or []
    result.next_state_options = raw_options if isinstance(raw_options, list) else []
    session_options = (after_session or {}).get("current_options") or []
    result.next_state_options_match_session = (
        isinstance(raw_options, list) and raw_options == session_options
    )

    correct_id = (after_session or {}).get("correct_option_id") or ""
    visible_by_id = {
        option.get("id"): option.get("text")
        for option in result.next_state_options
        if isinstance(option, dict)
    }
    result.visible_correct_option_value = visible_by_id.get(correct_id)
    if final_task is not None and hasattr(final_task, "task_signature"):
        committed = (after_session or {}).get("current_task_signature") or {}
        result.committed_task_signature_matches_final = (
            committed.get("structured_signature") == result.final_task_signature_canonical
            and committed.get("structured_signature_hash") == final_task.task_signature.digest()
        )

    if final_task is not None and 0 <= final_task.correct_option_index < len(final_task.options):
        result.model_marked_option_value = final_task.options[final_task.correct_option_index].text

    declared = getattr(final_task, "answer_kind", None) if final_task is not None else None
    result.final_declared_answer_kind = declared
    canonical, _normalized = canonical_answer_kind(
        declared, result.visible_correct_option_value or ""
    )
    result.final_canonical_answer_kind = canonical or None
    result.final_detected_answer_kind = detected_answer_kind(
        result.visible_correct_option_value or ""
    )
    if result.final_detected_answer_kind is None:
        # Prose/value labels such as "djeljiv" are intentionally not guessed.
        # Production keeps the schema-validated declaration unchanged here.
        if result.final_canonical_answer_kind != declared:
            result.answer_kind_diagnostics.append("canonical_metadata_mismatch")
        else:
            result.answer_kind_diagnostics.append(
                "canonicalization_not_required_not_derivable"
            )
    elif result.final_canonical_answer_kind != result.final_detected_answer_kind:
        result.answer_kind_diagnostics.append("canonical_metadata_mismatch")
    elif declared != result.final_detected_answer_kind:
        result.answer_kind_diagnostics.append(
            "canonicalized_from_declared_metadata"
        )

    # A raw Tutor/Reviewer declaration may legitimately differ (for example,
    # option_label versus integer) only when the visible answer supplies an
    # objective replacement. Non-derivable values retain their valid declared
    # kind. In both cases, the marked and visible correct values must agree.
    result.answer_metadata_consistent = bool(
        result.visible_correct_option_value
        and result.final_canonical_answer_kind
        and result.final_declared_answer_kind in VALID_ANSWER_KINDS
        and result.final_canonical_answer_kind in VALID_ANSWER_KINDS
        and (result.final_detected_answer_kind is None
             or result.final_canonical_answer_kind == result.final_detected_answer_kind)
        and result.model_marked_option_value == result.visible_correct_option_value
    )


def _record_rejected_generation_diagnostics(result: TurnResult, llm) -> None:
    """Capture safe scratchpad-only task diagnostics after a rejected draft.

    These values are never returned by Practice to a student.  They use the
    same title-derived semantic requirement as production publication so a
    manual canary explains a rejection without guessing from its log text.
    """
    tutor_task = getattr(getattr(llm, "last_tutor_output", None), "new_task", None)
    reviewer_output = getattr(llm, "last_reviewer_output", None)
    corrected_task = getattr(reviewer_output, "corrected_task", None)
    _record_lesson_identity_diagnostics(result, llm)
    requirement = lesson_fidelity.semantic_task_requirement(result.lesson_title)

    reviewer_final_task = getattr(getattr(reviewer_output, "final", None), "new_task", None)
    final_task = corrected_task or reviewer_final_task or tutor_task
    reviewer_evidence = _record_cross_evidence_diagnostics(result, llm)
    if final_task is not None and reviewer_evidence is not None:
        final_task = final_task.model_copy(update={
            "difficulty_evidence": reviewer_evidence,
        })
    _record_difficulty_evidence_diagnostics(
        result, final_task, source=("reviewer" if reviewer_evidence is not None else None),
    )
    if tutor_task is not None:
        result.tutor_proposed_task_text = tutor_task.text
    if corrected_task is not None:
        result.reviewer_corrected_task_text = corrected_task.text
    if requirement is not None:
        result.derived_semantic_requirement = requirement.prompt_block
        published_candidate = corrected_task or tutor_task
        if published_candidate is not None:
            result.deterministic_semantic_rejection_reason = requirement.failure_for(
                published_candidate.text
            )


def _supported_divisibility_mcq_errors(result: TurnResult) -> list[str]:
    """Independently prove a supported visible divisibility MCQ is sound.

    The live runner must not mistake the server's committed marked option for
    a mathematical proof.  This is intentionally the same narrow oracle used
    by publication: only explicit divisibility conditions with bare integer
    options are evaluated; prose choices remain outside its claimed scope.
    """
    options = result.next_state_options or []
    if not options:
        return []
    texts = [option.get("text", "") for option in options if isinstance(option, dict)]
    evaluation = mcq_integrity.evaluate_divisibility_mcq(
        result.published_task_text or "", texts,
    )
    if not evaluation.applicable:
        return []
    ids = [option.get("id") for option in options if isinstance(option, dict)]
    if len(ids) != len(options) or len(set(ids)) != len(ids):
        return ["duplicate_or_missing_option_ids"]
    if len(set(texts)) != len(texts):
        return ["duplicate_visible_option_texts"]
    marked_id = result.internal_correct_option_id_after or ""
    if marked_id not in ids:
        return ["marked_option_id_missing_from_committed_options"]
    marked_index = ids.index(marked_id)
    failure, evaluation = mcq_integrity.publication_failure(
        result.published_task_text or "", texts, marked_index, result.expected_answer or "",
    )
    if failure:
        return [failure]
    if evaluation.applicable:
        if result.visible_correct_option_value != texts[evaluation.correct_index]:
            return ["marked_option_math_mismatch"]
        if result.model_marked_option_value != result.visible_correct_option_value:
            return ["explanation_answer_mismatch"]
    return []


def _validate_divisibility_final_turn(result: TurnResult) -> list[str]:
    """Return every fail-closed condition for the focused final campaign."""
    errors: list[str] = []
    is_fresh = result.scenario == "divisibility_final_fresh_level1"
    is_harder = result.scenario == "divisibility_final_harder_level1_to_2"

    if not (is_fresh or is_harder):
        return ["unexpected_divisibility_final_scenario"]
    if result.sdk_calls_this_turn > MAX_NON_CONTRACT_CALLS_PER_TURN:
        errors.append("more_than_2_sdk_calls_in_turn")
    if result.sdk_calls_this_turn != 2:
        errors.append("task_generation_turn_did_not_use_tutor_and_reviewer")
    if result.previous_level != 1:
        errors.append(f"actual_previous_level_is_{result.previous_level}_not_1")
    expected_target = 1 if is_fresh else 2
    if result.target_level != expected_target:
        errors.append(f"actual_target_level_is_{result.target_level}_not_{expected_target}")

    if not result.published:
        errors.append(result.failure_class or "turn_did_not_publish")
        if result.failure_class == "family_contract_rejection":
            errors.append("family_contract_rejection")
        if result.session_unchanged_after_rejection is not True:
            errors.append("state_mutation_after_rejection")
        return errors

    if result.lesson_id != DIVISIBILITY[0] or result.effective_topic != DIVISIBILITY[0] \
            or result.session_lesson_id_after != DIVISIBILITY[0]:
        errors.append("wrong_lesson")
    if result.session_level_after != expected_target:
        errors.append(f"committed_level_is_{result.session_level_after}_not_{expected_target}")
    if not result.published_task_text:
        errors.append("missing_published_task_text")
    if not result.expected_answer:
        errors.append("missing_expected_answer")
    if len(result.next_state_options) != 4:
        errors.append("missing_multiple_choice_options_in_next_state")
    if result.next_state_options_match_session is not True:
        errors.append("next_state_options_do_not_match_committed_options")
    if not result.visible_correct_option_value:
        errors.append("missing_visible_correct_option_value")
    if result.visible_correct_option_value not in {
            option.get("text") for option in result.next_state_options
            if isinstance(option, dict)}:
        errors.append("visible_correct_option_not_returned_in_next_state")
    if result.final_declared_answer_kind not in VALID_ANSWER_KINDS:
        errors.append("invalid_declared_answer_kind_enum")
    if result.final_canonical_answer_kind not in VALID_ANSWER_KINDS:
        errors.append("invalid_final_answer_kind_enum")
    if result.final_detected_answer_kind is None:
        # Canonicalization is deliberately impossible for descriptive values.
        # The approved production rule keeps the valid declaration unchanged.
        if result.final_canonical_answer_kind != result.final_declared_answer_kind:
            errors.append("canonical_metadata_mismatch")
        if "canonicalization_not_required_not_derivable" \
                not in result.answer_kind_diagnostics:
            errors.append("missing_non_derivable_canonicalization_diagnostic")
    else:
        if result.final_canonical_answer_kind != result.final_detected_answer_kind:
            errors.append("canonical_metadata_mismatch")
        if result.final_declared_answer_kind != result.final_detected_answer_kind \
                and "canonicalized_from_declared_metadata" \
                not in result.answer_kind_diagnostics:
            errors.append("canonicalization_evidence_missing")
    if "canonical_metadata_mismatch" in result.answer_kind_diagnostics:
        errors.append("canonical_metadata_mismatch")
    if result.answer_metadata_consistent is not True:
        errors.append("answer_metadata_inconsistency")
    if any("answer_kind=" in message and "u suprotnosti" in message
           for message in result.diagnostics):
        errors.append("answer_kind_mismatch")
    errors.extend(_supported_divisibility_mcq_errors(result))

    checks = result.reviewer_checks or {}
    for check_name in (
        "math_correct", "tests_exact_lesson", "answer_correct",
        "marked_option_correct", "options_unique", "grade_appropriate",
        "solvable_and_unambiguous", "difficulty_level_appropriate",
    ):
        if checks.get(check_name) is not True:
            errors.append(f"reviewer_{check_name}_not_true")

    if is_fresh:
        if result.effective_server_label != "easy":
            errors.append("level1_effective_server_label_is_not_easy")
        if result.level_changed is not False:
            errors.append("fresh_level1_unexpected_level_change")
        if result.intro_actual == tutor_pipeline_module.INTRO_AT_HARDEST_LEVEL:
            errors.append("advanced_task_at_level1")
    else:
        if result.level_changed is not True:
            errors.append("harder_turn_level_did_not_change")
        if result.effective_server_label != "standard":
            errors.append("level2_effective_server_label_is_not_standard")
        if result.intro_actual != "Evo težeg zadatka.":
            errors.append("harder_turn_intro_is_not_exact")
        if checks.get("difficulty_direction_correct") is not True:
            errors.append("reviewer_difficulty_direction_correct_not_true")

    return errors


def _run_divisibility_final_static_checks() -> None:
    """Pure regression checks for answer-kind evidence; no LLM or I/O.

    These reproduce the completed fresh Level-1 result that exposed the
    runner bug, plus the numeric option-label case that requires production
    canonicalization. The negative assertion prevents a future check from
    accepting an unexplained declared-to-final change.
    """
    required_checks = {
        "math_correct": True,
        "tests_exact_lesson": True,
        "answer_correct": True,
        "marked_option_correct": True,
        "options_unique": True,
        "grade_appropriate": True,
        "solvable_and_unambiguous": True,
        "difficulty_level_appropriate": True,
        "difficulty_direction_correct": True,
    }

    def completed_turn(*, scenario, declared, final, detected, diagnostics):
        target = 1 if scenario == "divisibility_final_fresh_level1" else 2
        options = (
            [
                {"id": "a", "text": "nije djeljiv"},
                {"id": "b", "text": "djeljiv"},
                {"id": "c", "text": "nepoznato"},
                {"id": "d", "text": "drugo"},
            ] if detected is None else [
                {"id": "a", "text": "126"},
                {"id": "b", "text": "138"},
                {"id": "c", "text": "141"},
                {"id": "d", "text": "145"},
            ]
        )
        return TurnResult(
            scenario=scenario,
            lesson_id=DIVISIBILITY[0], lesson_title="static check",
            path="non_contract", grade=DIVISIBILITY[1],
            request_type="" if target == 1 else "harder",
            attempted=True, previous_level=1, target_level=target,
            level_changed=(target == 2), sdk_calls_this_turn=2,
            published=True, published_task_text="Koji je tačan odgovor?",
            expected_answer="static expected answer",
            next_state_options=options,
            next_state_options_match_session=True,
            visible_correct_option_value="djeljiv" if detected is None else "138",
            model_marked_option_value="djeljiv" if detected is None else "138",
            effective_topic=DIVISIBILITY[0], session_lesson_id_after=DIVISIBILITY[0],
            session_level_after=target,
            effective_server_label="easy" if target == 1 else "standard",
            intro_actual=(None if target == 1 else tutor_pipeline_module._NEW_TASK_INTRO["harder_task"]),
            reviewer_checks=required_checks,
            tutor_declared_answer_kind=declared,
            final_declared_answer_kind=declared,
            final_canonical_answer_kind=final,
            final_detected_answer_kind=detected,
            answer_metadata_consistent=True,
            answer_kind_diagnostics=list(diagnostics),
        )

    non_derivable = completed_turn(
        scenario="divisibility_final_fresh_level1", declared="short_text",
        final="short_text", detected=None,
        diagnostics=["canonicalization_not_required_not_derivable"],
    )
    assert not _validate_divisibility_final_turn(non_derivable)

    canonical, normalized = canonical_answer_kind("option_label", "138")
    assert (canonical, normalized) == ("integer", True)
    canonicalized = completed_turn(
        scenario="divisibility_final_harder_level1_to_2", declared="option_label",
        final="integer", detected="integer",
        diagnostics=["canonicalized_from_declared_metadata"],
    )
    assert not _validate_divisibility_final_turn(canonicalized)

    missing_evidence = completed_turn(
        scenario="divisibility_final_harder_level1_to_2", declared="option_label",
        final="integer", detected="integer", diagnostics=[],
    )
    assert "canonicalization_evidence_missing" in _validate_divisibility_final_turn(
        missing_evidence
    )

    invalid_options = TurnResult(
        scenario="divisibility_final_fresh_level1", lesson_id=DIVISIBILITY[0],
        lesson_title="static check", path="non_contract", grade=DIVISIBILITY[1], request_type="",
        published_task_text="Koji od sljedećih brojeva je djeljiv sa 25?",
        expected_answer="725", next_state_options=[
            {"id": "a", "text": "725"}, {"id": "b", "text": "550"},
            {"id": "c", "text": "600"}, {"id": "d", "text": "375"},
        ], internal_correct_option_id_after="a", visible_correct_option_value="725",
        model_marked_option_value="725",
    )
    assert _supported_divisibility_mcq_errors(invalid_options) == ["multiple_correct_options"]


_SMOKE_DIVISIBILITY_RULE_RE = re.compile(
    r"\b(?:djeljiv\w*|pravil\w*\s+djeljiv\w*)\b[^?]{0,160}"
    r"\bsa\s+\$?\s*(?:2|3|4|5|6|9|10|15|25)\b",
    re.IGNORECASE,
)
_ALLOWED_RESPONSE_CONTROLS = frozenset({"\n", "\r", "\t"})


def _uses_listed_divisibility_rule(result: TurnResult) -> bool:
    """True only for a visible application of one listed lesson rule.

    The production semantic gate rules out a remainder-only task generically;
    this focused smoke campaign additionally verifies that the exact selected
    lesson names one of its listed divisors in the visible task.
    """
    text = result.published_task_text or ""
    return (
        lesson_fidelity.exact_lesson_skill_failure(result.lesson_title, text) is None
        and bool(_SMOKE_DIVISIBILITY_RULE_RE.search(text))
    )


def _divisibility_25_and_10_solution_errors(task_text: Optional[str], solution: Optional[str]) -> list[str]:
    """Check the visible proof structure for a 25-and-10 divisibility task.

    This deliberately evaluates mathematical evidence instead of requiring a
    near-literal copy of the server's internal expected-answer wording.  It is
    scoped to the exact two-rule task shape used by this smoke campaign.
    """
    task = task_text or ""
    text = solution or ""
    errors: list[str] = []
    task_numbers = re.findall(r"\d+", task)
    tested_numbers = [number for number in task_numbers if number not in {"10", "25"}]
    tested_number = tested_numbers[0] if tested_numbers else ""
    if not tested_number:
        return ["full_solution_missing_tested_number_in_task"]
    if not re.search(r"\bdjeljiv\w*\b", task, re.IGNORECASE) \
            or not re.search(r"\b25\b", task) or not re.search(r"\b10\b", task):
        return ["full_solution_task_is_not_a_two_rule_25_and_10_divisibility_task"]

    if not re.search(rf"(?<!\d){re.escape(tested_number)}(?!\d)", text):
        errors.append("full_solution_does_not_state_tested_number")

    has_25_check = bool(re.search(
        r"(?:provjer\w*|pravil\w*|djeljiv\w*)[^\n.]{0,100}\b25\b",
        text, re.IGNORECASE,
    ))
    has_25_evidence = bool(re.search(
        r"(?:zadnj\w*\s+dvije\s+cifr\w*|posljednj\w*\s+dvije\s+cifr\w*)"
        r"[^\n.]{0,80}\b50\b|\b50\s*=\s*25(?:\s*\\cdot|\s*\*)?\s*\d+",
        text, re.IGNORECASE,
    ))
    if not has_25_check:
        errors.append("full_solution_omits_divisibility_check_for_25")
    if not has_25_evidence:
        errors.append("full_solution_missing_valid_25_rule_evidence")

    has_10_check = bool(re.search(
        r"(?:provjer\w*|pravil\w*|djeljiv\w*)[^\n.]{0,100}\b10\b",
        text, re.IGNORECASE,
    ))
    has_10_evidence = bool(re.search(
        r"(?:zadnj\w*\s+cifr\w*|posljednj\w*\s+cifr\w*)[^\n.]{0,80}\b0\b",
        text, re.IGNORECASE,
    ))
    if not has_10_check:
        errors.append("full_solution_omits_divisibility_check_for_10")
    if not has_10_evidence:
        errors.append("full_solution_missing_valid_10_rule_evidence")

    conclusion_start = max(text.lower().rfind("zaklju"), text.lower().rfind("dakle"))
    conclusion = text[conclusion_start:] if conclusion_start >= 0 else text[-260:]
    positive_both = bool(re.search(
        r"\bda\b[^\n.]{0,120}\bdjeljiv\w*\b[^\n.]{0,120}\b25\b[^\n.]{0,120}\b10\b"
        r"|\bdjeljiv\w*\b[^\n.]{0,120}\b25\b[^\n.]{0,120}\b10\b",
        conclusion, re.IGNORECASE,
    ))
    negative_conclusion = bool(re.search(r"\b(?:ne|nije)\s+djeljiv\w*\b", conclusion, re.IGNORECASE))
    if not positive_both or negative_conclusion:
        errors.append("full_solution_missing_affirmative_both_divisors_conclusion")
    return errors


def _has_disallowed_control_character(text: Optional[str]) -> bool:
    return any(ord(char) < 32 and char not in _ALLOWED_RESPONSE_CONTROLS
               for char in (text or ""))


def _validate_production_smoke_final_turn(result: TurnResult) -> list[str]:
    """Fail-closed validator for the exact seven-call production smoke trace."""
    errors: list[str] = []
    kind = {
        "production_smoke_final_fresh_level1": "fresh",
        "production_smoke_final_correct_choice": "correct_choice",
        "production_smoke_final_harder_level1_to_2": "harder",
        "production_smoke_final_first_hint": "hint",
        "production_smoke_final_full_solution": "full_solution",
    }.get(result.scenario)
    if kind is None:
        return ["unexpected_production_smoke_final_scenario"]

    expected_calls = 2 if kind in {"fresh", "harder"} else 1
    if result.sdk_calls_this_turn != expected_calls:
        errors.append(f"expected_exactly_{expected_calls}_sdk_calls")

    if not result.published:
        errors.append(result.failure_class or "turn_did_not_publish")
        if result.failure_class == "family_contract_rejection":
            errors.append("family_contract_rejection")
        if result.session_unchanged_after_rejection is not True:
            errors.append("state_mutation_after_rejection")
        return errors

    if result.lesson_id != DIVISIBILITY[0] or result.effective_topic != DIVISIBILITY[0] \
            or result.session_lesson_id_after != DIVISIBILITY[0]:
        errors.append("wrong_lesson")

    # Transition fields describe a generation request; session fields describe
    # the committed state around every turn.  In particular, a successful
    # harder generation is 1 -> 2, not a fictional 2 -> 2 transition merely
    # because the later hint/full-solution turns run at committed Level 2.
    if kind == "fresh":
        if result.session_level_before != 1:
            errors.append(f"fresh_session_before_is_{result.session_level_before}_not_1")
        if result.previous_level != 1:
            errors.append(f"fresh_transition_previous_is_{result.previous_level}_not_1")
        if result.target_level != 1:
            errors.append(f"fresh_transition_target_is_{result.target_level}_not_1")
        if result.session_level_after != 1:
            errors.append(f"fresh_session_after_is_{result.session_level_after}_not_1")
    elif kind == "correct_choice":
        if result.session_level_before != 1:
            errors.append(f"correct_choice_session_before_is_{result.session_level_before}_not_1")
        if result.session_level_after != 1:
            errors.append(f"correct_choice_session_after_is_{result.session_level_after}_not_1")
    elif kind == "harder":
        if result.session_level_before != 1:
            errors.append(f"harder_session_before_is_{result.session_level_before}_not_1")
        if result.previous_level != 1:
            errors.append(f"harder_transition_previous_is_{result.previous_level}_not_1")
        if result.target_level != 2:
            errors.append(f"harder_transition_target_is_{result.target_level}_not_2")
        if result.session_level_after != 2:
            errors.append(f"harder_session_after_is_{result.session_level_after}_not_2")
    else:  # hint and full_solution: current committed state only, no transition
        if result.session_level_before != 2:
            errors.append(f"{kind}_session_before_is_{result.session_level_before}_not_2")
        if result.session_level_after != 2:
            errors.append(f"{kind}_session_after_is_{result.session_level_after}_not_2")

    if kind in {"fresh", "harder"}:
        if not result.published_task_text:
            errors.append("missing_published_task_text")
        if not result.expected_answer:
            errors.append("missing_expected_answer")
        if not _uses_listed_divisibility_rule(result):
            errors.append("remainder_only_or_not_listed_divisibility_rule_task")
        if len(result.next_state_options) != 4:
            errors.append("missing_multiple_choice_options_in_next_state")
        option_ids = [option.get("id") for option in result.next_state_options
                      if isinstance(option, dict)]
        option_values = [option.get("text") for option in result.next_state_options
                         if isinstance(option, dict)]
        if len(option_ids) != 4 or len(set(option_ids)) != 4 \
                or any(not value for value in option_values) or len(set(option_values)) != 4:
            errors.append("ambiguous_option_structure")
        if result.next_state_options_match_session is not True:
            errors.append("next_state_options_do_not_match_committed_options")
        if not result.visible_correct_option_value or \
                result.visible_correct_option_value not in option_values:
            errors.append("invalid_visible_correct_option_mapping")
        if result.model_marked_option_value != result.visible_correct_option_value:
            errors.append("wrong_marked_option_or_answer")
        if result.final_declared_answer_kind not in VALID_ANSWER_KINDS \
                or result.final_canonical_answer_kind not in VALID_ANSWER_KINDS:
            errors.append("invalid_answer_kind_enum")
        if result.final_detected_answer_kind is None:
            if result.final_canonical_answer_kind != result.final_declared_answer_kind:
                errors.append("canonical_metadata_mismatch")
            if "canonicalization_not_required_not_derivable" \
                    not in result.answer_kind_diagnostics:
                errors.append("missing_non_derivable_canonicalization_diagnostic")
        else:
            if result.final_canonical_answer_kind != result.final_detected_answer_kind:
                errors.append("canonical_metadata_mismatch")
            if result.final_declared_answer_kind != result.final_detected_answer_kind \
                    and "canonicalized_from_declared_metadata" \
                    not in result.answer_kind_diagnostics:
                errors.append("canonicalization_evidence_missing")
        if "canonical_metadata_mismatch" in result.answer_kind_diagnostics:
            errors.append("canonical_metadata_mismatch")
        if result.answer_metadata_consistent is not True:
            errors.append("answer_metadata_inconsistency")
        if any("answer_kind=" in message and "u suprotnosti" in message
               for message in result.diagnostics):
            errors.append("answer_kind_mismatch")
        errors.extend(_supported_divisibility_mcq_errors(result))

        checks = result.reviewer_checks or {}
        for check_name in (
            "math_correct", "tests_exact_lesson", "answer_correct",
            "marked_option_correct", "options_unique", "grade_appropriate",
            "solvable_and_unambiguous", "difficulty_level_appropriate",
        ):
            if checks.get(check_name) is not True:
                errors.append(f"reviewer_{check_name}_not_true")
        if kind == "fresh":
            if result.level_changed is not False:
                errors.append("fresh_level1_unexpected_level_change")
            if result.effective_server_label != "easy":
                errors.append("level1_effective_server_label_is_not_easy")
            if result.intro_actual == tutor_pipeline_module.INTRO_AT_HARDEST_LEVEL:
                errors.append("advanced_task_at_level1")
        else:
            if result.level_changed is not True:
                errors.append("harder_turn_level_did_not_change")
            if result.effective_server_label != "standard":
                errors.append("level2_effective_server_label_is_not_standard")
            if result.intro_actual != "Evo težeg zadatka.":
                errors.append("harder_turn_intro_is_not_exact")
            if checks.get("difficulty_direction_correct") is not True:
                errors.append("missing_or_false_difficulty_direction_confirmation")
        return errors

    if kind == "correct_choice":
        if result.answer_verdict != "correct":
            errors.append("correct_student_answer_not_recognized")
        if not result.internal_correct_option_id_before \
                or result.internal_correct_option_id_before != result.internal_correct_option_id_after:
            errors.append("internal_correct_option_mapping_changed_or_missing")
        if result.task_completed_after is not True:
            errors.append("correct_choice_did_not_complete_task")
        return errors

    if kind == "hint":
        text = (result.answer_text or "").strip()
        if not text or text == practice.SAFE_ERROR_MESSAGE:
            errors.append("mathematically_useless_hint")
        if not re.search(r"(?i)\b(djeljiv|pravilo|zbir\s+cifara|paran|posljednj)", text):
            errors.append("hint_does_not_make_divisibility_progress")
        if feedback.ensure_hint_makes_progress(result.published_task_text or "", text) != text:
            errors.append("hint_only_checks_proper_factor_of_composite_divisor")
        if feedback.leaks_answer(
                text, result.internal_correct_option_value or "", result.expected_answer or ""):
            errors.append("hint_reveals_complete_final_answer")
        return errors

    # full_solution
    text = (result.answer_text or "").strip()
    if not text or text == practice.SAFE_ERROR_MESSAGE:
        errors.append("generic_or_missing_full_solution")
    if _has_disallowed_control_character(text):
        errors.append("full_solution_contains_control_character")
    if text.endswith("\\"):
        errors.append("full_solution_has_dangling_terminal_backslash")
    if mathsafe.find_unsafe_math_issues(text):
        errors.append("full_solution_has_malformed_latex")
    if find_numeric_inconsistencies(text):
        errors.append("full_solution_has_numeric_inconsistency")
    if text.endswith(("=", "+", "-", "*", "/", "(")):
        errors.append("full_solution_has_truncated_expression")
    errors.extend(_divisibility_25_and_10_solution_errors(
        result.published_task_text, text,
    ))
    if result.revealed_correct_option_id != result.internal_correct_option_id_after:
        errors.append("full_solution_did_not_reveal_committed_correct_option")
    if result.task_completed_after is not True:
        errors.append("full_solution_did_not_complete_task")
    return errors


def _run_production_smoke_final_static_checks() -> None:
    """Pure campaign-shape checks; no session, model, API, or environment I/O."""
    assert PRODUCTION_SMOKE_FINAL_CEILING == 7
    assert [scenario.name for scenario in PRODUCTION_SMOKE_FINAL_CAMPAIGN] == [
        "production_smoke_final_fresh_level1",
        "production_smoke_final_correct_choice",
        "production_smoke_final_harder_level1_to_2",
        "production_smoke_final_first_hint",
        "production_smoke_final_full_solution",
    ]
    assert [scenario.interaction_kind for scenario in PRODUCTION_SMOKE_FINAL_CAMPAIGN] == [
        "task_generation", "correct_choice", "task_generation", "hint", "full_solution",
    ]
    assert [scenario.intent for scenario in PRODUCTION_SMOKE_FINAL_CAMPAIGN] == [
        "", "", "", "hint_request", "solution_request",
    ]
    assert [scenario.session_id for scenario in PRODUCTION_SMOKE_FINAL_CAMPAIGN] == [
        "production-smoke-final-session",
    ] * 5
    choice_payload = _turn_payload(
        PRODUCTION_SMOKE_FINAL_CAMPAIGN[1], selected_option_id="b"
    )
    assert choice_payload["interaction_type"] == "choice_answer"
    assert choice_payload["student_message"] == "Izabrana opcija B."
    assert choice_payload["selected_option_id"] == "b"
    hint_payload = _turn_payload(PRODUCTION_SMOKE_FINAL_CAMPAIGN[3])
    assert hint_payload["student_message"] == "Ne znam."
    assert hint_payload["intent"] == "hint_request"
    assert hint_payload["interaction_phase"] == "practice_help"

    # Completed successful prefix: fresh 1 -> 1, a correct click while still
    # committed at 1, then the real harder transition 1 -> 2.  This guards
    # against accidentally comparing the harder turn's previous level with
    # the later committed level used by hint/full-solution turns.
    reviewer_checks = {
        "math_correct": True,
        "tests_exact_lesson": True,
        "answer_correct": True,
        "marked_option_correct": True,
        "options_unique": True,
        "grade_appropriate": True,
        "solvable_and_unambiguous": True,
        "difficulty_level_appropriate": True,
        "difficulty_direction_correct": True,
    }
    options = [
        {"id": "a", "text": "Da"},
        {"id": "b", "text": "Ne"},
        {"id": "c", "text": "Samo sa 2"},
        {"id": "d", "text": "Ne može se odrediti"},
    ]

    def generation_result(scenario, *, previous, target, session_before, session_after):
        harder = scenario == "production_smoke_final_harder_level1_to_2"
        return TurnResult(
            scenario=scenario, lesson_id=DIVISIBILITY[0],
            lesson_title="Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25",
            path="non_contract", grade=DIVISIBILITY[1],
            request_type="harder" if harder else "", attempted=True,
            previous_level=previous, target_level=target,
            level_changed=harder, session_level_before=session_before,
            session_level_after=session_after, sdk_calls_this_turn=2,
            published=True,
            published_task_text=(
                "Je li 12350 djeljiv i sa 10 i sa 25?" if harder
                else "Je li 340 djeljiv sa 10?"
            ),
            expected_answer="Da", next_state_options=list(options),
            next_state_options_match_session=True,
            visible_correct_option_value="Da", model_marked_option_value="Da",
            effective_topic=DIVISIBILITY[0], session_lesson_id_after=DIVISIBILITY[0],
            effective_server_label="standard" if harder else "easy",
            intro_actual=tutor_pipeline_module._NEW_TASK_INTRO["harder_task"] if harder else tutor_pipeline_module._NEW_TASK_INTRO["generate_task"],
            reviewer_checks=dict(reviewer_checks), tutor_declared_answer_kind="short_text",
            final_declared_answer_kind="short_text",
            final_canonical_answer_kind="short_text", final_detected_answer_kind=None,
            answer_metadata_consistent=True,
            answer_kind_diagnostics=["canonicalization_not_required_not_derivable"],
        )

    fresh = generation_result(
        "production_smoke_final_fresh_level1",
        previous=1, target=1, session_before=1, session_after=1,
    )
    correct_choice = TurnResult(
        scenario="production_smoke_final_correct_choice", lesson_id=DIVISIBILITY[0],
        lesson_title=fresh.lesson_title, path="non_contract", grade=DIVISIBILITY[1],
        request_type="", attempted=True, session_level_before=1, session_level_after=1,
        sdk_calls_this_turn=1, published=True, effective_topic=DIVISIBILITY[0],
        session_lesson_id_after=DIVISIBILITY[0], answer_verdict="correct",
        internal_correct_option_id_before="a", internal_correct_option_id_after="a",
        task_completed_after=True,
    )
    harder = generation_result(
        "production_smoke_final_harder_level1_to_2",
        previous=1, target=2, session_before=1, session_after=2,
    )
    assert not _validate_production_smoke_final_turn(fresh)
    assert not _validate_production_smoke_final_turn(correct_choice)
    assert not _validate_production_smoke_final_turn(harder)

    wrong_harder_transition = generation_result(
        "production_smoke_final_harder_level1_to_2",
        previous=2, target=2, session_before=2, session_after=2,
    )
    wrong_harder_commit = generation_result(
        "production_smoke_final_harder_level1_to_2",
        previous=1, target=2, session_before=1, session_after=1,
    )
    assert "harder_transition_previous_is_2_not_1" in \
        _validate_production_smoke_final_turn(wrong_harder_transition)
    assert "harder_transition_target_is_2_not_2" not in \
        _validate_production_smoke_final_turn(wrong_harder_transition)
    assert "harder_session_after_is_1_not_2" in \
        _validate_production_smoke_final_turn(wrong_harder_commit)

    two_rule_task = (
        "Provjeri da li je broj 12650 djeljiv sa 25 i sa 10 koristeći pravila djeljivosti."
    )
    captured_solution = (
        "Uradio sam zadatak.\n"
        "Provjera za $25$: zadnje dvije cifre su $50$. $50=25\\cdot2$, zato je broj djeljiv sa $25$.\n"
        "Provjera za $10$: zadnja cifra je $0$, zato je broj djeljiv sa $10$.\n"
        "Zaključak: Da — $12650$ je djeljiv i sa $25$ i sa $10$."
    )

    def full_solution_result(answer):
        return TurnResult(
            scenario="production_smoke_final_full_solution", lesson_id=DIVISIBILITY[0],
            lesson_title=fresh.lesson_title, path="non_contract", grade=DIVISIBILITY[1],
            request_type="", attempted=True, session_level_before=2, session_level_after=2,
            sdk_calls_this_turn=1, published=True, answer_text=answer,
            published_task_text=two_rule_task, expected_answer="Djeljiv je i sa 25 i sa 10.",
            effective_topic=DIVISIBILITY[0], session_lesson_id_after=DIVISIBILITY[0],
            internal_correct_option_id_after="c",
            internal_correct_option_value="Djeljiv je i sa 25 i sa 10.",
            revealed_correct_option_id="c", task_completed_after=True,
        )

    assert not _validate_production_smoke_final_turn(full_solution_result(captured_solution))
    assert "full_solution_omits_divisibility_check_for_10" in \
        _validate_production_smoke_final_turn(full_solution_result(
            "12650 je djeljiv sa 25: zadnje dvije cifre su 50. "
            "Zaključak: Da, djeljiv je sa 25."
        ))
    assert "full_solution_missing_valid_25_rule_evidence" in \
        _validate_production_smoke_final_turn(full_solution_result(
            "12650 je djeljiv sa 25 i sa 10. "
            "Zaključak: Da, djeljiv je i sa 25 i sa 10."
        ))
    assert "full_solution_missing_affirmative_both_divisors_conclusion" in \
        _validate_production_smoke_final_turn(full_solution_result(
            "Provjera za 25: zadnje dvije cifre su 50. 50=25\\cdot2, zato je broj djeljiv sa 25. "
            "Provjera za 10: zadnja cifra je 0, zato je broj djeljiv sa 10. "
            "Zaključak: Ne, 12650 nije djeljiv i sa 25 i sa 10."
        ))
    assert "full_solution_has_dangling_terminal_backslash" in \
        _validate_production_smoke_final_turn(full_solution_result(captured_solution + "\\"))
    assert "CountingLLM" not in _offline_recheck_results.__code__.co_names
    assert "OpenAIPracticeLLM" not in _offline_recheck_results.__code__.co_names

    # The wrapper's eighth attempted invocation is rejected before delegation
    # to an SDK adapter.  Calling its counter directly is deliberately inert.
    counter = CountingLLM(object(), PRODUCTION_SMOKE_FINAL_CEILING)
    for _ in range(PRODUCTION_SMOKE_FINAL_CEILING):
        counter._count("static")
    try:
        counter._count("static-eighth")
    except SDKCallBudgetExceeded:
        pass
    else:
        raise AssertionError("eighth SDK call was not refused before delegation")


def _run_one_turn(store, llm, capture, report, scenario: Scenario, campaign: str):
    lesson = lesson_info(scenario.grade, scenario.lesson_id)
    result = TurnResult(
        scenario=scenario.name, lesson_id=scenario.lesson_id,
        lesson_title=lesson["title"] if lesson else "(unknown lesson)",
        path=scenario.path, grade=scenario.grade, request_type=scenario.request_type,
    )

    # --- §2: ACTUAL stored state drives everything ------------------------
    before_session = store.peek(scenario.session_id)
    committed_level = (before_session or {}).get("difficulty_level", 1)
    result.session_level_before = committed_level
    selected_option_id = ""
    if scenario.interaction_kind == "correct_choice":
        # Deliberately take the selected ID from server-owned session state.
        # It is never read from the browser-safe response and is only used to
        # reproduce a real correct click in this isolated canary.
        selected_option_id = (before_session or {}).get("correct_option_id") or ""
        result.internal_correct_option_id_before = selected_option_id

    transition = difficulty_level.transition(committed_level, scenario.request_type)
    result.previous_level = transition.previous_level
    result.target_level = transition.target_level
    result.level_changed = transition.level_changed
    result.boundary_reason = transition.boundary_reason

    if scenario.requires_committed_level is not None \
            and committed_level != scenario.requires_committed_level:
        result.failure_class = "unmet_prerequisite"
        result.failure_is_infrastructure = False
        result.prerequisite = (
            f"requires committed level {scenario.requires_committed_level}, "
            f"session is committed at {committed_level} — skipped, 0 SDK calls spent"
        )
        result.sdk_calls_before_turn = llm.call_count
        result.sdk_calls_after_turn = llm.call_count
        report.turns.append(result)
        report.skipped_unmet_prerequisite += 1
        return result, False

    if scenario.interaction_kind == "correct_choice" and not selected_option_id:
        result.failure_class = "unmet_prerequisite"
        result.failure_is_infrastructure = False
        result.prerequisite = "active server-owned correct_option_id is missing, 0 SDK calls spent"
        result.sdk_calls_before_turn = llm.call_count
        result.sdk_calls_after_turn = llm.call_count
        result.stop_triggered = "missing_internal_correct_option_id"
        report.turns.append(result)
        report.skipped_unmet_prerequisite += 1
        return result, True

    # `generation_changed` je nekad predviđalo da li DETERMINISTIČKI K1/K3
    # generator kapabilnošću uopšte može prikazati pomjeren nivo. Taj generator
    # je povučen (2026-08-14): lekcije s ugovorom idu istom modelskom rutom kao
    # sve ostale, gdje RECENZENT garantuje da objavljen zadatak odgovara nivou.
    # Zato predviđanja više nema — ostaje serverska tranzicija kao jedini izvor.
    generation_changed = True
    result.generation_changed = generation_changed

    result.intro_expected = _expected_intro(scenario.request_type, transition)

    if scenario.interaction_kind in {"correct_choice", "hint", "full_solution"}:
        turn_cap = 1
    else:
        turn_cap = (MAX_CONTRACT_CALLS_PER_TURN if scenario.path == "contract"
                    else MAX_NON_CONTRACT_CALLS_PER_TURN)
    remaining = llm.ceiling - llm.call_count
    if remaining < turn_cap:
        result.failure_class = "unmet_prerequisite"
        result.failure_is_infrastructure = False
        result.prerequisite = (
            f"remaining SDK budget ({remaining}) is below this turn's worst-case "
            f"cap ({turn_cap}) — stopped before starting, 0 SDK calls spent"
        )
        result.sdk_calls_before_turn = llm.call_count
        result.sdk_calls_after_turn = llm.call_count
        report.turns.append(result)
        report.skipped_unmet_prerequisite += 1
        return result, True

    # --- Real turn --------------------------------------------------------
    calls_before = llm.call_count
    result.sdk_calls_before_turn = calls_before
    result.attempted = True
    llm.last_tutor_output = None
    llm.last_reviewer_output = None
    llm.last_failure = None
    capture.reset()

    try:
        response = practice.run_practice_turn(
            store, llm, _turn_payload(
                scenario,
                selected_option_id=selected_option_id,
                last_tutor_task=(before_session or {}).get("current_task") or "",
            )
        )
    except SDKCallBudgetExceeded as exc:
        result.failure_class = "reporting_error"
        result.failure_is_infrastructure = True
        result.prerequisite = str(exc)
        result.stop_triggered = "sdk_call_ceiling_reached"
        result.sdk_calls_after_turn = llm.call_count
        result.sdk_calls_this_turn = llm.call_count - calls_before
        result.sdk_call_stages = list(getattr(llm, "call_log", [])[calls_before:])
        report.turns.append(result)
        return result, True
    except LLMError as exc:  # defensive; run_practice_turn already catches these
        result.failure_class = "api_error"
        result.failure_is_infrastructure = True
        result.prerequisite = f"LLMError escaped run_practice_turn: {type(exc).__name__}"
        result.sdk_calls_after_turn = llm.call_count
        result.sdk_calls_this_turn = llm.call_count - calls_before
        result.sdk_call_stages = list(getattr(llm, "call_log", [])[calls_before:])
        report.turns.append(result)
        report.total_task_turns_attempted += 1
        report.rejected_count += 1
        return result, True

    result.sdk_calls_after_turn = llm.call_count
    result.sdk_calls_this_turn = llm.call_count - calls_before
    result.sdk_call_stages = list(getattr(llm, "call_log", [])[calls_before:])
    result.diagnostics = capture.safe_diagnostics()

    if result.sdk_calls_this_turn > turn_cap:
        result.stop_triggered = f"more_than_{turn_cap}_calls_on_{scenario.interaction_kind}_turn"

    after_session = store.peek(scenario.session_id)
    result.session_level_after = (after_session or {}).get("difficulty_level", committed_level)
    result.session_lesson_id_after = (after_session or {}).get("lesson_id")
    result.session_lesson_title_after = (after_session or {}).get("lesson_title")
    result.published = response.get("status") == "ready"
    result.answer_text = response.get("answer")
    result.student_facing_response = response.get("answer")
    result.effective_topic = response.get("effective_topic")
    result.answer_verdict = response.get("answer_verdict")
    result.revealed_correct_option_id = response.get("revealed_correct_option_id")
    result.internal_correct_option_id_after = (after_session or {}).get("correct_option_id") or ""
    result.task_completed_after = (after_session or {}).get("task_completed")
    options_after = (after_session or {}).get("current_options") or []
    result.internal_correct_option_value = next(
        (option.get("text") for option in options_after
         if isinstance(option, dict)
         and option.get("id") == result.internal_correct_option_id_after),
        None,
    )

    if result.published:
        if scenario.interaction_kind == "task_generation":
            result.intro_actual = _actual_intro(result.answer_text)
            result.intro_truthful = (result.intro_actual == result.intro_expected)
            result.published_task_text = _published_task_text(result.answer_text)
        else:
            result.published_task_text = (after_session or {}).get("current_task")
        # Only meaningful for a PUBLISHED turn: on a rejection these session
        # fields still hold the PREVIOUS task's values (a reporting defect in
        # the first campaign — a stale label looked like a real mismatch).
        if after_session:
            result.effective_server_label = after_session.get("difficulty")
            result.expected_answer = after_session.get("expected_answer_summary")
        if scenario.interaction_kind == "task_generation":
            result.content_flags = _content_quality_flags(
                result.published_task_text or "", result.expected_answer)
            _record_answer_metadata(result, response, after_session, llm)
    else:
        failure = getattr(llm, "last_failure", None) or {}
        result.llm_failure_stage = failure.get("stage")
        result.llm_failure_category = failure.get("category")
        result.llm_failure_diagnostics = failure.get("diagnostics") or {}
        result.failure_class = _classify_llm_failure(result.llm_failure_category) \
            if result.llm_failure_category else _classify_failure(capture.messages)
        if result.failure_class == "publication_validation_rejection":
            result.publication_validation_category = result.failure_class
        result.failure_is_infrastructure = result.failure_class in _INFRASTRUCTURE_CLASSES
        result.effective_server_label = None   # explicitly unchanged
        result.expected_answer = None
        result.session_unchanged_after_rejection = (after_session == before_session)
        if scenario.interaction_kind == "task_generation":
            _record_rejected_generation_diagnostics(result, llm)
        if not result.session_unchanged_after_rejection:
            result.stop_triggered = "state_mutated_after_rejection"

    tutor_out = llm.last_tutor_output
    if tutor_out is not None and getattr(tutor_out, "new_task", None) is not None:
        result.tutor_declared_label = tutor_out.new_task.difficulty

    reviewer_out = llm.last_reviewer_output
    if reviewer_out is not None:
        checks = reviewer_out.checks
        result.reviewer_decision = reviewer_out.decision
        result.reviewer_checks = {
            "math_correct": getattr(checks, "math_correct", None),
            "tests_exact_lesson": getattr(checks, "tests_exact_lesson",
                                            getattr(checks, "inside_lesson", None)),
            "answer_correct": getattr(checks, "answer_correct",
                                        getattr(checks, "marked_option_correct", None)),
            "marked_option_correct": getattr(checks, "marked_option_correct", None),
            "options_unique": getattr(checks, "options_unique", None),
            "grade_appropriate": getattr(checks, "grade_appropriate", None),
            "solvable_and_unambiguous": getattr(checks, "solvable_and_unambiguous",
                                                  getattr(checks, "task_solvable_and_unambiguous", None)),
            "difficulty_level_appropriate": getattr(checks, "difficulty_level_appropriate",
                                                      getattr(checks, "difficulty_evidence_valid", None)),
            "difficulty_direction_correct": getattr(checks, "difficulty_direction_correct", None),
            "task_package_consistent": getattr(checks, "task_package_consistent", None),
            "difficulty_evidence_valid": getattr(checks, "difficulty_evidence_valid", None),
            "task_signature_consistent": getattr(checks, "task_signature_consistent", None),
            # ŽIVI GATE 0883e8c: turn je pao na „odobreno uprkos oborenim
            # provjerama: ['language_age_appropriate']“, a artefakt tu provjeru
            # uopšte nije prikazivao — mapa je nosila samo legacy imena, pa je
            # izgledalo kao da je kod izmišljen. Ova polja su čisto dijagnostička.
            "intent_handled": getattr(checks, "intent_handled", None),
            "mathjax_valid": getattr(checks, "mathjax_valid", None),
            "language_age_appropriate": getattr(checks, "language_age_appropriate", None),
            "response_addresses_student": getattr(checks, "response_addresses_student", None),
            "independently_solved": getattr(checks, "independently_solved", None),
        }
        # Univerzalni put nosi ispravku u `final.new_task`; legacy `corrected_task`
        # tamo je uvijek None, pa je `reviewer_corrected_task_text` na živom gateu
        # bio null iako je recenzent VRATIO kompletan paket.
        if result.reviewer_corrected_task_text is None and reviewer_out.decision == "correct":
            final_task = getattr(getattr(reviewer_out, "final", None), "new_task", None)
            if final_task is not None:
                result.reviewer_corrected_task_text = final_task.text
        result.lesson_preserved_signal = (
            f"reviewer_self_reported={result.reviewer_checks['tests_exact_lesson']} (not independently proven)")
        result.level_appropriate_signal = (
            f"reviewer_self_reported={result.reviewer_checks['difficulty_level_appropriate']} (not independently proven)")
        result.direction_correct_signal = (
            f"reviewer_self_reported={result.reviewer_checks['difficulty_direction_correct']}"
            if transition.level_changed and scenario.request_type in ("harder", "easier")
            else "not_required_for_this_transition")
    elif scenario.path == "contract":
        result.lesson_preserved_signal = "guaranteed_by_construction (server-generated skeleton)"
        result.level_appropriate_signal = (
            "guaranteed_by_construction (verify_matches_target gates publication)"
            if result.published else "n/a (not published)")
        result.direction_correct_signal = "n/a (deterministic contract path, no Reviewer)"

    if campaign == "divisibility-final":
        result.strict_validation_errors = _validate_divisibility_final_turn(result)
        if result.strict_validation_errors:
            # A failed final-canary invariant is a stop condition. There is no
            # replacement turn or retry: the two configured attempts are the
            # complete experiment.
            result.stop_triggered = result.strict_validation_errors[0]
    elif campaign == "production-smoke-final":
        result.strict_validation_errors = _validate_production_smoke_final_turn(result)
        if result.strict_validation_errors:
            result.stop_triggered = result.strict_validation_errors[0]

    report.turns.append(result)
    report.total_task_turns_attempted += 1
    if result.published:
        report.published_count += 1
    else:
        report.rejected_count += 1
    return result, bool(result.stop_triggered)


def _verdict(report: CanaryReport) -> tuple[str, dict]:
    if report.campaign == "divisibility-final":
        timeout_turns = [t for t in report.turns if t.failure_class == "api_timeout"]
        non_timeout_failures = []
        for t in report.turns:
            if t.failure_class != "api_timeout":
                non_timeout_failures.extend(
                    f"{t.scenario}: {error}" for error in t.strict_validation_errors
                )
        if len(report.turns) != 2 and not timeout_turns:
            non_timeout_failures.append("did_not_complete_exactly_two_turns")
        if report.total_sdk_calls > DIVISIBILITY_FINAL_CEILING:
            non_timeout_failures.append("sdk_call_ceiling_exceeded")

        buckets = {
            "lesson_failures": [],
            "difficulty_failures": [],
            "state_or_call_budget_failures": non_timeout_failures,
            "infrastructure_failures": [f"{t.scenario}: api_timeout" for t in timeout_turns],
            "controller_rejections": [],
        }
        # A timeout is the one explicitly non-controller outcome. The missing
        # second turn is expected because it must stop immediately.
        if timeout_turns and not non_timeout_failures:
            return "DIVISIBILITY FINAL CANARY PARTIAL — INFRASTRUCTURE FAILURE", buckets
        if non_timeout_failures or timeout_turns:
            return "DIVISIBILITY FINAL CANARY FAILED — KEEP FEATURE OFF", buckets
        if report.total_sdk_calls != DIVISIBILITY_FINAL_CEILING:
            buckets["state_or_call_budget_failures"].append(
                "successful campaign did not use exactly four calls"
            )
            return "DIVISIBILITY FINAL CANARY FAILED — KEEP FEATURE OFF", buckets
        return "DIVISIBILITY FINAL CANARY PASS — FIX VERIFIED", buckets

    if report.campaign == "production-smoke-final":
        timeout_turns = [turn for turn in report.turns if turn.failure_class == "api_timeout"]
        failures = []
        for turn in report.turns:
            if turn.failure_class != "api_timeout":
                failures.extend(f"{turn.scenario}: {error}"
                                for error in turn.strict_validation_errors)
                if turn.stop_triggered and not turn.strict_validation_errors:
                    failures.append(f"{turn.scenario}: {turn.stop_triggered}")
        if len(report.turns) != len(PRODUCTION_SMOKE_FINAL_CAMPAIGN) and not timeout_turns:
            failures.append("did_not_complete_exact_five_turn_sequence")
        if report.total_sdk_calls > PRODUCTION_SMOKE_FINAL_CEILING:
            failures.append("sdk_call_ceiling_exceeded")

        buckets = {
            "lesson_failures": [],
            "difficulty_failures": [],
            "state_or_call_budget_failures": failures,
            "infrastructure_failures": [f"{turn.scenario}: api_timeout"
                                        for turn in timeout_turns],
            "controller_rejections": [],
        }
        if timeout_turns and not failures:
            return "PRODUCTION SMOKE FINAL PARTIAL — INFRASTRUCTURE FAILURE", buckets
        if failures or timeout_turns:
            return "PRODUCTION SMOKE FINAL FAILED — KEEP FEATURE OFF", buckets
        if report.total_sdk_calls != PRODUCTION_SMOKE_FINAL_CEILING:
            buckets["state_or_call_budget_failures"].append(
                "successful campaign did not use exactly seven calls"
            )
            return "PRODUCTION SMOKE FINAL FAILED — KEEP FEATURE OFF", buckets
        return "PRODUCTION SMOKE FINAL PASS — ACTIVATION VERIFIED", buckets

    """Verdict + the failure buckets it was derived from. Infrastructure
    failures (timeout / transport / schema) are reported but do NOT condemn
    the difficulty controller — they say nothing about its behaviour."""
    lesson_failures, difficulty_failures, state_budget_failures = [], [], []
    infrastructure_failures, controller_rejections = [], []

    for t in report.turns:
        if t.stop_triggered:
            state_budget_failures.append(f"{t.scenario}: {t.stop_triggered}")
        if t.published:
            if t.intro_truthful is False:
                difficulty_failures.append(
                    f"{t.scenario}: intro mismatch (expected {t.intro_expected!r}, "
                    f"got {t.intro_actual!r})")
            checks = t.reviewer_checks or {}
            if checks.get("tests_exact_lesson") is False:
                lesson_failures.append(f"{t.scenario}: tests_exact_lesson=False")
            if checks.get("difficulty_level_appropriate") is False:
                difficulty_failures.append(
                    f"{t.scenario}: published despite difficulty_level_appropriate=False")
            if t.session_level_after != t.target_level:
                difficulty_failures.append(
                    f"{t.scenario}: committed level {t.session_level_after} != "
                    f"target {t.target_level}")
        elif t.failure_class:
            if t.failure_is_infrastructure:
                infrastructure_failures.append(f"{t.scenario}: {t.failure_class}")
            elif t.failure_class != "unmet_prerequisite":
                controller_rejections.append(f"{t.scenario}: {t.failure_class}")

    buckets = {
        "lesson_failures": lesson_failures,
        "difficulty_failures": difficulty_failures,
        "state_or_call_budget_failures": state_budget_failures,
        "infrastructure_failures": infrastructure_failures,
        "controller_rejections": controller_rejections,
    }

    if lesson_failures or state_budget_failures:
        return "DIFFICULTY CANARY FAILED — KEEP FEATURE OFF", buckets
    if difficulty_failures:
        return "DIFFICULTY CANARY PARTIAL — FIXES REQUIRED BEFORE ACTIVATION", buckets
    if report.published_count == 0:
        return "DIFFICULTY CANARY FAILED — KEEP FEATURE OFF", buckets
    if controller_rejections or infrastructure_failures or report.skipped_unmet_prerequisite:
        return "DIFFICULTY CANARY PARTIAL — FIXES REQUIRED BEFORE ACTIVATION", buckets
    return "DIFFICULTY CANARY PASS — READY FOR CONTROLLED PRODUCTION ACTIVATION", buckets


def _turn_result_from_captured_record(record: dict) -> TurnResult:
    """Rebuild only the runner record; never creates a session, LLM, or SDK client."""
    if not isinstance(record, dict):
        raise ValueError("turn_record_is_not_an_object")
    required = ("scenario", "lesson_id", "lesson_title", "path", "grade", "request_type")
    missing = [name for name in required if name not in record]
    if missing:
        raise ValueError("turn_record_missing_" + ",".join(missing))
    fields = TurnResult.__dataclass_fields__
    return TurnResult(**{name: record[name] for name in fields if name in record})


def _adjudicate_production_smoke_results(raw: dict) -> tuple[dict, bool, list[str]]:
    """Purely re-evaluate captured runner data after a runner-only fix."""
    adjudicated = json.loads(json.dumps(raw, ensure_ascii=False))
    failures: list[str] = []
    if raw.get("campaign") != "production-smoke-final":
        failures.append("wrong_campaign")
    if raw.get("total_sdk_calls") != PRODUCTION_SMOKE_FINAL_CEILING:
        failures.append("total_sdk_calls_is_not_exactly_7")
    if raw.get("total_task_turns_attempted") != 5:
        failures.append("total_task_turns_attempted_is_not_5")
    if raw.get("published_count") != 5 or raw.get("rejected_count") != 0:
        failures.append("published_and_rejected_counts_do_not_prove_all_five_turns")
    if raw.get("skipped_unmet_prerequisite") != 0:
        failures.append("unmet_prerequisite_recorded")

    turn_records = adjudicated.get("turns")
    if not isinstance(turn_records, list) or len(turn_records) != 5:
        failures.append("missing_exact_five_raw_turn_records")
        turn_records = []

    false_positive_recorded = False
    expected_spans = ((0, 2), (2, 3), (3, 5), (5, 6), (6, 7))
    for index, record in enumerate(turn_records):
        try:
            turn = _turn_result_from_captured_record(record)
        except ValueError as error:
            failures.append(f"turn_{index}:{error}")
            continue
        errors = _validate_production_smoke_final_turn(turn)
        record["offline_recomputed_strict_validation_errors"] = errors
        record["offline_recheck_sdk_calls_made"] = 0

        if not turn.attempted or not turn.published:
            failures.append(f"{turn.scenario}:not_attempted_or_not_published")
        if turn.failure_class or turn.failure_is_infrastructure:
            failures.append(f"{turn.scenario}:captured_failure_class")
        if errors:
            failures.extend(f"{turn.scenario}:{error}" for error in errors)
        if index < len(expected_spans) and (
                turn.sdk_calls_before_turn, turn.sdk_calls_after_turn) != expected_spans[index]:
            failures.append(f"{turn.scenario}:unexpected_sdk_call_span")

        old_errors = record.get("strict_validation_errors")
        if not isinstance(old_errors, list):
            failures.append(f"{turn.scenario}:missing_raw_strict_validation_errors")
            continue
        allowed_old_error = "full_solution_does_not_state_internal_correct_answer"
        unexpected_old_errors = [error for error in old_errors if error != allowed_old_error]
        if unexpected_old_errors:
            failures.append(f"{turn.scenario}:preexisting_strict_failure")
        if allowed_old_error in old_errors:
            if turn.scenario != "production_smoke_final_full_solution" or errors:
                failures.append(f"{turn.scenario}:unresolved_full_solution_failure")
            else:
                record["offline_runner_false_positive"] = allowed_old_error
                false_positive_recorded = True

    if not false_positive_recorded:
        failures.append("prior_full_solution_false_positive_not_proven")
    allowed_stop = "full_solution_does_not_state_internal_correct_answer"
    if raw.get("stop_reason") not in (None, allowed_stop):
        failures.append("unexpected_prior_stop_reason")
    if raw.get("stopped_early") and raw.get("stop_reason") != allowed_stop:
        failures.append("unexpected_prior_early_stop")

    adjudicated["offline_recheck"] = {
        "source": "captured_results_only",
        "sdk_calls_made": 0,
        "prior_runner_false_positive": (
            "full_solution_does_not_state_internal_correct_answer"
            if false_positive_recorded else None
        ),
        "passed": not failures,
        "failures": failures,
    }
    return adjudicated, not failures, failures


def _offline_recheck_results(input_path: Path) -> int:
    """Offline-only entrypoint: JSON in, adjudicated JSON out, zero SDK calls."""
    try:
        raw = json.loads(input_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("results_root_is_not_an_object")
        adjudicated, passed, failures = _adjudicate_production_smoke_results(raw)
        RECHECKED_RESULTS_PATH.write_text(
            json.dumps(adjudicated, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as error:
        _safe_print(f"PRODUCTION SMOKE FINAL FAILED — KEEP FEATURE OFF ({type(error).__name__})")
        return 1

    if passed:
        _safe_print("PRODUCTION SMOKE FINAL PASS — ACTIVATION VERIFIED")
        return 0
    _safe_print("PRODUCTION SMOKE FINAL FAILED — KEEP FEATURE OFF")
    _safe_print(f"Offline recheck failures: {failures}")
    return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Universal Difficulty real-model canary")
    parser.add_argument("--campaign", choices=sorted(CAMPAIGNS), default="followup",
                        help="which scenario campaign to run (default: followup, 10 SDK calls)")
    parser.add_argument("--static-checks", action="store_true",
                        help="run pure final-canary regression checks; no model calls")
    parser.add_argument("--recheck-results", type=Path, metavar="PATH",
                        help="offline-only adjudication of captured production-smoke-final JSON")
    args = parser.parse_args(argv)
    if args.static_checks:
        _run_divisibility_final_static_checks()
        _run_production_smoke_final_static_checks()
        _safe_print("FINAL CANARY STATIC CHECKS PASS — ZERO SDK CALLS")
        return 0
    if args.recheck_results is not None:
        return _offline_recheck_results(args.recheck_results)
    scenarios, ceiling = CAMPAIGNS[args.campaign]

    _safe_print(f"Campaign: {args.campaign}   SDK call ceiling: {ceiling}")
    _safe_print(f"Practice model: {config.OPENAI_MODEL_TEXT} "
                f"(reasoning effort: {config.REASONING_EFFORT}, "
                f"timeout: {config.AI_TIMEOUT_S}s)")
    if not _UTF8_STREAMS_OK:
        _safe_print("NOTE: console streams could not be fully reconfigured to UTF-8; "
                    "output will be escaped where necessary (results are unaffected).")
    _safe_print("Starting isolated canary — REAL model calls will be made.\n")

    store = SessionStore()
    llm = CountingLLM(OpenAIPracticeLLM(), ceiling)

    capture = _LogCapture()
    matbot_logger = logging.getLogger("matbot")
    previous_level = matbot_logger.level
    matbot_logger.setLevel(logging.INFO)
    matbot_logger.addHandler(capture)

    report = CanaryReport(
        campaign=args.campaign,
        started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        model=config.OPENAI_MODEL_TEXT,
        reasoning_effort=config.REASONING_EFFORT,
        timeout_seconds=config.AI_TIMEOUT_S,
        sdk_call_ceiling=ceiling,
        utf8_streams_ok=_UTF8_STREAMS_OK,
    )

    try:
        for scenario in scenarios:
            result, should_stop = _run_one_turn(
                store, llm, capture, report, scenario, args.campaign
            )
            if not result.attempted:
                _safe_print(f"[{scenario.name}] SKIPPED ({result.failure_class}) "
                            f"— {result.prerequisite}")
            else:
                _safe_print(
                    f"[{scenario.name}] published={result.published} "
                    f"calls={result.sdk_calls_this_turn} total={llm.call_count}/{ceiling} "
                    f"level {result.previous_level}->{result.target_level} "
                    f"committed={result.session_level_after} "
                    + (f"generation_changed={result.generation_changed} "
                       if result.generation_changed is not None else "")
                    + (f"intro_truthful={result.intro_truthful}" if result.published
                       else f"failure={result.failure_class}")
                )
            if should_stop:
                report.stopped_early = True
                report.stop_reason = result.stop_triggered or result.prerequisite
                break
    finally:
        matbot_logger.removeHandler(capture)
        matbot_logger.setLevel(previous_level)

    report.total_sdk_calls = llm.call_count
    report.finished_at = time.strftime("%Y-%m-%dT%H:%M:%S")

    # --- §3: PERSIST FIRST, then report. A console problem can never lose
    # the results, change the outcome, or suppress the verdict. -------------
    persisted = False
    persist_error = None
    try:
        RESULTS_PATH.write_text(
            json.dumps(asdict(report), indent=2, ensure_ascii=False), encoding="utf-8")
        persisted = True
    except Exception as exc:
        persist_error = f"{type(exc).__name__}"

    verdict, buckets = _verdict(report)

    _safe_print("\n" + "=" * 72)
    _safe_print(f"Task turns attempted: {report.total_task_turns_attempted}")
    _safe_print(f"Skipped (unmet prerequisite, 0 calls): {report.skipped_unmet_prerequisite}")
    _safe_print(f"Total SDK calls: {report.total_sdk_calls} / {ceiling}")
    _safe_print(f"Published: {report.published_count}   Rejected: {report.rejected_count}")
    if report.stopped_early:
        _safe_print(f"STOPPED EARLY: {report.stop_reason}")
    _safe_print("=" * 72)

    for label, key in (
        ("Lesson-fidelity failures", "lesson_failures"),
        ("Difficulty/direction/intro failures", "difficulty_failures"),
        ("State/call-budget failures", "state_or_call_budget_failures"),
        ("Infrastructure failures (NOT controller defects)", "infrastructure_failures"),
        ("Controller rejections", "controller_rejections"),
    ):
        items = buckets[key]
        _safe_print(f"{label}: {len(items)} {items}")

    _safe_print("\nPer-turn failure classification:")
    for t in report.turns:
        if not t.published:
            _safe_print(f"  - [{t.scenario}] {t.failure_class}"
                        + (f" | {t.prerequisite}" if t.prerequisite else "")
                        + (f" | diagnostics: {t.diagnostics}" if t.diagnostics else ""))

    _safe_print("\nPublished tasks (manual math / lesson / language audit):")
    for t in report.turns:
        if t.published:
            _safe_print(f"  - [{t.scenario}] ({t.lesson_title})")
            _safe_print(f"      task: {t.published_task_text!r}")
            _safe_print(f"      expected_answer: {t.expected_answer!r}   "
                        f"server_label: {t.effective_server_label!r}   "
                        f"tutor_declared: {t.tutor_declared_label!r}")
            if t.content_flags:
                _safe_print(f"      OUTPUT-QUALITY FLAGS (non-blocking): {t.content_flags}")
    _safe_print("  NOTE: Bosnian sentence naturalness cannot be checked mechanically — "
                "read the task texts above.")

    if persisted:
        _safe_print(f"\nResults written to: {RESULTS_PATH}")
    else:
        _safe_print(f"\nWARNING: could not persist results ({persist_error}) — "
                    "the verdict below still reflects the campaign.")

    _safe_print("\nMATBOT_PRACTICE_DIFFICULTY_LEVELS was set for THIS process only; "
                "the feature is not active anywhere else once this process exits.")

    _safe_print("\n" + verdict)
    # Exit 0 whenever the campaign itself completed and results persisted —
    # a console encoding problem must never produce an unrelated exit code.
    return 0 if persisted else 1


if __name__ == "__main__":
    if "--static-checks" in sys.argv:
        # This path constructs only in-memory TurnResult values. It does not
        # need an API key or feature flag and must stay usable in CI/offline.
        sys.exit(main())
    if "--recheck-results" in sys.argv:
        # Offline adjudication reads a captured JSON file only; it never needs
        # an API key, feature flag, session store, LLM wrapper, or SDK client.
        sys.exit(main())
    try:
        _check_preconditions()
        sys.exit(main())
    except PreconditionError as exc:
        _safe_print(f"REFUSING TO RUN: {exc}")
        sys.exit(2)
