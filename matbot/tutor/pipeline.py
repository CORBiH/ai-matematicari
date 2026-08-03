"""Univerzalni dvopozivni Practice turn — JEDAN aktivni put za svih 534 lekcije.

    LessonContext → Tutor poziv → Reviewer poziv → serverska validacija
    → objava → copy-on-write stanje

GRANICA POZIVA (tvrdo): najviše TAČNO DVA modela poziva po neblokiranom turnu.
  • blokirano prije modela (nepoznata lekcija, nevažeći klik) → 0 poziva
  • Tutor nacrt neparsabilan/timeout                          → 1 poziv, STOP
  • sve ostalo                                                → tačno 2 poziva
Nema retryja, repair petlje, trećeg poziva ni skrivene zamjene: kad recenzent
padne ili serverska validacija odbije, vraća se sigurna poruka.

STANJE: sesija je lokalna kopija i mijenja se TEK kad su oba poziva gotova,
konačan payload prošao šemu i sve serverske provjere, i kad zadatak nije
zastario. `store.save` je jedina commit tačka.

Ovaj modul NEMA nijednu granu po ID-ju lekcije. Sve što razlikuje lekcije
dolazi iz LessonContext-a (podaci), a semantičku kapiju opsega drži recenzent.
"""
import copy
import logging
import random
import uuid

from matbot import config, geometrycheck, option_equivalence
from matbot.llm import LLMError, failure_diagnostics_kv
from matbot.mathcheck import find_numeric_inconsistencies
from matbot.mathsafe import sanitize_and_validate_math_text
from matbot.terminology import normalize_terminology
from matbot.tutor import lesson_context as lesson_context_module
from matbot.tutor import prompts as tutor_prompts
from matbot.tutor.schema import (TASK_INTENTS, UnifiedOutputError,
                                 validate_final, validate_reviewer)

logger = logging.getLogger("matbot.tutor")

SAFE_ERROR_MESSAGE = (
    "Nešto je zapelo pri sastavljanju odgovora. Pošalji poruku ponovo za koji trenutak."
)

_NEW_TASK_INTRO = {
    "easier_task": "Evo lakšeg zadatka.",
    "harder_task": "Evo težeg zadatka.",
    "next_task": "Evo sljedećeg zadatka.",
    "generate_task": "Evo zadatka.",
}

_LOG_LIMIT = 200


def _clip(value, limit=_LOG_LIMIT):
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _error_response(active_task=""):
    """Namjerno BEZ 'status' i BEZ 'next_state' — frontend tada čuva svoje
    stanje (isti ugovor kao i ranije)."""
    return {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": active_task or ""}


def _next_state(session):
    state = {
        "v": 1,
        "correct_streak": session["correct_streak"],
        "hint_level": session["hint_level"],
    }
    if session["current_task"]:
        task = {"question": session["current_task"]}
        if session["current_options"]:
            task["options"] = session["current_options"]
        state["task"] = task
    return state


def _shuffle_options(texts, correct_index):
    """Server je jedini koji dodjeljuje ID-jeve opcijama i pamti tačan.
    Miješa se TAČNO JEDNOM po novom zadatku."""
    ids = ["a", "b", "c", "d"]
    pairs = list(enumerate(texts))
    random.shuffle(pairs)
    current_options, correct_option_id = [], ""
    for slot, (original_index, text) in enumerate(pairs):
        current_options.append({"id": ids[slot], "text": text})
        if original_index == correct_index:
            correct_option_id = ids[slot]
    return current_options, correct_option_id


# ---------------------------------------------------------------------------
# ZAJEDNIČKE SERVERSKE PROVJERE — identične za svih 534 lekcije
# ---------------------------------------------------------------------------

def _safe_text(raw, where, allow_wrap=False):
    """Sanitizacija + terminologija + math-safety. Baca UnifiedOutputError."""
    cleaned, safe = sanitize_and_validate_math_text(
        (raw or "").strip(), allow_whole_expression_wrap=allow_wrap
    )
    if not safe:
        raise UnifiedOutputError(f"nebezbjedan matematički zapis [{where}]")
    return normalize_terminology(cleaned)


def _reject_if_inconsistent(text, where):
    issues = find_numeric_inconsistencies(text)
    if issues:
        raise UnifiedOutputError(f"{issues[0]} [{where}]")


def _reject_if_geometry_invalid(text, context, where):
    issues = geometrycheck.find_geometry_issues(
        text, context.geometry_scope, list(context.geometry_figures)
    )
    if issues:
        raise UnifiedOutputError(f"geometry_notation: {','.join(issues)} [{where}]")


def _validate_task_server_side(task, context):
    """Sve deterministe koje su i ranije štitile objavljen zadatak.

    Vraća (tekst_zadatka, sanitizovani_tekstovi_opcija)."""
    task_text = _safe_text(task.text, "tekst zadatka")
    _reject_if_inconsistent(task_text, "tekst zadatka")
    _reject_if_geometry_invalid(task_text, context, "tekst zadatka")

    option_texts = [
        _safe_text(option.text, "opcija", allow_wrap=True) for option in task.options
    ]

    duplicates = option_equivalence.find_textual_duplicate_pairs(option_texts)
    if duplicates:
        raise UnifiedOutputError(f"duple opcije: {duplicates}")
    equivalent = option_equivalence.find_equivalent_option_pairs(option_texts)
    if equivalent:
        raise UnifiedOutputError(f"semantically_duplicate_options: {equivalent}")

    # Tačna opcija i očekivani odgovor nose PRAVU matematiku — uvijek se
    # provjeravaju. Distraktori su namjerno pogrešni i NIKAD se ne provjeravaju.
    correct_text = option_texts[task.correct_option_index]
    _reject_if_inconsistent(correct_text, "tačna opcija")
    _reject_if_geometry_invalid(correct_text, context, "tačna opcija")
    expected = _safe_text(task.expected_answer, "expected_answer", allow_wrap=True)
    _reject_if_inconsistent(expected, "expected_answer")

    return task_text, option_texts, expected


# ---------------------------------------------------------------------------
# DVA POZIVA
# ---------------------------------------------------------------------------

def _call_tutor(llm, context, session, student_message, trusted_verdict):
    instructions = tutor_prompts.build_tutor_instructions(context)
    input_text = tutor_prompts.build_tutor_input(
        context, session, student_message, trusted_verdict
    )
    return llm.tutor_turn(instructions, input_text)


def _call_reviewer(llm, context, session, student_message, draft, trusted_verdict):
    instructions = tutor_prompts.build_reviewer_instructions(context)
    draft_json = draft.model_dump_json(indent=None, exclude_none=True)
    input_text = tutor_prompts.build_reviewer_input(
        context, session, student_message, draft_json, trusted_verdict
    )
    return llm.reviewer_turn(instructions, input_text)


def _log_rejection(request_id, context, stage, detail, intent=""):
    """Interna dijagnostika — NIKAD u browser."""
    logger.warning(
        "tutor_rejected request_id=%s topic=%s stage=%s intent=%s detail=%s",
        request_id, context.topic_id if context else "", stage, intent or "-",
        _clip(detail, 300),
    )


def _log_difficulty(request_id, context, final):
    """Dijagnostika težine ide SAMO u log (učenik je nikad ne vidi)."""
    diagnostics = final.difficulty_diagnostics
    if diagnostics is None:
        return
    logger.info(
        "tutor_difficulty request_id=%s topic=%s intent=%s magnitude=%s steps=%s "
        "representation=%s sign=%s scaffolding=%s distractors=%s reasoning=%s why=%s",
        request_id, context.topic_id, final.intent,
        diagnostics.number_magnitude, diagnostics.number_of_steps,
        diagnostics.representation_complexity, diagnostics.sign_complexity,
        diagnostics.scaffolding, diagnostics.distractor_closeness,
        diagnostics.reasoning_depth, _clip(diagnostics.rationale, 200),
    )


# ---------------------------------------------------------------------------
# ULAZNA TAČKA
# ---------------------------------------------------------------------------

def run_turn(store, llm, turn):
    """Jedan Practice turn za BILO KOJU od 534 lekcije."""
    request_id = uuid.uuid4().hex[:12]

    context = lesson_context_module.build(turn["grade"], turn["selected_topic"])
    if context is None:
        # Nepouzdan kurikularni kontekst → 0 poziva, bez stanja.
        logger.warning("tutor_turn request_id=%s invalid_curriculum_context", request_id)
        return _error_response()

    session = store.load(
        session_id=turn["session_id"],
        grade=turn["grade"],
        lesson_id=context.topic_id,
        lesson_title=context.title,
        oblast_id=context.oblast_id,
        oblast=context.oblast,
        mode="practice",
    )

    if turn.get("interaction_type") == "choice_answer":
        return _run_choice_turn(store, llm, session, turn, context, request_id)
    return _run_text_turn(store, llm, session, turn, context, request_id)


def _resolve_choice(session, turn):
    """Deterministička obrada klika PRIJE ijednog poziva.

    Vraća (trusted_verdict, cached_response, blocked). Tačnost klika je
    SERVERSKA činjenica — model je saopštava, nikad ne utvrđuje."""
    if not session["current_options"] or not session["correct_option_id"]:
        return None, None, True
    client_turn_id = turn.get("client_turn_id") or ""
    if (client_turn_id and client_turn_id == session["last_choice_turn_id"]
            and session["last_choice_response"] is not None):
        # Idempotentan retry: identičan odgovor, bez poziva i bez mutacije.
        return None, copy.deepcopy(session["last_choice_response"]), False
    if session["task_completed"]:
        return None, None, True

    selected_id = turn.get("selected_option_id") or ""
    options_by_id = {option["id"]: option for option in session["current_options"]}
    if selected_id not in options_by_id:
        return None, None, True

    return {
        "selected_text": options_by_id[selected_id]["text"],
        "selected_id": selected_id,
        "is_correct": selected_id == session["correct_option_id"],
        "wrong_attempts": len(session["wrong_option_ids"]),
    }, None, False


def _run_choice_turn(store, llm, session, turn, context, request_id):
    active_task_before = session["current_task"]
    verdict, cached, blocked = _resolve_choice(session, turn)
    if cached is not None:
        return cached
    if blocked:
        return _error_response(active_task_before)

    is_correct = verdict["is_correct"]
    wrong_before = verdict["wrong_attempts"]

    # Napredovanje se računa na KOPIJI; commit ide tek na kraju.
    if is_correct:
        session["correct_streak"] += 1
        session["task_completed"] = True
        session["last_result"] = "correct"
        session["retry_required"] = False
        family = session["current_family"]
        if family and family not in session["correctly_completed_families"]:
            session["correctly_completed_families"].append(family)
    else:
        session["correct_streak"] = 0
        session["wrong_option_ids"].append(verdict["selected_id"])
        session["last_result"] = "incorrect"
        session["retry_required"] = True
        if wrong_before >= 1:
            session["task_completed"] = True

    final, calls = _two_call(
        llm, context, session, turn["student_message"], request_id, verdict
    )
    if final is None:
        return _error_response(active_task_before)

    try:
        reply = _safe_text(final.reply, "reply")
        _reject_if_inconsistent(reply, "reply")
        _reject_if_geometry_invalid(reply, context, "reply")
    except UnifiedOutputError as error:
        _log_rejection(request_id, context, "choice_reply", error, final.intent)
        return _error_response(active_task_before)

    session["recent_turns"].append({
        "student": f"[izabrao opciju: {verdict['selected_text']}]"[:300],
        "tutor": reply[:400],
    })

    response = {
        "status": "ready",
        "answer": reply,
        "answer_verdict": "correct" if is_correct else "incorrect",
        "last_tutor_task": session["current_task"] or "",
        "next_state": _next_state(session),
        "session_mode": "practice",
        "effective_topic": context.topic_id,
    }
    # Tačna opcija se otkriva SAMO na drugi pogrešan klik.
    if not is_correct and wrong_before >= 1:
        response["revealed_correct_option_id"] = session["correct_option_id"]

    client_turn_id = turn.get("client_turn_id") or ""
    if client_turn_id:
        session["last_choice_turn_id"] = client_turn_id
        session["last_choice_response"] = copy.deepcopy(response)

    store.save(session)   # JEDINA commit tačka
    logger.info(
        "tutor_choice request_id=%s topic=%s is_correct=%s calls=%s",
        request_id, context.topic_id, is_correct, calls,
    )
    return response


def _run_text_turn(store, llm, session, turn, context, request_id):
    active_task_before = session["current_task"]
    had_active_task = bool(active_task_before)

    final, calls = _two_call(
        llm, context, session, turn["student_message"], request_id, None
    )
    if final is None:
        return _error_response(active_task_before)

    try:
        reply = _safe_text(final.reply, "reply")
        _reject_if_inconsistent(reply, "reply")
        _reject_if_geometry_invalid(reply, context, "reply")

        task_text = active_task_before
        if final.intent in TASK_INTENTS:
            task_text = _publish_task(session, context, final, request_id)
            intro = _NEW_TASK_INTRO.get(final.intent, _NEW_TASK_INTRO["generate_task"])
            answer = intro + "\n\nZadatak: " + task_text
        else:
            answer = reply
            if final.intent == "hint_request":
                session["hint_level"] = min(
                    session["hint_level"] + 1, config.MAX_HINT_LEVEL
                )
    except UnifiedOutputError as error:
        _log_rejection(request_id, context, "publication", error, final.intent)
        return _error_response(active_task_before)

    session["recent_turns"].append(
        {"student": turn["student_message"][:300], "tutor": answer[:400]}
    )

    response = {
        "status": "ready",
        "answer": answer,
        "answer_verdict": None,      # tekstualni turn se nikad ne ocjenjuje
        "last_tutor_task": session["current_task"] or "",
        "next_state": _next_state(session),
        "session_mode": "practice",
        "effective_topic": context.topic_id,
    }
    # „Uradi ga ti“: server deterministički završava zadatak i otkriva opciju.
    if (final.intent == "full_solution_request" and had_active_task
            and session["correct_option_id"]):
        session["task_completed"] = True
        response["revealed_correct_option_id"] = session["correct_option_id"]

    store.save(session)   # JEDINA commit tačka
    logger.info(
        "tutor_turn request_id=%s topic=%s intent=%s calls=%s",
        request_id, context.topic_id, final.intent, calls,
    )
    return response


def _publish_task(session, context, final, request_id):
    """Provjeri i primijeni nov zadatak. Baca UnifiedOutputError (fail closed)."""
    task = final.new_task
    task_text, option_texts, expected = _validate_task_server_side(task, context)

    current_options, correct_option_id = _shuffle_options(
        option_texts, task.correct_option_index
    )
    session["current_task"] = task_text
    session["expected_answer_summary"] = expected
    session["difficulty"] = task.difficulty
    session["hint_level"] = 0
    session["recent_tasks"].append(task_text)
    session["current_options"] = current_options
    session["correct_option_id"] = correct_option_id
    session["wrong_option_ids"] = []
    session["task_completed"] = False
    session["last_choice_turn_id"] = ""
    session["last_choice_response"] = None

    # Napredovanje: oblik zadatka za ovu lekciju dolazi iz konteksta lekcije.
    family = context.primary_family
    if family:
        session["current_family"] = family
        recent = session["recently_used_families"]
        if not recent or recent[-1] != family:
            recent.append(family)
    _log_difficulty(request_id, context, final)
    return task_text


def _log_sdk_entry(request_id, context, stage, call_index, result):
    """JEDAN strukturisan red PO MODELSKOM POZIVU.

    Postoji zbog računovodstva dvopozivnog puta: uspješan turn mora ostaviti
    TAČNO DVA ovakva reda (stage=tutor call=1, stage=reviewer call=2), pa se
    broj poziva i trošak mogu prebrojati iz loga bez pogađanja. Loguje se samo
    latencija i usage — nikad prompt, izlaz modela ni ijedan sadržaj."""
    logger.info(
        "tutor_sdk_call request_id=%s topic=%s stage=%s call=%s latency_ms=%s usage=%s",
        request_id, context.topic_id, stage, call_index,
        getattr(result, "latency_ms", None), getattr(result, "usage", None),
    )


def _two_call(llm, context, session, student_message, request_id, trusted_verdict):
    """Tutor → Reviewer. Vraća (final_draft | None, broj_poziva).

    Nijedna grana ne pravi treći poziv. Kad Tutor nacrt ne može ni da se
    isparsira, DRUGI POZIV SE NE DEŠAVA — nema šta da se recenzira."""
    calls = 0
    try:
        tutor_result = _call_tutor(
            llm, context, session, student_message, trusted_verdict
        )
        calls += 1
        _log_sdk_entry(request_id, context, "tutor", calls, tutor_result)
    except LLMError as error:
        logger.warning(
            "tutor_call request_id=%s topic=%s stage=tutor call=1 %s",
            request_id, context.topic_id, failure_diagnostics_kv(error),
        )
        return None, calls

    draft = tutor_result.output
    has_active_task = bool(session["current_task"])
    try:
        validate_final(draft, has_active_task=has_active_task)
    except UnifiedOutputError as error:
        # Neupotrebljiv nacrt: recenzent se NE poziva (nema validnog predmeta),
        # pa turn staje na jednom pozivu.
        _log_rejection(request_id, context, "tutor_draft", error, draft.intent)
        return None, calls

    try:
        reviewer_result = _call_reviewer(
            llm, context, session, student_message, draft, trusted_verdict
        )
        calls += 1
        _log_sdk_entry(request_id, context, "reviewer", calls, reviewer_result)
    except LLMError as error:
        logger.warning(
            "reviewer_call request_id=%s topic=%s stage=reviewer call=2 %s",
            request_id, context.topic_id, failure_diagnostics_kv(error),
        )
        return None, calls

    reviewer = reviewer_result.output
    try:
        validate_reviewer(reviewer)
    except UnifiedOutputError as error:
        _log_rejection(request_id, context, "reviewer_payload", error, draft.intent)
        return None, calls

    if reviewer.decision == "fail_closed":
        _log_rejection(
            request_id, context, "reviewer_fail_closed",
            reviewer.fail_reason_code, draft.intent,
        )
        return None, calls

    final = reviewer.final
    try:
        validate_final(final, has_active_task=has_active_task)
    except UnifiedOutputError as error:
        _log_rejection(request_id, context, "reviewer_final", error, final.intent)
        return None, calls

    if reviewer.decision == "correct":
        logger.info(
            "tutor_corrected request_id=%s topic=%s intent=%s",
            request_id, context.topic_id, final.intent,
        )
    return final, calls
