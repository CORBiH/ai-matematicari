"""Determinističke provjere jednog Practice turna.

PRAVILO OVOG MODULA (isto kao za validatore u `matbot/`): provjera koja ne može
NIŠTA DOKAZATI mora vratiti `SKIP`, nikad `PASS`. „Preskočeno“ nije dokaz
ispravnosti — ono podiže scenario na REVIEW umjesto da ga tiho propusti.

Sve provjere su čiste funkcije nad jednim `TurnObservation`. Nijedna ne poziva
model i nijedna ne uvodi novu matematiku: gdje god postoji validator u
`matbot/`, poziva se TAJ validator, da bi evaluacija mjerila isto što i server.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from matbot import api, config, feedback, geometrycheck, mathsafe, option_equivalence
from matbot.mathcheck import find_numeric_inconsistencies
from matbot.mathsegments import DISPLAY, INLINE, tokenize_math
from matbot.practice import PRACTICE_UNAVAILABLE_MESSAGE, SAFE_ERROR_MESSAGE
from matbot.terminology import normalize_terminology
from matbot.tutor import package_preflight

# Serverski „canned" tekstovi. Svaki od njih stiže sa HTTP 200 i, kad se pojavi
# na turnu koji je trebao dati zadatak ili pomoć, to je TEHNIČKI FALLBACK
# PREDSTAVLJEN KAO USPJEH — najveća zamka ove evaluacije. Nikad se ne broji
# kao uspjeh (vidi `check_not_safe_error` / `check_no_fallback_text`).
_FALLBACK_MESSAGES = (
    SAFE_ERROR_MESSAGE,
    PRACTICE_UNAVAILABLE_MESSAGE,
    api.NON_PRACTICE_MESSAGE,
    api.IMAGE_NOT_SUPPORTED_MESSAGE,
    api.REQUEST_TOO_LARGE_MESSAGE,
    api.EMPTY_MESSAGE_PROMPT,
    api.TOO_LONG_MESSAGE,
    api.AUTH_FRIENDLY_MESSAGE,
    api.RATE_LIMIT_MESSAGE,
    api.TURN_IN_PROGRESS_MESSAGE,
)

PASS = "pass"
FAIL = "fail"
SKIP = "skip"


@dataclass(frozen=True)
class CheckResult:
    name: str
    outcome: str          # PASS | FAIL | SKIP
    detail: str = ""

    @property
    def failed(self):
        return self.outcome == FAIL

    @property
    def skipped(self):
        return self.outcome == SKIP


@dataclass
class TurnObservation:
    """Sve što jedan turn ostavi za sobom — browserski payload I serverski internali.

    Serverski internali se čitaju iz `SessionStore.peek`, isto kao u postojećem
    live release gateu; nikad iz odgovora koji ide u browser."""

    scenario_id: str
    step_index: int
    step_kind: str
    topic_id: str
    grade: int
    request_payload: dict
    http_status: int
    response: dict
    session_before: Optional[dict]
    session_after: Optional[dict]
    sdk_calls: int
    sdk_call_kinds: tuple = ()
    latency_ms: int = 0
    usage: dict = field(default_factory=dict)
    failure_category: Optional[str] = None      # llm_timeout / llm_sdk_error / ...
    failure_stage: Optional[str] = None
    log_lines: tuple = ()
    tutor_draft_issues: tuple = ()              # package_preflight opis nacrta
    reviewer_final_issues: tuple = ()
    reviewer_decision: Optional[str] = None
    final_task_package: object = None           # TaskPayload | None (nikad u izvještaj)
    # Historija unutar iste sesije, koju pozivalac puni prije provjera.
    previous_task_texts: tuple = ()
    previous_task_signatures: tuple = ()
    previous_task_identities: tuple = ()
    previous_help_texts: tuple = ()
    previous_response: Optional[dict] = None

    # -- izvedeno ---------------------------------------------------------
    @property
    def answer(self) -> str:
        return (self.response or {}).get("answer") or ""

    @property
    def published(self) -> bool:
        """„Objavljeno" znači STVARAN odgovor, ne HTTP 200.

        SAFE_ERROR_MESSAGE se vraća sa statusom 200 i to je po produktnom
        ugovoru ispravno — ali za evaluaciju je to pad, ne uspjeh."""
        return ((self.response or {}).get("status") == "ready"
                and self.answer.strip() not in _FALLBACK_MESSAGES)

    @property
    def student_message(self) -> str:
        return (self.request_payload or {}).get("student_message") or ""

    @property
    def task_after(self) -> str:
        return ((self.session_after or {}).get("current_task") or "")

    @property
    def task_before(self) -> str:
        return ((self.session_before or {}).get("current_task") or "")

    @property
    def options_after(self) -> list:
        return list((self.session_after or {}).get("current_options") or [])

    @property
    def correct_option_text(self) -> str:
        correct_id = (self.session_after or {}).get("correct_option_id") or ""
        for option in self.options_after:
            if isinstance(option, dict) and option.get("id") == correct_id:
                return option.get("text") or ""
        return ""

    @property
    def expected_answer(self) -> str:
        return ((self.session_after or {}).get("expected_answer_summary") or "")

    @property
    def identity_after(self) -> str:
        return ((self.session_after or {}).get("current_task_identity") or "")

    @property
    def identity_before(self) -> str:
        return ((self.session_before or {}).get("current_task_identity") or "")

    @property
    def issued_new_task(self) -> bool:
        """Nov zadatak po SERVERSKOM kanonskom identitetu (pitanje+opcije).

        ŽIVI F4G RERUN (G03): isti tekst pitanja („Koji od navedenih brojeva je
        djeljiv i sa 6 i sa 25?“) s NOVIM opcijama je legitiman nov zadatak —
        matbot/tutor/task_identity.py identitet računa iz pitanja I opcija.
        Harness je poredio samo tekst, pa je ispravan turn padao kao
        „no new task issued“. Tekstualno poređenje ostaje SAMO kao rezerva za
        starije zapise sesije bez identiteta."""
        if self.identity_after or self.identity_before:
            return bool(self.identity_after) and self.identity_after != self.identity_before
        return bool(self.task_after) and self.task_after != self.task_before

    @property
    def hint_level_before(self) -> int:
        """Broj RANIJE datih nagovještaja — isto značenje kao u sesiji servera."""
        try:
            return int((self.session_before or {}).get("hint_level") or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def serves_hint_ladder_top(self) -> bool:
        """True kad OVAJ turn služi POSLJEDNJI (treći) nagovještaj.

        Ogledalo produkcijskog uslova `is_hint_ladder_top` u
        matbot/tutor/pipeline.py: `hint_level` je broj ranije datih hintova, pa
        vrh ljestvice počinje na `MAX_HINT_LEVEL - 1`. Čita se ISTA serverska
        sesija koju gate koristi, nikad prozna procjena iz odgovora."""
        payload = self.request_payload or {}
        is_hint = (payload.get("intent") or "") == "hint_request"
        return is_hint and self.hint_level_before >= config.MAX_HINT_LEVEL - 1

    @property
    def visible_task_text(self) -> str:
        """Tekst zadatka onako kako ga učenik vidi u odgovoru (iza uvoda)."""
        marker = "\n\nZadatak: "
        index = self.answer.find(marker)
        return self.answer[index + len(marker):] if index >= 0 else ""


# ---------------------------------------------------------------------------
# POMOĆNE FUNKCIJE
# ---------------------------------------------------------------------------

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Interni tragovi koji NIKAD ne smiju stići u browser (CLAUDE.md, pravilo 7).
# Samo doslovni, jednoznačni markeri — nikad riječ koja se prirodno javlja u
# bosanskom nastavnom tekstu.
_INTERNAL_MARKERS = (
    "difficulty_evidence", "task_signature", "correct_option_index",
    "correct_option_id", "expected_answer", "selected_lesson_id",
    "selected_lesson_title", "target_difficulty_level", "lesson_focus",
    "new_task", "worked_solution", "fail_closed", "fail_reason_code",
    "reviewed_difficulty_evidence", "independently_solved", "independent_answer",
    "reasoning_steps", "condition_count", "operation_count",
    "representation_change_count", "combines_concepts",
    "llm_schema_parse_error", "llm_incomplete_max_output_tokens",
    "numeric_equality_mismatch", "semantically_duplicate_options",
    "unknown_mathjax_command", "circle_diameter_uses_D",
    "image_rectangle_value_mismatch", "tutor_rejected", "UnifiedOutputError",
    "InvalidOutputError", "Traceback (most recent call last)",
    "request_id=", "stage=publication", "stage=tutor_draft", "stage=reviewer",
    "MATBOT_", "OPENAI_",
)

# Engleske funkcijske riječi. Prag je 3 RAZLIČITE riječi da normalan bosanski
# tekst s jednim posuđenim terminom nikad ne padne.
_ENGLISH_MARKERS = (
    "the", "and", "with", "that", "this", "because", "should", "would",
    "which", "there", "please", "following", "answer", "question", "correct",
    "options", "example", "step", "value", "number",
)
_WORD_RE = re.compile(r"[A-Za-z]+")


def _text_outside_math(text: str) -> str:
    """Samo prozni dio — matematički segmenti se nikad ne mjere jezički."""
    return "".join(content for kind, content in tokenize_math(text or "")
                   if kind not in (INLINE, DISPLAY))


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _token_overlap(left: str, right: str) -> float:
    """Jaccard nad riječima; 1.0 znači isti skup riječi."""
    left_tokens = set(re.findall(r"\w+", _normalized(left)))
    right_tokens = set(re.findall(r"\w+", _normalized(right)))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _geometry_context(observation: TurnObservation):
    from matbot import geometry_rules
    session = observation.session_after or observation.session_before or {}
    return geometry_rules.route_geometry_topic(
        session.get("oblast", ""), session.get("lesson_title", "")
    )


# ---------------------------------------------------------------------------
# PROVJERE
# ---------------------------------------------------------------------------

def check_published(obs: TurnObservation) -> CheckResult:
    if obs.http_status != 200:
        return CheckResult("published", FAIL, f"http_status={obs.http_status}")
    if not obs.published:
        return CheckResult("published", FAIL,
                           f"safe_error_or_no_status failure_category={obs.failure_category or '-'}")
    return CheckResult("published", PASS)


def check_not_safe_error(obs: TurnObservation) -> CheckResult:
    """HTTP 200 + SAFE_ERROR_MESSAGE = FAIL. Zaseban kod, da se u izvještaju
    vidi po imenu koliko turnova je „uspjelo" a učeniku dalo tehničku poruku."""
    if obs.answer.strip() == SAFE_ERROR_MESSAGE:
        return CheckResult("not_safe_error", FAIL,
                           f"canned SAFE_ERROR_MESSAGE returned with http_status={obs.http_status}")
    return CheckResult("not_safe_error", PASS)


def check_no_fallback_text(obs: TurnObservation) -> CheckResult:
    """Nijedan serverski canned tekst ne smije proći kao uspješan Practice turn."""
    text = obs.answer.strip()
    for message in _FALLBACK_MESSAGES:
        if text == message:
            return CheckResult("no_fallback_text", FAIL,
                               f"server fallback text presented as a successful turn: {message[:60]!r}")
    return CheckResult("no_fallback_text", PASS)


def check_response_schema(obs: TurnObservation) -> CheckResult:
    """Ugovor odgovora iz docs/ARCHITECTURE.md, u OBA smjera.

    Uspjeh nosi status/next_state/session_mode/effective_topic; odbijanje mora
    biti BEZ `status` i BEZ `next_state` — po tome frontend zna da zadrži
    vlastito stanje. Oba oblika su ovdje provjerena."""
    body = obs.response or {}
    problems = []
    if not isinstance(body, dict):
        return CheckResult("response_schema", FAIL, "response body is not a JSON object")
    if body.get("_non_json_body") or body.get("_budget_exceeded"):
        return CheckResult("response_schema", FAIL, "no usable JSON body was returned")
    if body.get("status") == "ready":
        if not isinstance(body.get("answer"), str) or not body["answer"].strip():
            problems.append("answer missing or empty")
        if body.get("answer_verdict") not in (None, "correct", "incorrect"):
            problems.append(f"answer_verdict={body.get('answer_verdict')!r}")
        if not isinstance(body.get("last_tutor_task"), str):
            problems.append("last_tutor_task is not a string")
        state = body.get("next_state")
        if not isinstance(state, dict) or "v" not in state:
            problems.append("next_state missing or has no version")
        elif "task" in state and not isinstance(state["task"], dict):
            problems.append("next_state.task is not an object")
        if body.get("session_mode") != "practice":
            problems.append(f"session_mode={body.get('session_mode')!r}")
        if not isinstance(body.get("effective_topic"), str):
            problems.append("effective_topic is not a string")
        for internal in ("expected_answer_summary", "solution_summary",
                         "correct_option_id", "current_task_signature"):
            if internal in body:
                problems.append(f"internal field {internal!r} leaked into the payload")
        state = body.get("next_state") if isinstance(body.get("next_state"), dict) else {}
        for internal in ("expected_answer_summary", "correct_option_id", "solution_summary"):
            if internal in state:
                problems.append(f"internal field {internal!r} leaked into next_state")
    else:
        if "status" in body:
            problems.append("rejection carries a status field")
        if "next_state" in body:
            problems.append("rejection carries next_state — the frontend would clear its own state")
        if not isinstance(body.get("answer"), str):
            problems.append("rejection has no answer string")
        if not isinstance(body.get("last_tutor_task"), str):
            problems.append("rejection has no last_tutor_task string")
    if problems:
        return CheckResult("response_schema", FAIL, "; ".join(problems[:4]))
    return CheckResult("response_schema", PASS)


def check_not_published(obs: TurnObservation) -> CheckResult:
    if obs.published:
        return CheckResult("not_published", FAIL, "turn published although it must be blocked")
    return CheckResult("not_published", PASS)


def check_zero_calls(obs: TurnObservation) -> CheckResult:
    if obs.sdk_calls != 0:
        return CheckResult("zero_calls", FAIL, f"sdk_calls={obs.sdk_calls}")
    return CheckResult("zero_calls", PASS)


def check_task_published(obs: TurnObservation) -> CheckResult:
    if not obs.published:
        return CheckResult("task_published", FAIL, "turn not published")
    if not obs.task_after:
        return CheckResult("task_published", FAIL, "no active task after the turn")
    if not obs.issued_new_task:
        return CheckResult("task_published", FAIL, "active task unchanged — no new task issued")
    if not obs.visible_task_text.strip():
        return CheckResult("task_published", FAIL, "task text is not visible in the reply")
    return CheckResult("task_published", PASS)


_COMPUTE_IMPERATIVE_RE = re.compile(
    r"(?i)\b(izra[čc]unaj|izra[čc]unajte|rije[šs]i|rije[šs]ite|odredi|odredite|"
    r"sredi|skrati|pro[šs]iri|uprosti|napi[šs]i vrijednost)\b"
)
_DIGIT_RE = re.compile(r"\d")


def check_task_self_contained(obs: TurnObservation) -> CheckResult:
    """Objavljen zadatak mora sadržavati ono što traži da se izračuna.

    ZAŠTO POSTOJI (Talas A, scenario A25, lekcija 8-01-014): objavljen je
    zadatak čiji je CIJELI tekst glasio „Izračunaj vrijednost izraza:“ — bez
    ijednog izraza, uz četiri numeričke opcije i označen tačan odgovor.
    Recenzent je vratio `approve` uz `task_solvable_and_unambiguous=true`, a
    nijedan serverski validator to nije mogao vidjeti: `mathsafe` nema šta da
    sanitizuje, `mathcheck` nema jednakost, `option_equivalence` vidi četiri
    različite vrijednosti, `mcq_integrity` nije primjenjiv.

    ISPRAVKA LAŽNOG POZITIVA (živi run postIncompleteFix, scenario B43):
    „U trouglu $ABC$ centar opisane kružnice je tačka koja se dobije kao:“ je
    oboren samo zato što završava dvotačkom — a njegove četiri opcije
    („sjecište simetrala stranica“ …) gramatički i semantički DOVRŠAVAJU
    pitanje. To je legitiman option-completed MCQ obrazac.

    Zato evaluator više nema vlastito, šire pravilo o dvotački, nego poziva
    ISTI uski produkcijski helper (`matbot.tutor.schema.incomplete_task_request`)
    koji odbija samo tekst sastavljen ISKLJUČIVO od imperativa bez objekta.
    Jedno pravilo za oba sloja: evaluator i server ne mogu se razići."""
    from matbot.tutor.schema import incomplete_task_request

    text = (obs.visible_task_text or obs.task_after or "").strip()
    if not text:
        return CheckResult("task_self_contained", SKIP, "no published task text on this turn")
    missing = incomplete_task_request(text)
    if missing:
        return CheckResult("task_self_contained", FAIL,
                           f"task asks for a mathematical object it never shows: {missing!r}")
    return CheckResult("task_self_contained", PASS)


def check_no_new_task(obs: TurnObservation) -> CheckResult:
    if obs.issued_new_task:
        return CheckResult("no_new_task", FAIL, "a new task was issued on a non-generation turn")
    return CheckResult("no_new_task", PASS)


def check_task_preserved(obs: TurnObservation) -> CheckResult:
    if not obs.task_before:
        return CheckResult("task_preserved", SKIP, "no active task before this turn")
    if obs.task_after != obs.task_before:
        return CheckResult("task_preserved", FAIL, "active task was replaced or lost")
    return CheckResult("task_preserved", PASS)


def check_options_ok(obs: TurnObservation) -> CheckResult:
    options = obs.options_after
    if len(options) != 4:
        return CheckResult("options_ok", FAIL, f"option_count={len(options)}")
    ids = [option.get("id") for option in options]
    texts = [option.get("text") or "" for option in options]
    if sorted(ids) != ["a", "b", "c", "d"]:
        return CheckResult("options_ok", FAIL, f"option_ids={ids}")
    if len(set(_normalized(text) for text in texts)) != 4:
        return CheckResult("options_ok", FAIL, "two options have the same text")
    equivalent = option_equivalence.find_equivalent_option_pairs(texts)
    if equivalent:
        return CheckResult("options_ok", FAIL, f"semantically_duplicate_options={equivalent}")
    correct_id = (obs.session_after or {}).get("correct_option_id") or ""
    if correct_id not in ids:
        return CheckResult("options_ok", FAIL, "server correct_option_id is not one of the options")
    return CheckResult("options_ok", PASS)


def check_lesson_matches(obs: TurnObservation) -> CheckResult:
    effective = (obs.response or {}).get("effective_topic")
    session_lesson = (obs.session_after or {}).get("lesson_id")
    problems = []
    if effective is not None and effective != obs.topic_id:
        problems.append(f"effective_topic={effective}")
    if session_lesson is not None and session_lesson != obs.topic_id:
        problems.append(f"session_lesson_id={session_lesson}")
    if problems:
        return CheckResult("lesson_matches", FAIL, ",".join(problems))
    if effective is None and session_lesson is None:
        return CheckResult("lesson_matches", SKIP, "no lesson identity available on this turn")
    return CheckResult("lesson_matches", PASS)


def check_no_leak(obs: TurnObservation) -> CheckResult:
    text = obs.answer
    hits = [marker for marker in _INTERNAL_MARKERS if marker in text]
    if hits:
        return CheckResult("no_leak", FAIL, "internal markers: " + ",".join(hits[:5]))
    if re.search(r'\{\s*"[a-z_]+"\s*:', text):
        return CheckResult("no_leak", FAIL, "raw JSON object in the student-visible reply")
    return CheckResult("no_leak", PASS)


def check_no_control_chars(obs: TurnObservation) -> CheckResult:
    if _CONTROL_CHARS_RE.search(obs.answer):
        return CheckResult("no_control_chars", FAIL, "disallowed control character in the reply")
    if obs.answer.rstrip().endswith("\\"):
        return CheckResult("no_control_chars", FAIL, "dangling terminal backslash")
    return CheckResult("no_control_chars", PASS)


def check_math_safe(obs: TurnObservation) -> CheckResult:
    issues = mathsafe.find_unsafe_math_issues(obs.answer)
    if issues:
        return CheckResult("math_safe", FAIL, ",".join(issues[:3]))
    return CheckResult("math_safe", PASS)


def check_numeric_consistent(obs: TurnObservation) -> CheckResult:
    issues = find_numeric_inconsistencies(obs.answer)
    if issues:
        return CheckResult("numeric_consistent", FAIL, issues[0][:160])
    if not any(kind in (INLINE, DISPLAY) for kind, _ in tokenize_math(obs.answer)):
        return CheckResult("numeric_consistent", SKIP, "no math segment to verify")
    return CheckResult("numeric_consistent", PASS)


def check_geometry_ok(obs: TurnObservation) -> CheckResult:
    scope, figures = _geometry_context(obs)
    if not scope:
        return CheckResult("geometry_ok", SKIP, "non-geometry lesson — verifier stays off by design")
    issues = geometrycheck.find_geometry_issues(obs.answer, scope, list(figures or ()))
    if issues:
        return CheckResult("geometry_ok", FAIL, ",".join(issues[:3]))
    return CheckResult("geometry_ok", PASS)


def check_terminology_clean(obs: TurnObservation) -> CheckResult:
    if normalize_terminology(obs.answer) != obs.answer:
        return CheckResult("terminology_clean", FAIL, "a normalizable forbidden term reached the student")
    return CheckResult("terminology_clean", PASS)


def check_bosnian(obs: TurnObservation) -> CheckResult:
    prose = _text_outside_math(obs.answer).lower()
    words = set(_WORD_RE.findall(prose))
    hits = sorted(words & set(_ENGLISH_MARKERS))
    if len(hits) >= 3:
        return CheckResult("bosnian", FAIL, "english markers: " + ",".join(hits[:6]))
    return CheckResult("bosnian", PASS)


def check_no_verdict(obs: TurnObservation) -> CheckResult:
    verdict = (obs.response or {}).get("answer_verdict")
    if verdict is not None:
        return CheckResult("no_verdict", FAIL, f"answer_verdict={verdict} on a text turn")
    return CheckResult("no_verdict", PASS)


def check_verdict_correct(obs: TurnObservation) -> CheckResult:
    verdict = (obs.response or {}).get("answer_verdict")
    if verdict != "correct":
        return CheckResult("verdict_correct", FAIL, f"answer_verdict={verdict}")
    return CheckResult("verdict_correct", PASS)


def check_verdict_incorrect(obs: TurnObservation) -> CheckResult:
    verdict = (obs.response or {}).get("answer_verdict")
    if verdict != "incorrect":
        return CheckResult("verdict_incorrect", FAIL, f"answer_verdict={verdict}")
    return CheckResult("verdict_incorrect", PASS)


def check_task_completed(obs: TurnObservation) -> CheckResult:
    if not (obs.session_after or {}).get("task_completed"):
        return CheckResult("task_completed", FAIL, "task_completed is not set")
    return CheckResult("task_completed", PASS)


def check_task_not_completed(obs: TurnObservation) -> CheckResult:
    if (obs.session_after or {}).get("task_completed"):
        return CheckResult("task_not_completed", FAIL, "task closed too early")
    return CheckResult("task_not_completed", PASS)


def check_correct_option_stable(obs: TurnObservation) -> CheckResult:
    before = (obs.session_before or {}).get("correct_option_id")
    after = (obs.session_after or {}).get("correct_option_id")
    if before is None or after is None:
        return CheckResult("correct_option_stable", SKIP, "no committed option id on both sides")
    if before != after:
        return CheckResult("correct_option_stable", FAIL, "the correct option changed during a click")
    return CheckResult("correct_option_stable", PASS)


def check_reveal_present(obs: TurnObservation) -> CheckResult:
    revealed = (obs.response or {}).get("revealed_correct_option_id")
    expected = (obs.session_after or {}).get("correct_option_id")
    if not revealed:
        return CheckResult("reveal_present", FAIL, "the solution was requested but no option was revealed")
    if expected and revealed != expected:
        return CheckResult("reveal_present", FAIL, "a different option than the committed one was revealed")
    return CheckResult("reveal_present", PASS)


def check_reveal_absent(obs: TurnObservation) -> CheckResult:
    if (obs.response or {}).get("revealed_correct_option_id"):
        return CheckResult("reveal_absent", FAIL, "the correct option was revealed too early")
    return CheckResult("reveal_absent", PASS)


def check_help_nonempty(obs: TurnObservation) -> CheckResult:
    """Hint ili rješenje mora nositi sadržaj, ne najavu.

    Živi nalaz iz `matbot/tutor/pipeline.py::_compose_visible_help`: model je
    znao staviti najavu u `reply`, a hint u zasebno polje."""
    text = obs.answer.strip()
    if not text or text == SAFE_ERROR_MESSAGE:
        return CheckResult("help_nonempty", FAIL, "empty or canned help")
    if len(text) < 25:
        return CheckResult("help_nonempty", FAIL, f"help is {len(text)} characters long")
    return CheckResult("help_nonempty", PASS)


def _student_stated_committed_answer(obs: TurnObservation) -> bool:
    """True samo kad je UČENIK sam napisao committed odgovor.

    Dva uslova, oba obavezna:
      • produkcijsko pravilo — isti `feedback.leaks_answer` nad učenikovom
        porukom, dakle evaluator ne izmišlja drugi kriterij;
      • granica cifre — doslovni sloj u `leaks_answer` radi podnizom, pa bi
        „Mislim da je x=30.“ pokrio committed `$x=3$` i tiho oslabio provjeru za
        POGREŠAN pokušaj. Evaluator je zato namjerno uži od produkcije: mora
        postojati pojava committed vrijednosti kojoj ni lijevo ni desno ne stoji
        cifra.
    """
    message = obs.student_message
    correct, expected = obs.correct_option_text, obs.expected_answer
    task = obs.task_after or obs.task_before
    if not message or not feedback.leaks_answer(message, correct, expected, task_text=task):
        return False
    haystack = _normalized(message).replace(" ", "")
    for candidate in (correct, expected):
        needle = _normalized(candidate).replace(" ", "").strip("$").rstrip(".,!?;:")
        if not needle:
            continue
        for match in re.finditer(re.escape(needle), haystack):
            before = haystack[match.start() - 1] if match.start() else ""
            after = haystack[match.end()] if match.end() < len(haystack) else ""
            if not before.isdigit() and not after.isdigit():
                return True
    return False


def _answer_leak_result(obs: TurnObservation, name: str) -> CheckResult:
    """Curenje odgovora PRIJE izričitog `solution_request`.

    Koristi se DOSLOVNO isti `feedback.leaks_answer` koji legacy put primjenjuje
    na pogrešan klik — dakle mjeri se postojećim projektnim pravilom, ne novim.
    U univerzalnom putu taj sloj na serveru NE POSTOJI (nalaz audita B), pa je
    ovo jedino mjesto gdje se mjeri.

    KAD SE NE MOŽE DOKAZATI: ako vrijednost nije došla od tutora, ponavljanje
    nije curenje. Dva takva slučaja, oba SKIP i nikad FAIL — isto pravilo kao za
    validatore u `matbot/`, i isti par izuzetaka koji produkcijski gate
    (`tutor/pipeline.py::_reveals_committed_answer`) već primjenjuje:

      • vrijednost stoji u tekstu SAMOG zadatka (npr. „Koji je veći: $3/5$ ili
        $2/7$?“ s odgovorom $3/5$) — prepričavanje, ne otkrivanje;
      • vrijednost je napisao UČENIK. ŽIVI RUN postFinalFixes, B53: zadatak
        `$(x+2)=5$`, committed `$x=3$`, učenik „Mislim da je rješenje x=3.“ —
        tutor je smio potvrditi i provjeriti uvrštavanjem, a check je to
        prijavio kao curenje. Produkcija taj korak nije blokirala; nedostajao je
        samo evaluatorov pandan tom izuzetku.

    NIVO 3 NIJE CURENJE (PP-1 LIVE-150, D004): proizvodna ljestvica je
    trostepena i TREĆI nagovještaj po ugovoru daje cijeli postupak I konačan
    rezultat — prompt to izričito traži (`_HINT_LEVEL_GUIDANCE[2]`), produkcijski
    gate ga zato izuzima (`is_hint_ladder_top`), a deterministički put je na to
    harmonizovan. Evaluator je taj sankcionisan vrh ljestvice prijavljivao kao
    ANSWER_LEAK i time mjerio pravilo koje proizvod nema. Izuzetak je USKO
    vezan za vrh ljestvice: nivoi 1 i 2 i dalje padaju, i svako curenje van
    hint/solution puta i dalje pada."""
    if obs.serves_hint_ladder_top:
        return CheckResult(name, SKIP,
                           "treći nagovještaj po ugovoru daje konačan rezultat — "
                           "the third hint is contractually allowed to state the answer")
    correct = obs.correct_option_text
    expected = obs.expected_answer
    if not correct and not expected:
        return CheckResult(name, SKIP, "no committed answer to compare against")
    task = obs.task_after or obs.task_before
    if not feedback.leaks_answer(obs.answer, correct, expected, task_text=task):
        return CheckResult(name, PASS)
    if task and feedback.leaks_answer(task, correct, expected):
        return CheckResult(name, SKIP,
                           "the value also appears in the task text itself — a leak cannot be proven")
    if _student_stated_committed_answer(obs):
        return CheckResult(name, SKIP,
                           "učenik je sam napisao committed odgovor — the student already "
                           "stated this value, so the reply cannot be proven to disclose it")
    return CheckResult(name, FAIL, "the reply reveals the committed answer before a solution request")


def check_hint_no_leak(obs: TurnObservation) -> CheckResult:
    return _answer_leak_result(obs, "hint_no_leak")


def check_no_answer_leak(obs: TurnObservation) -> CheckResult:
    return _answer_leak_result(obs, "no_answer_leak")


def check_solution_complete(obs: TurnObservation) -> CheckResult:
    """Nakon izričitog `solution_request` puni postupak MORA doći do rezultata.

    Isti oracle kao za curenje, samo obrnutog znaka: ovdje je navođenje odgovora
    OBAVEZNO. Kad se odgovor ne može mehanički prepoznati, vraća se SKIP —
    kratak odgovor tipa „2“ se ne može razlikovati od broja iz zadatka."""
    text = obs.answer.strip()
    if not text or text in _FALLBACK_MESSAGES:
        return CheckResult("solution_complete", FAIL, "no worked solution was returned")
    if len(text) < 80:
        return CheckResult("solution_complete", FAIL,
                           f"worked solution is only {len(text)} characters long")
    correct = obs.correct_option_text
    expected = obs.expected_answer
    if not correct and not expected:
        return CheckResult("solution_complete", SKIP, "no committed answer to compare against")
    if feedback.leaks_answer(text, correct, expected):
        return CheckResult("solution_complete", PASS)
    needle = _normalized(expected or correct).replace(" ", "")
    if needle and needle.strip("$") in _normalized(text).replace(" ", ""):
        return CheckResult("solution_complete", PASS)
    return CheckResult("solution_complete", SKIP,
                       "the final result could not be mechanically located in the worked solution")


def check_free_text_grading_no_oracle(obs: TurnObservation) -> CheckResult:
    """NAMJERNO uvijek SKIP — i to je nalaz, ne propust.

    Server ocjenjuje isključivo klik na opciju. Za utipkan odgovor
    `answer_verdict` je uvijek `null` (matbot/tutor/pipeline.py::_run_text_turn),
    modelov `grading` se ne objavljuje niti commituje, pa ne postoji nijedan
    deterministički oracle kojim bi se dokazalo da je slobodan tekst ispravno
    ocijenjen. Ovo se mjeri kvalitativnom rubrikom, nikad automatski."""
    verdict = (obs.response or {}).get("answer_verdict")
    return CheckResult(
        "free_text_grading_no_oracle", SKIP,
        f"no deterministic oracle for free-text grading (answer_verdict={verdict!r}); "
        "server grades clicks only — judged by rubric",
    )


def check_hint_differs(obs: TurnObservation) -> CheckResult:
    if not obs.previous_help_texts:
        return CheckResult("hint_differs", SKIP, "first hint in this session")
    current = _normalized(obs.answer)
    for previous in obs.previous_help_texts:
        if current == _normalized(previous):
            return CheckResult("hint_differs", FAIL, "hint is byte-identical to an earlier one")
        overlap = _token_overlap(obs.answer, previous)
        if overlap >= 0.85:
            return CheckResult("hint_differs", FAIL, f"hint repeats an earlier one (overlap={overlap:.2f})")
    return CheckResult("hint_differs", PASS)


def check_task_differs(obs: TurnObservation) -> CheckResult:
    """Duplikat se dokazuje STRUKTURNIM POTPISOM, ne sličnošću teksta.

    ISPRAVKA LAŽNOG POZITIVA (živi run postIncompleteFix, scenario B52):
    „$x+\\frac{1}{2}<2$“ i „$x+\\frac{1}{2}<1$“ daju rješenja $x<3/2$ i
    $x<1/2$ — dva stvarno različita zadatka. Ranija Jaccard mjera nad SKUPOM
    riječi dala im je preklapanje 1.00, jer se razlikuju samo u jednom broju
    koji je u oba skupa već prisutan. Nijedna mjera tekstualne sličnosti ne
    može razlikovati „razlikuje se u jednom broju“ od „identično“.

    Zato se koristi isti dokaz koji i server koristi za svoju zaštitu od
    duplikata (`_is_duplicate_structured_signature`): matematički potpis
    zadatka. Doslovno ponovljen tekst i dalje pada, jer to server NIKAD ne
    smije objaviti."""
    if not obs.issued_new_task:
        return CheckResult("task_differs", FAIL, "no new task was issued")
    if not obs.previous_task_texts:
        return CheckResult("task_differs", SKIP, "first task in this session")

    # Kanonski identitet (pitanje+opcije) je serverski dokaz duplikata — isti
    # koji objava koristi (Faza 4G; vidi docstring `issued_new_task`).
    if obs.identity_after and obs.previous_task_identities:
        if obs.identity_after in obs.previous_task_identities:
            return CheckResult("task_differs", FAIL,
                               "identical canonical task identity")

    current = _normalized(obs.task_after)
    identity_proves_different = bool(
        obs.identity_after and obs.previous_task_identities
        and obs.identity_after not in obs.previous_task_identities)
    for previous in obs.previous_task_texts:
        if current == _normalized(previous):
            if identity_proves_different:
                # Isti tekst pitanja, ali kanonski različit paket (druge
                # opcije) — legitiman nov zadatak, ne duplikat.
                continue
            return CheckResult("task_differs", FAIL,
                               "the new task text is identical to an earlier one")

    signature = ((obs.session_after or {}).get("current_task_signature") or {})
    digest = signature.get("structured_signature_hash")
    if not digest:
        return CheckResult("task_differs", SKIP,
                           "no structured signature — difference cannot be proven")
    if digest in obs.previous_task_signatures:
        return CheckResult("task_differs", FAIL, "identical structured task signature")
    return CheckResult("task_differs", PASS)


def check_state_unchanged(obs: TurnObservation) -> CheckResult:
    if obs.session_after != obs.session_before:
        return CheckResult("state_unchanged", FAIL, "session state mutated on a rejected turn")
    return CheckResult("state_unchanged", PASS)


def check_identical_response(obs: TurnObservation) -> CheckResult:
    if obs.previous_response is None:
        return CheckResult("identical_response", SKIP, "no earlier response to compare with")
    if obs.response != obs.previous_response:
        return CheckResult("identical_response", FAIL, "idempotent retry produced a different response")
    return CheckResult("identical_response", PASS)


def check_package_clean(obs: TurnObservation) -> CheckResult:
    """Isti `package_preflight` koji server pokreće nad konačnim paketom.

    Faza F5G: server dokaz težine mjeri lekcijski-relativnim profilom
    razriješenim iz server-vlasničkog konteksta — evaluacija MORA mjeriti
    isto, inače objavljen valjan paket lažno pada na globalnoj rubrici
    (živi F5H nalaz: sva četiri profilirana scenarija)."""
    if obs.final_task_package is None:
        return CheckResult("package_clean", SKIP, "no structured package captured for this turn")
    from matbot import difficulty_profiles
    from matbot.tutor import lesson_context as lesson_context_module

    context = lesson_context_module.build(obs.grade, obs.topic_id)
    profile = difficulty_profiles.resolve_for_context(context)
    # F5K: i semantički ugovor vježbe — harness mjeri ISTO što i server.
    issues = package_preflight.collect_package_issues(
        obs.final_task_package, difficulty_profile=profile,
        practice_contract=getattr(context, "practice_contract", None))
    if issues:
        return CheckResult("package_clean", FAIL, package_preflight.describe_issues(issues))
    return CheckResult("package_clean", PASS)


def check_stays_in_lesson(obs: TurnObservation) -> CheckResult:
    """Poruka pokušava promijeniti temu ili uputiti model mimo moda."""
    session_lesson = (obs.session_after or {}).get("lesson_id")
    if session_lesson is not None and session_lesson != obs.topic_id:
        return CheckResult("stays_in_lesson", FAIL, f"session moved to lesson {session_lesson}")
    if (obs.response or {}).get("session_mode") not in (None, "practice"):
        return CheckResult("stays_in_lesson", FAIL,
                           f"session_mode={(obs.response or {}).get('session_mode')}")
    return CheckResult("stays_in_lesson", PASS)


def _make_calls_check(limit: int):
    def check(obs: TurnObservation) -> CheckResult:
        if obs.sdk_calls > limit:
            return CheckResult(f"calls_at_most:{limit}", FAIL, f"sdk_calls={obs.sdk_calls}")
        return CheckResult(f"calls_at_most:{limit}", PASS)
    return check


def _make_level_check(level: int):
    def check(obs: TurnObservation) -> CheckResult:
        actual = (obs.session_after or {}).get("difficulty_level")
        if actual is None:
            return CheckResult(f"level:{level}", SKIP, "no committed difficulty level")
        if actual != level:
            return CheckResult(f"level:{level}", FAIL, f"committed difficulty_level={actual}")
        return CheckResult(f"level:{level}", PASS)
    return check


_CHECKS = {
    "published": check_published,
    "not_published": check_not_published,
    "not_safe_error": check_not_safe_error,
    "no_fallback_text": check_no_fallback_text,
    "response_schema": check_response_schema,
    "no_answer_leak": check_no_answer_leak,
    "solution_complete": check_solution_complete,
    "free_text_grading_no_oracle": check_free_text_grading_no_oracle,
    "zero_calls": check_zero_calls,
    "task_published": check_task_published,
    "task_self_contained": check_task_self_contained,
    "no_new_task": check_no_new_task,
    "task_preserved": check_task_preserved,
    "options_ok": check_options_ok,
    "lesson_matches": check_lesson_matches,
    "no_leak": check_no_leak,
    "no_control_chars": check_no_control_chars,
    "math_safe": check_math_safe,
    "numeric_consistent": check_numeric_consistent,
    "geometry_ok": check_geometry_ok,
    "terminology_clean": check_terminology_clean,
    "bosnian": check_bosnian,
    "no_verdict": check_no_verdict,
    "verdict_correct": check_verdict_correct,
    "verdict_incorrect": check_verdict_incorrect,
    "task_completed": check_task_completed,
    "task_not_completed": check_task_not_completed,
    "correct_option_stable": check_correct_option_stable,
    "reveal_present": check_reveal_present,
    "reveal_absent": check_reveal_absent,
    "help_nonempty": check_help_nonempty,
    "hint_no_leak": check_hint_no_leak,
    "hint_differs": check_hint_differs,
    "task_differs": check_task_differs,
    "state_unchanged": check_state_unchanged,
    "identical_response": check_identical_response,
    "package_clean": check_package_clean,
    "stays_in_lesson": check_stays_in_lesson,
}

# Kvalitativne rubrike (0 = neprihvatljivo, 1 = djelimično, 2 = dobro).
# NIJEDNA se ne ocjenjuje automatski u ovoj fazi — prisustvo rubrike diže
# scenario na REVIEW, jer ocjena modela-sudije nije odobrena.
RUBRICS = {
    "clarity": "Je li tekst jasan i nedvosmislen učeniku?",
    "grade_fit": "Odgovara li težina i rječnik izabranom razredu?",
    "lesson_alignment": "Ispituje li zadatak BAŠ izabranu lekciju, a ne samo oblast?",
    "hint_usefulness": "Pomjera li hint učenika za tačno jedan koristan korak?",
    "difficulty_appropriate": "Je li promjena težine stvarna, a ne samo oznaka?",
    "pedagogy": "Je li odgovor tehnički ispravan ALI pedagoški loš?",
    "refusal_quality": "Je li odbijanje van teme pristojno i vraća li učenika na zadatak?",
    "unit_correctness": "Je li mjerna jedinica tačna (npr. cm naspram cm²)?",
}


def known_check_names() -> tuple:
    return tuple(sorted(_CHECKS)) + ("calls_at_most:N", "level:N")


def resolve(name: str):
    """Vrati funkciju provjere ili None kad ime nije poznato."""
    if name in _CHECKS:
        return _CHECKS[name]
    if name.startswith("calls_at_most:"):
        try:
            return _make_calls_check(int(name.split(":", 1)[1]))
        except ValueError:
            return None
    if name.startswith("level:"):
        try:
            return _make_level_check(int(name.split(":", 1)[1]))
        except ValueError:
            return None
    return None


def run_checks(names, obs: TurnObservation) -> list:
    results = []
    for name in names:
        check = resolve(name)
        if check is None:
            results.append(CheckResult(name, FAIL, "unknown check name"))
            continue
        try:
            results.append(check(obs))
        except Exception as error:      # dijagnostika nikad ne ruši kampanju
            results.append(CheckResult(name, SKIP, f"check raised {type(error).__name__}"))
    return results


# ---------------------------------------------------------------------------
# GRUPISANJE PADOVA PO ROOT CAUSE-U
# ---------------------------------------------------------------------------

_ROOT_CAUSE = {
    "published": "generation_or_publication_failure",
    "task_published": "generation_or_publication_failure",
    "task_self_contained": "unsolvable_or_incomplete_task",
    "not_safe_error": "technical_fallback_as_success",
    "no_fallback_text": "technical_fallback_as_success",
    "response_schema": "response_contract",
    "no_answer_leak": "answer_leak",
    "hint_no_leak": "answer_leak",
    "solution_complete": "solution_disclosure",
    "free_text_grading_no_oracle": "grading",
    "not_published": "guard_did_not_block",
    "zero_calls": "call_budget",
    "options_ok": "mcq_integrity",
    "package_clean": "mcq_integrity",
    "lesson_matches": "lesson_scope",
    "stays_in_lesson": "lesson_scope",
    "no_leak": "internal_leak",
    "no_control_chars": "notation_and_math",
    "math_safe": "notation_and_math",
    "numeric_consistent": "notation_and_math",
    "geometry_ok": "notation_and_math",
    "terminology_clean": "language",
    "bosnian": "language",
    "no_verdict": "grading",
    "verdict_correct": "grading",
    "verdict_incorrect": "grading",
    "task_completed": "session_state",
    "task_not_completed": "session_state",
    "correct_option_stable": "session_state",
    "task_preserved": "session_state",
    "no_new_task": "session_state",
    "state_unchanged": "session_state",
    "identical_response": "session_state",
    "reveal_present": "solution_disclosure",
    "reveal_absent": "solution_disclosure",
    "help_nonempty": "hint_quality",
    "hint_differs": "hint_quality",
    "task_differs": "repetition",
}


def root_cause(check_name: str) -> str:
    if check_name.startswith("calls_at_most:"):
        return "call_budget"
    if check_name.startswith("level:"):
        return "difficulty_control"
    return _ROOT_CAUSE.get(check_name, "other")
