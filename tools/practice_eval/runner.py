"""Live evaluation runner za mod „Vježbaj sa mnom“.

Runner NE pravi paralelnu implementaciju MAT-BOT-a. Svaki potez ide kroz pravu
Flask rutu `/api/ai-tutor/chat`, dakle kroz cijeli postojeći guard chain
(token → IP limit → payload parse → validation → session limit → turn lock →
run_practice_turn) i kroz pravi `matbot.llm.OpenAIPracticeLLM`.

Jedina dodana stvar je posmatrački omotač oko adaptera koji broji stvarne SDK
pozive na granici poziva, hvata bezbjednu dijagnostiku neuspjeha i zapamti
strukturisani paket zadnjeg poziva. Omotač NE mijenja ponašanje: ne dodaje
retry, ne mijenja prompt i ne dira budžet poziva koji `matbot/` već drži.

Ograničenja koja runner NAMJERNO postavlja i koja moraju biti u izvještaju:
  • rate limiteri se dižu na praktično beskonačno da kampanja ne bi proizvela
    LAŽNE 429/409 padove (traženo u specifikaciji FAZE 1);
  • konkurentnost je podrazumijevano 1 i tvrdo ograničena na 4;
  • `.env` se NIKAD ne učitava — koristi se isključivo okruženje procesa;
  • tvrdi plafon SDK poziva odbija poziv PRIJE delegacije SDK-u.
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.practice_eval import checks as check_lib          # noqa: E402
from tools.practice_eval import classify as classify_lib     # noqa: E402
from tools.practice_eval import coherence as coherence_lib   # noqa: E402
from tools.practice_eval.scenario import (                    # noqa: E402
    Scenario, ScenarioError, load_scenarios, validate_scenarios,
)

MAX_CONCURRENCY = 4
DEFAULT_OUTPUT_ROOT = ROOT / "scratchpad" / "practice_eval"

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_REVIEW = "REVIEW"
STATUS_INFRA = "INFRA_ERROR"
STATUS_RATE_LIMITED = "RATE_LIMITED"
STATUS_TIMEOUT = "TIMEOUT"


def _console_print(*values, sep=" ", end="\n", file=None, flush=False):
    """Write evaluator CLI output without assuming a UTF-8 console.

    Structured artifacts remain UTF-8. Only the console surface falls back
    to escaped Unicode when the active stream (notably Windows CP1252) cannot
    encode a character. Application/model encoding is never mutated.
    """
    stream = file or sys.stdout
    text = sep.join(str(value) for value in values) + end
    encoding = getattr(stream, "encoding", None)
    if encoding:
        try:
            text = text.encode(encoding, errors="backslashreplace").decode(encoding)
        except LookupError:
            pass
    stream.write(text)
    if flush:
        stream.flush()

# Prefiksi log redova koje smijemo prepisati u izvještaj. Aplikacija ih već
# emituje ograničene i scrubovane (matbot/llm.py::_scrub, practice._clip_for_log).
_SAFE_LOG_PREFIXES = (
    "practice_turn ", "practice_choice ", "practice_plan ",
    "practice_contract_rejected ", "practice_duplicate_options ",
    "practice_system_verification ", "practice_difficulty_label_mismatch ",
    "lesson_fidelity ", "tutor_turn ", "tutor_choice ", "tutor_rejected ",
    "tutor_sdk_call ", "tutor_call ", "reviewer_call ", "tutor_corrected ",
    "tutor_draft_preflight ", "tutor_difficulty ", "validation_failed ",
    "rate_limited ", "turn_in_progress ", "auth_failed ",
)
_MAX_LOG_CHARS = 400


class CallBudgetExceeded(RuntimeError):
    """Tvrdi plafon kampanje bi bio prekoračen — poziv se odbija prije SDK-a."""


class LogCapture(logging.Handler):
    """Sakuplja `matbot.*` redove za trajanje jednog zahtjeva, po niti."""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self._local = threading.local()

    def _bucket(self):
        if not hasattr(self._local, "messages"):
            self._local.messages = []
        return self._local.messages

    def emit(self, record):
        try:
            self._bucket().append(record.getMessage())
        except Exception:
            pass

    def reset(self):
        self._local.messages = []

    def safe_lines(self):
        return tuple(
            message[:_MAX_LOG_CHARS] for message in self._bucket()
            if message.startswith(_SAFE_LOG_PREFIXES)
        )


class ObservingLLM:
    """Tanak omotač oko PRAVOG `OpenAIPracticeLLM`.

    Dodaje tačno tri stvari i nijednu promjenu ponašanja: brojanje stvarnih SDK
    poziva na granici invokacije, hard plafon kampanje, i pamćenje zadnjeg
    strukturisanog izlaza po vrsti poziva radi determinističke provjere paketa.
    """

    def __init__(self, inner, ceiling):
        self._inner = inner
        self._lock = threading.Lock()
        self.ceiling = ceiling
        self.call_count = 0
        self.budget_exceeded = False
        self._local = threading.local()

    # -- po-zahtjevno stanje (po niti) ------------------------------------
    def begin_request(self):
        self._local.calls = 0
        self._local.kinds = []
        self._local.latency_ms = 0
        self._local.usage = {}
        self._local.failure = None
        self._local.tutor_output = None
        self._local.reviewer_output = None
        self._local.single_call_output = None

    def request_record(self):
        return {
            "calls": getattr(self._local, "calls", 0),
            "kinds": tuple(getattr(self._local, "kinds", ())),
            "latency_ms": getattr(self._local, "latency_ms", 0),
            "usage": dict(getattr(self._local, "usage", {}) or {}),
            "failure": getattr(self._local, "failure", None),
            "tutor_output": getattr(self._local, "tutor_output", None),
            "reviewer_output": getattr(self._local, "reviewer_output", None),
            "single_call_output": getattr(self._local, "single_call_output", None),
        }

    def _count(self, method_name):
        with self._lock:
            if self.call_count + 1 > self.ceiling:
                self.budget_exceeded = True
                raise CallBudgetExceeded(
                    f"refusing SDK call #{self.call_count + 1} ({method_name}): "
                    f"campaign ceiling of {self.ceiling} would be exceeded"
                )
            self.call_count += 1
        self._local.calls = getattr(self._local, "calls", 0) + 1
        kinds = getattr(self._local, "kinds", None)
        if kinds is None:
            kinds = self._local.kinds = []
        kinds.append(method_name)

    def _invoke(self, stage, method_name, method, instructions, input_text,
                **kwargs):
        from matbot.llm import LLMError, safe_failure_diagnostics

        self._count(method_name)
        try:
            result = method(instructions, input_text, **kwargs)
        except LLMError as error:
            self._local.failure = {
                "stage": stage,
                "category": getattr(error, "category", "llm_error"),
                "diagnostics": safe_failure_diagnostics(error),
            }
            raise
        self._local.latency_ms = getattr(self._local, "latency_ms", 0) + \
            int(getattr(result, "latency_ms", 0) or 0)
        usage = getattr(self._local, "usage", None) or {}
        for key, value in (getattr(result, "usage", None) or {}).items():
            if isinstance(value, int):
                usage[key] = usage.get(key, 0) + value
        self._local.usage = usage
        return result

    # -- metode koje aplikacija stvarno zove ------------------------------
    def practice_turn(self, instructions, input_text):
        # SANKCIONISANA JEDNOPOZIVNA RUTA (živi C001/C002): legacy K1/K3 put
        # pravi zadatak u JEDNOM pozivu i to NIJE kvar proizvoda. Ranije se
        # ovdje izlaz nije snimao, pa `_final_task_package` nije imao šta da
        # vrati, `package_clean` je vraćao SKIP i scenario je izgledao kao
        # rupa u pokrivenosti — iako je paket postojao i bio provjerljiv.
        # Snima se STVARAN izlaz stvarnog poziva: nijedan poziv se ne izmišlja
        # i knjigovodstvo ostaje istinito (jedan poziv = jedan poziv).
        result = self._invoke("tutor", "practice_turn", self._inner.practice_turn,
                              instructions, input_text)
        self._local.single_call_output = result.output
        return result

    def lesson_fidelity_turn(self, instructions, input_text):
        return self._invoke("reviewer", "lesson_fidelity_turn",
                            self._inner.lesson_fidelity_turn, instructions, input_text)

    def tutor_turn(self, instructions, input_text):
        result = self._invoke("tutor", "tutor_turn", self._inner.tutor_turn,
                              instructions, input_text)
        self._local.tutor_output = result.output
        return result

    def reviewer_turn(self, instructions, input_text, timeout_s=None):
        # Faza 4H: pipeline prosljeđuje sužen rok ostatka turna.
        result = self._invoke("reviewer", "reviewer_turn", self._inner.reviewer_turn,
                              instructions, input_text, timeout_s=timeout_s)
        self._local.reviewer_output = result.output
        return result


class RefusingLLM:
    """Za `--dry-run`: svaki pokušaj poziva je greška, nikad tiho preskakanje."""

    def __getattr__(self, name):
        def _refuse(*_args, **_kwargs):
            raise AssertionError(f"dry-run attempted a real model call: {name}")
        return _refuse


# ---------------------------------------------------------------------------
# APLIKACIJA
# ---------------------------------------------------------------------------

def build_app(llm):
    """Prava Flask aplikacija s pravim rutama; samo se ubacuju naši objekti."""
    from matbot.ratelimit import RateLimiter
    from matbot.session_store import SessionStore
    from matbot.turnlock import TurnLockRegistry

    import app as app_module

    flask_app = app_module.app
    flask_app.config["TESTING"] = True
    flask_app.config["MATBOT_LLM"] = llm
    flask_app.config["MATBOT_SESSION_STORE"] = SessionStore()
    flask_app.config["MATBOT_TURN_LOCKS"] = TurnLockRegistry()
    # Namjerno podignuti limiti: kampanja ne smije proizvesti LAŽNE 429.
    flask_app.config["MATBOT_SESSION_LIMITER"] = RateLimiter(10 ** 7, 10 ** 8)
    flask_app.config["MATBOT_IP_LIMITER"] = RateLimiter(10 ** 7, 10 ** 8)
    return flask_app


def _git(*args):
    completed = subprocess.run(["git", *args], cwd=ROOT, text=True,
                               encoding="utf-8", errors="replace",
                               capture_output=True, check=False)
    return completed.stdout.strip() if completed.returncode == 0 else ""


def runtime_metadata():
    from matbot import config, practice

    pipeline = (os.environ.get("MATBOT_PRACTICE_PIPELINE", "") or "").strip().lower()
    return {
        "git_commit": _git("rev-parse", "HEAD"),
        "git_tree": _git("rev-parse", "HEAD^{tree}"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "model_text": config.OPENAI_MODEL_TEXT,
        "tutor_model": config.TUTOR_MODEL,
        "reviewer_model": config.REVIEWER_MODEL,
        "reasoning_effort": config.REASONING_EFFORT,
        "timeout_seconds": config.AI_TIMEOUT_S,
        "max_output_tokens_practice": config.MAX_OUTPUT_TOKENS_PRACTICE,
        "practice_pipeline": pipeline or "(unset → legacy_single_call)",
        "universal_pipeline_active": pipeline == practice.UNIVERSAL_PIPELINE_FLAG,
        "difficulty_levels_enabled": config.practice_difficulty_levels_enabled(),
    }


# ---------------------------------------------------------------------------
# IZVRŠAVANJE JEDNOG SCENARIJA
# ---------------------------------------------------------------------------

@dataclass
class TurnRecord:
    step_index: int
    kind: str
    request: dict
    http_status: int
    response: dict
    sdk_calls: int
    sdk_call_kinds: tuple = ()
    latency_ms: int = 0
    usage: dict = field(default_factory=dict)
    failure_category: str = ""
    failure_stage: str = ""
    failure_diagnostics: dict = field(default_factory=dict)
    reviewer_decision: str = ""
    tutor_draft_issues: str = ""
    reviewer_final_issues: str = ""
    # Recenzentov NEZAVISNO izračunat dokaz težine. Odvojen od
    # `reviewer_final_issues` jer server prenosi baš njega u konačan paket:
    # u Talasu A je paket izgledao čist dok je recenzentov vlastiti dokaz
    # obarao isti prag (A28, A31, A35, A36).
    reviewer_independent_evidence_errors: str = ""
    precondition_unmet: str = ""
    log_lines: tuple = ()
    check_results: list = field(default_factory=list)
    session_after_summary: dict = field(default_factory=dict)
    # RC11: STVARNA ruta izvršavanja ovog turna, izvedena iz snimljenih vrsta
    # poziva — `universal_two_call` / `single_call` / `deterministic_zero_call`.
    # Jednopozivni scenario time izričito zapisuje svoju rutu i ne može više
    # izgledati kao rupa u pokrivenosti (živi C001/C002).
    route: str = ""
    # Je li strukturni paket uopšte uhvaćen na ovom turnu (za razliku od
    # „uhvaćen i čist“). Bez ovoga se „nema šta da se provjeri“ ne razlikuje
    # od „provjereno i uredno“.
    package_captured: bool = False


@dataclass
class ScenarioRecord:
    id: str
    wave: str
    importance: str
    grade: int
    oblast: str
    topic_id: str
    reason: str
    tags: list
    status: str
    failed_checks: list
    skipped_checks: list
    rubrics: list
    root_causes: list
    sdk_calls: int
    duration_s: float
    session_id: str
    turns: list
    preconditions_unmet: list = field(default_factory=list)
    # RC11 taksonomija (tools/practice_eval/classify.py): sirovi PASS/FAIL ne
    # razlikuje pogrešan objavljen sadržaj, sigurno odbijanje objave, nevaljan
    # scenario i posljedicu ranijeg odbijanja. Ova polja to razdvajaju.
    outcome_class: str = ""
    routes: list = field(default_factory=list)
    package_evidence: list = field(default_factory=list)
    root_failures: list = field(default_factory=list)
    cascade_failures: list = field(default_factory=list)
    coherence_problems: list = field(default_factory=list)
    request_alignment: str = "must_follow"
    third_call_violations: list = field(default_factory=list)
    classification_notes: list = field(default_factory=list)


def _session_summary(session):
    """Serverski internali koji ostaju u LOKALNOM dijagnostičkom fajlu.

    `marked_option_text` i `expected_answer` se zapisuju jer je Talas A pokazao
    da se bez njih ne može offline provjeriti je li server označio ISPRAVNU
    opciju kad na tom zadatku nije bilo klika. Isto radi i postojeći live
    release gate (`internal_correct_option_value`). Ovo nikad ne ide u browser."""
    if not session:
        return {}
    signature = session.get("current_task_signature") or {}
    correct_id = session.get("correct_option_id") or ""
    marked = next((option.get("text") for option in (session.get("current_options") or [])
                   if isinstance(option, dict) and option.get("id") == correct_id), "")
    return {
        "lesson_id": session.get("lesson_id"),
        "difficulty_level": session.get("difficulty_level"),
        "difficulty": session.get("difficulty"),
        "hint_level": session.get("hint_level"),
        "correct_streak": session.get("correct_streak"),
        "task_completed": session.get("task_completed"),
        "wrong_option_ids": list(session.get("wrong_option_ids") or []),
        "current_task_chars": len(session.get("current_task") or ""),
        "task_signature_hash": signature.get("structured_signature_hash"),
        "correct_option_id": correct_id,
        "marked_option_text": marked,
        "expected_answer": session.get("expected_answer_summary") or "",
    }


def _final_task_package(record):
    """Paket koji bi se STVARNO objavio, bez obzira na rutu.

    ŽIVI C001/C002: sankcionisana JEDNOPOZIVNA ruta (legacy K1/K3) pravi
    zadatak u jednom pozivu i nema recenzenta. Ranije se ovdje gledao samo
    univerzalni par (tutor/reviewer), pa jednopozivni paket nije bio uhvaćen,
    `package_clean` je vraćao SKIP i scenario je izgledao kao rupa u
    pokrivenosti — iako ruta nije kvar i paket je bio provjerljiv."""
    reviewer = record.get("reviewer_output")
    if reviewer is not None:
        final = getattr(reviewer, "final", None)
        task = getattr(final, "new_task", None)
        if task is not None:
            return task
    tutor = record.get("tutor_output")
    task = getattr(tutor, "new_task", None)
    if task is not None:
        return task
    return getattr(record.get("single_call_output"), "new_task", None)


def _difficulty_profile_for(scenario):
    """Isti lekcijski-relativni profil koji server razrješava (Faza F5G).

    Evaluacija MORA mjeriti istim granicama kao server, inače objavljen valjan
    paket lažno pada na globalnoj rubrici (živi F5H nalaz)."""
    from matbot import difficulty_profiles
    from matbot.tutor import lesson_context as lesson_context_module

    try:
        context = lesson_context_module.build(scenario.grade, scenario.topic_id)
    except Exception:
        return None
    return difficulty_profiles.resolve_for_context(context)


def _practice_contract_for(scenario):
    """Semantički ugovor vježbe lekcije (F5K) — harness mjeri kao server."""
    from matbot.tutor import lesson_context as lesson_context_module

    try:
        context = lesson_context_module.build(scenario.grade, scenario.topic_id)
    except Exception:
        return None
    return getattr(context, "practice_contract", None)


def _independent_evidence_errors(reviewer_output, difficulty_profile=None):
    """Isti validator koji server pokreće nad recenzentovim VLASTITIM dokazom.

    Ne uvodi novi prag — poziva `matbot.tutor.schema.difficulty_evidence_errors`,
    tačno kao `validate_reviewer` (uključujući lekcijski-relativni profil).
    Postoji samo da se u izvještaju razlikuje „paket nosi loš dokaz“ od
    „recenzent je sam izmjerio da paket nije na traženom nivou pa ga ipak
    odobrio“."""
    from matbot.tutor.schema import difficulty_evidence_errors

    evidence = getattr(reviewer_output, "reviewed_difficulty_evidence", None)
    final = getattr(reviewer_output, "final", None)
    task = getattr(final, "new_task", None)
    if evidence is None or task is None:
        return ""
    try:
        return ",".join(difficulty_evidence_errors(
            evidence, task.target_difficulty_level, profile=difficulty_profile))
    except Exception:
        return ""


def _classify_transport(turn_records):
    for turn in turn_records:
        category = (turn.failure_category or "").lower()
        exception = str((turn.failure_diagnostics or {}).get("exception_class", "")).lower()
        if category == "llm_timeout":
            return STATUS_TIMEOUT
        if "ratelimit" in exception or "rate_limit" in exception:
            return STATUS_RATE_LIMITED
        if category == "llm_sdk_error":
            return STATUS_INFRA
        if turn.http_status >= 500:
            return STATUS_INFRA
    return ""


def run_scenario(flask_app, llm, capture, scenario: Scenario, token) -> ScenarioRecord:
    from matbot import auth

    started = time.monotonic()
    store = flask_app.config["MATBOT_SESSION_STORE"]
    session_id = scenario.session_id
    client = flask_app.test_client()
    headers = {auth.TOKEN_HEADER: token}

    turn_records = []
    previous_task_texts = []
    previous_task_signatures = []
    previous_task_identities = []
    previous_help_texts = []
    last_response = None
    last_client_turn_id = ""
    calls_before_scenario = llm.call_count

    for index, step in enumerate(scenario.steps):
        session_before = store.peek(session_id)

        # PREDUSLOV, ne očekivanje: follow-up korak bez aktivnog zadatka nema
        # šta da testira. Preskače se BEZ poziva i BEZ ijedne provjere — inače
        # bi jedan raniji pad proizveo lanac lažno „nezavisnih“ kvarova
        # (Talas A: A10, A31, A35).
        if step.get("requires_active_task") and not (session_before or {}).get("current_task"):
            turn_records.append(TurnRecord(
                step_index=index, kind=step["kind"], request={}, http_status=0,
                response={}, sdk_calls=0,
                precondition_unmet="no active task — step skipped, 0 SDK calls spent",
                session_after_summary=_session_summary(session_before),
            ))
            continue

        payload, client_turn_id = _build_payload(
            scenario, step, session_before, last_client_turn_id
        )
        capture.reset()
        llm.begin_request()
        try:
            response = client.post("/api/ai-tutor/chat", json=payload, headers=headers)
            http_status = response.status_code
            body = response.get_json(silent=True)
            if not isinstance(body, dict):
                body = {"answer": "", "_non_json_body": True}
        except CallBudgetExceeded as error:
            http_status = 599
            body = {"answer": "", "_budget_exceeded": str(error)}
        request_record = llm.request_record()
        session_after = store.peek(session_id)

        failure = request_record.get("failure") or {}
        turn = TurnRecord(
            step_index=index,
            kind=step["kind"],
            request=_redacted_payload(payload),
            http_status=http_status,
            response=body,
            sdk_calls=request_record["calls"],
            sdk_call_kinds=request_record["kinds"],
            latency_ms=request_record["latency_ms"],
            usage=request_record["usage"],
            failure_category=failure.get("category", "") or "",
            failure_stage=failure.get("stage", "") or "",
            failure_diagnostics=failure.get("diagnostics", {}) or {},
            log_lines=capture.safe_lines(),
            session_after_summary=_session_summary(session_after),
        )

        package = _final_task_package(request_record)
        turn.package_captured = package is not None
        turn.route = classify_lib.turn_route({
            "sdk_calls": turn.sdk_calls, "sdk_call_kinds": turn.sdk_call_kinds,
            "precondition_unmet": turn.precondition_unmet,
        })
        reviewer_output = request_record.get("reviewer_output")
        difficulty_profile = _difficulty_profile_for(scenario)
        practice_contract = _practice_contract_for(scenario)
        if reviewer_output is not None:
            turn.reviewer_decision = getattr(reviewer_output, "decision", "") or ""
            turn.reviewer_independent_evidence_errors = _independent_evidence_errors(
                reviewer_output, difficulty_profile)
        tutor_task = getattr(request_record.get("tutor_output"), "new_task", None)
        if tutor_task is not None:
            from matbot.tutor import package_preflight
            turn.tutor_draft_issues = package_preflight.describe_issues(
                package_preflight.collect_package_issues(
                    tutor_task, difficulty_profile=difficulty_profile,
                    practice_contract=practice_contract))
        if package is not None:
            from matbot.tutor import package_preflight
            turn.reviewer_final_issues = package_preflight.describe_issues(
                package_preflight.collect_package_issues(
                    package, difficulty_profile=difficulty_profile,
                    practice_contract=practice_contract))

        observation = check_lib.TurnObservation(
            scenario_id=scenario.id,
            step_index=index,
            step_kind=step["kind"],
            topic_id=step.get("topic_id") or scenario.topic_id,
            grade=scenario.grade,
            request_payload=payload,
            http_status=http_status,
            response=body,
            session_before=session_before,
            session_after=session_after,
            sdk_calls=request_record["calls"],
            sdk_call_kinds=request_record["kinds"],
            latency_ms=request_record["latency_ms"],
            usage=request_record["usage"],
            failure_category=failure.get("category"),
            failure_stage=failure.get("stage"),
            log_lines=turn.log_lines,
            reviewer_decision=turn.reviewer_decision,
            final_task_package=package,
            previous_task_texts=tuple(previous_task_texts),
            previous_task_signatures=tuple(previous_task_signatures),
            previous_task_identities=tuple(previous_task_identities),
            previous_help_texts=tuple(previous_help_texts),
            previous_response=copy.deepcopy(last_response) if step["kind"] == "repeat_choice" else None,
        )
        results = check_lib.run_checks(step["checks"], observation)
        turn.check_results = [asdict(result) for result in results]
        turn_records.append(turn)

        # Historija za sljedeće korake istog scenarija.
        if observation.issued_new_task:
            previous_task_texts.append(observation.task_after)
            signature = (session_after or {}).get("current_task_signature") or {}
            if signature.get("structured_signature_hash"):
                previous_task_signatures.append(signature["structured_signature_hash"])
            if observation.identity_after:
                previous_task_identities.append(observation.identity_after)
        if step.get("collect_help"):
            previous_help_texts.append(observation.answer)
        last_response = copy.deepcopy(body)
        last_client_turn_id = client_turn_id

        if llm.budget_exceeded:
            break

    transport = _classify_transport(turn_records)
    unmet = [{"step": turn.step_index, "reason": turn.precondition_unmet}
             for turn in turn_records if turn.precondition_unmet]
    failed, skipped = [], []
    for turn in turn_records:
        for result in turn.check_results:
            label = f"step{turn.step_index}:{result['name']}"
            if result["outcome"] == check_lib.FAIL:
                failed.append({"check": result["name"], "step": turn.step_index,
                               "detail": result["detail"], "label": label})
            elif result["outcome"] == check_lib.SKIP:
                skipped.append({"check": result["name"], "step": turn.step_index,
                                "detail": result["detail"], "label": label})

    rubrics = sorted({name for step in scenario.steps for name in step.get("rubrics", ())})
    if transport:
        status = transport
    elif failed:
        status = STATUS_FAIL
    elif skipped or rubrics or unmet:
        # Preskočen preduslov znači NEDOKAZANO, nikad „dobro“ — scenario s
        # neizvršenim korakom ne smije završiti kao strogi PASS.
        status = STATUS_REVIEW
    else:
        status = STATUS_PASS

    root_causes = sorted({check_lib.root_cause(entry["check"]) for entry in failed})
    record = ScenarioRecord(
        id=scenario.id, wave=scenario.wave, importance=scenario.importance,
        grade=scenario.grade, oblast=scenario.oblast, topic_id=scenario.topic_id,
        reason=scenario.reason, tags=list(scenario.tags), status=status,
        failed_checks=failed, skipped_checks=skipped, rubrics=rubrics,
        root_causes=root_causes,
        sdk_calls=llm.call_count - calls_before_scenario,
        duration_s=round(time.monotonic() - started, 2),
        session_id=session_id,
        turns=[asdict(turn) for turn in turn_records],
        preconditions_unmet=unmet,
    )
    # RC11: klasifikacija se računa iz VEĆ SNIMLJENOG zapisa, pa je izvještaj
    # ne mora ponovo izvoditi i ne može se raziću s njim.
    verdict = classify_lib.classify(asdict(record), scenario)
    record.outcome_class = verdict["outcome_class"]
    record.routes = verdict["routes"]
    record.package_evidence = verdict["package_evidence"]
    record.root_failures = verdict["root_failures"]
    record.cascade_failures = verdict["cascade_failures"]
    record.coherence_problems = verdict["coherence_problems"]
    record.request_alignment = verdict["request_alignment"]
    record.third_call_violations = verdict["third_call_violations"]
    record.classification_notes = verdict["notes"]
    return record


def _build_payload(scenario: Scenario, step, session_before, last_client_turn_id):
    """Payload TAČNO onakav kakav frontend šalje (templates/index.html)."""
    kind = step["kind"]
    client_turn_id = uuid.uuid4().hex
    payload = {
        "session_id": scenario.session_id,
        "client_turn_id": client_turn_id,
        "grade": scenario.grade,
        "mode": "practice",
        "selected_topic": step.get("topic_id") or scenario.topic_id,
        "selected_oblast": "",
        "student_message": step.get("message", ""),
        "conversation_history": [],
    }
    if kind in ("choice", "repeat_choice"):
        option_id = _select_option_id(step, session_before)
        payload["interaction_type"] = "choice_answer"
        payload["selected_option_id"] = option_id
        payload["student_message"] = f"Izabrana opcija {option_id.upper()}."
        if kind == "repeat_choice":
            payload["client_turn_id"] = last_client_turn_id
            client_turn_id = last_client_turn_id
        return payload, client_turn_id

    payload["interaction_type"] = "student_question"
    if step.get("intent"):
        payload["intent"] = step["intent"]
    if step.get("difficulty_request"):
        payload["difficulty_request"] = step["difficulty_request"]
    if step.get("interaction_phase"):
        payload["interaction_phase"] = step["interaction_phase"]
    if step.get("send_last_task") and session_before:
        payload["last_tutor_task"] = (session_before.get("current_task") or "")[:600]
    return payload, client_turn_id


def _select_option_id(step, session_before):
    """Klik se bira iz SERVERSKOG stanja, nikad iz browserskog odgovora.

    Tačan ID se u produkciji nikad ne šalje u browser prije otkrivanja; ovdje se
    čita iz `SessionStore.peek` isključivo da bi se reprodukovao stvaran klik."""
    session = session_before or {}
    options = [option.get("id") for option in (session.get("current_options") or [])
               if isinstance(option, dict)]
    correct = session.get("correct_option_id") or ""
    wrong_already = list(session.get("wrong_option_ids") or [])
    select = step.get("select", "correct")
    if select == "correct":
        return correct or (options[0] if options else "a")
    if select in ("wrong", "second_wrong"):
        for option_id in options:
            if option_id != correct and option_id not in wrong_already:
                return option_id
        return next((option_id for option_id in options if option_id != correct), "a")
    return select


def _redacted_payload(payload):
    """Zahtjev ide u izvještaj bez ijednog zaglavlja — token se nikad ne bilježi."""
    return {key: value for key, value in payload.items()}


# ---------------------------------------------------------------------------
# KAMPANJA
# ---------------------------------------------------------------------------

def estimate_calls(scenarios):
    """(min, max) modelskih poziva — izvedeno iz deklarisanih koraka."""
    minimum = maximum = 0
    for scenario in scenarios:
        for step in scenario.steps:
            expected = step.get("expect_calls", 0)
            maximum += expected
            minimum += 1 if expected else 0
    return minimum, maximum


def _load_completed(results_path: Path):
    if not results_path.exists():
        return set()
    completed = set()
    for line in results_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            completed.add(json.loads(line)["id"])
        except (ValueError, KeyError):
            continue
    return completed


def run_campaign(scenarios, output_dir: Path, max_model_calls: int, concurrency: int,
                 delay_ms: int, resume: bool):
    from matbot import auth

    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "results.jsonl"
    already = _load_completed(results_path) if resume else set()
    pending = [scenario for scenario in scenarios if scenario.id not in already]

    llm_inner = _real_llm()
    llm = ObservingLLM(llm_inner, max_model_calls)
    capture = LogCapture()
    flask_app = build_app(llm)
    token = auth.issue_token()

    matbot_logger = logging.getLogger("matbot")
    previous_level = matbot_logger.level
    matbot_logger.setLevel(logging.INFO)
    matbot_logger.addHandler(capture)

    meta = runtime_metadata()
    meta.update({
        "started_at": datetime.now(timezone.utc).isoformat(),
        "scenario_count_total": len(scenarios),
        "scenario_count_pending": len(pending),
        "scenario_count_skipped_resume": len(already),
        "max_model_calls": max_model_calls,
        "concurrency": concurrency,
        "rate_limiters": "raised for the campaign — no real 429/409 pressure is exercised",
    })
    (output_dir / "run_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    write_lock = threading.Lock()
    records = []

    def _one(scenario):
        record = run_scenario(flask_app, llm, capture, scenario, token)
        with write_lock:
            records.append(record)
            with results_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
            _console_print(f"[{record.status:<12}] {record.id}  {record.topic_id}  "
                           f"calls={record.sdk_calls}  {record.duration_s}s"
                           + (f"  -> {','.join(record.root_causes)}"
                              if record.root_causes else ""))
        if delay_ms:
            time.sleep(delay_ms / 1000.0)
        return record

    try:
        if concurrency <= 1:
            for scenario in pending:
                if llm.budget_exceeded:
                    _console_print("STOP: campaign SDK call ceiling reached.")
                    break
                _one(scenario)
        else:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                list(pool.map(_one, pending))
    finally:
        matbot_logger.removeHandler(capture)
        matbot_logger.setLevel(previous_level)

    meta["finished_at"] = datetime.now(timezone.utc).isoformat()
    meta["actual_sdk_calls"] = llm.call_count
    meta["budget_exceeded"] = llm.budget_exceeded
    (output_dir / "run_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta, records


def _real_llm():
    from matbot.llm import OpenAIPracticeLLM
    return OpenAIPracticeLLM()


# ---------------------------------------------------------------------------
# DRY RUN
# ---------------------------------------------------------------------------

def dry_run(scenarios, output_dir: Path):
    from matbot.topics import lesson_info

    problems = validate_scenarios(scenarios)
    # RC11: dokazano nespojiva poruka i lekcija se hvataju OVDJE — prije nego
    # što talas potroši ijedan živi poziv na nevaljano očekivanje (22 od 100
    # scenarija u discovery-100). Vidi tools/practice_eval/coherence.py.
    problems.extend(coherence_lib.validate_wave(scenarios))
    for scenario in scenarios:
        lesson = lesson_info(scenario.grade, scenario.topic_id)
        if lesson is None:
            problems.append(f"{scenario.id}: topic {scenario.topic_id} does not exist for grade {scenario.grade}")
            continue
        if lesson["oblast"] != scenario.oblast:
            problems.append(f"{scenario.id}: declared oblast does not match topics.json "
                            f"({scenario.oblast!r} vs {lesson['oblast']!r})")
        for index, step in enumerate(scenario.steps):
            step_topic = step.get("topic_id")
            if step_topic and lesson_info(scenario.grade, step_topic) is None:
                problems.append(f"{scenario.id} step{index}: topic {step_topic} does not exist "
                                f"for grade {scenario.grade}")
            for name in step["checks"]:
                if check_lib.resolve(name) is None:
                    problems.append(f"{scenario.id} step{index}: unknown check {name!r}")
            for name in step.get("rubrics", ()):
                if name not in check_lib.RUBRICS:
                    problems.append(f"{scenario.id} step{index}: unknown rubric {name!r}")

    minimum, maximum = estimate_calls(scenarios)
    lessons = {(scenario.grade, scenario.topic_id) for scenario in scenarios}
    oblasti = {(scenario.grade, scenario.oblast) for scenario in scenarios}
    total_lessons = _total_lesson_count()

    meta = runtime_metadata()
    summary = {
        "dry_run": True,
        "sdk_calls_made": 0,
        "scenarios": len(scenarios),
        "unique_ids_ok": len({scenario.id for scenario in scenarios}) == len(scenarios),
        "unique_lessons": len(lessons),
        "curriculum_lessons_total": total_lessons,
        "lesson_coverage_percent": round(100.0 * len(lessons) / total_lessons, 2),
        "unique_oblasti": len(oblasti),
        "estimated_model_calls_min": minimum,
        "estimated_model_calls_max": maximum,
        "problems": problems,
        "runtime": meta,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "dry_run.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _total_lesson_count():
    payload = json.loads((ROOT / "data" / "topics.json").read_text(encoding="utf-8"))
    return sum(len(grade.get("lessons", [])) for grade in payload.get("grades", {}).values())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        description="MAT-BOT live Practice evaluation (FAZA 1 dijagnostika)")
    parser.add_argument("--scenarios", default=str(ROOT / "tools" / "practice_eval" / "scenarios"),
                        help="scenario JSONL file or a directory of them")
    parser.add_argument("--wave", choices=["A", "B", "all"],
                        help="run only one wave; 'all' applies no wave filter")
    parser.add_argument("--scenario", action="append", default=[],
                        help="run only these scenario IDs (repeatable)")
    parser.add_argument("--list", action="store_true", help="list scenarios and exit, zero SDK calls")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate everything without a single model call")
    parser.add_argument("--max-scenarios", type=int, default=0)
    parser.add_argument("--max-model-calls", type=int, default=0,
                        help="hard campaign ceiling; a call beyond it is refused before the SDK")
    parser.add_argument("--concurrency", type=int, default=1,
                        help=f"1..{MAX_CONCURRENCY}; default 1 to avoid false 409/429")
    parser.add_argument("--delay-ms", type=int, default=0)
    parser.add_argument("--resume", action="store_true",
                        help="skip scenario IDs already present in results.jsonl")
    parser.add_argument("--output-dir", default="")
    return parser


def select(scenarios, args):
    """`--wave all` je izričito ODSUSTVO filtera, ne treći talas.

    Namjerno se ne piše kao `wave in ("A", "B")`: kad se jednom doda talas C,
    „all“ ga mora obuhvatiti bez ijedne izmjene ovdje."""
    selected = list(scenarios)
    if args.wave and args.wave != "all":
        selected = [scenario for scenario in selected if scenario.wave == args.wave]
    if args.scenario:
        wanted = set(args.scenario)
        selected = [scenario for scenario in selected if scenario.id in wanted]
    if args.max_scenarios:
        selected = selected[:args.max_scenarios]
    return selected


def main(argv=None):
    from tools.practice_eval import report as report_lib

    args = build_parser().parse_args(argv)
    try:
        scenarios = load_scenarios(Path(args.scenarios))
    except ScenarioError as error:
        _console_print(f"SCENARIO ERROR: {error}")
        return 2
    selected = select(scenarios, args)

    if args.list:
        _console_print(f"{'ID':<6} {'W':<2} {'IMP':<12} {'G':<2} {'TOPIC':<10} {'CALLS':<6} REASON")
        for scenario in selected:
            calls = sum(step.get("expect_calls", 0) for step in scenario.steps)
            _console_print(f"{scenario.id:<6} {scenario.wave:<2} {scenario.importance:<12} "
                           f"{scenario.grade:<2} {scenario.topic_id:<10} {calls:<6} "
                           f"{scenario.reason[:70]}")
        _console_print(f"\n{len(selected)} scenarios, 0 SDK calls made.")
        return 0

    output_dir = Path(args.output_dir) if args.output_dir else \
        DEFAULT_OUTPUT_ROOT / datetime.now().strftime("%Y%m%d-%H%M%S")

    if args.dry_run:
        summary = dry_run(selected, output_dir)
        _console_print(json.dumps(
            {key: value for key, value in summary.items() if key != "runtime"},
            ensure_ascii=False, indent=2))
        _console_print("\nRUNTIME: " + json.dumps(summary["runtime"], ensure_ascii=False))
        _console_print(f"\nWritten to {output_dir}")
        _console_print("DRY RUN — 0 SDK calls made.")
        return 1 if summary["problems"] else 0

    concurrency = max(1, min(args.concurrency, MAX_CONCURRENCY))
    _, maximum = estimate_calls(selected)
    ceiling = args.max_model_calls or maximum
    if not os.environ.get("OPENAI_API_KEY"):
        _console_print("REFUSING TO RUN: OPENAI_API_KEY is not present in this process environment.")
        return 2

    meta, records = run_campaign(selected, output_dir, ceiling, concurrency,
                                 args.delay_ms, args.resume)
    all_records = report_lib.load_records(output_dir / "results.jsonl")
    report_lib.write_reports(output_dir, meta, all_records, _total_lesson_count())
    _console_print(f"\nResults: {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
