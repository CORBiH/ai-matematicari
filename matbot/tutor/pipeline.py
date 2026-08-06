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

from matbot import (config, difficulty_level, feedback, geometrycheck, mcq_integrity,
                    option_equivalence)
from matbot.llm import LLMError, failure_diagnostics_kv
from matbot.mathcheck import find_numeric_inconsistencies
from matbot.semantics import detectors as semantic_detectors
from matbot.tutor import lesson_context as lesson_context_module
from matbot.tutor import package_preflight
from matbot.tutor import reviewer_authority
from matbot.tutor import prompts as tutor_prompts
from matbot.tutor.schema import (TASK_INTENTS, UnifiedOutputError,
                                 normalize_for_intent, validate_final, validate_task,
                                 validate_difficulty_evidence, validate_reviewer)

logger = logging.getLogger("matbot.tutor")

SAFE_ERROR_MESSAGE = (
    "Nešto je zapelo pri sastavljanju odgovora. Pošalji poruku ponovo za koji trenutak."
)

# Recenzentov KONAČAN paket i dalje nosi dokazan deterministički defekt. Zaseban
# kod da gate ovo razlikuje od obične završne validacije i od pada šeme.
REVIEWER_FINAL_INTEGRITY_CODE = "reviewer_final_mcq_integrity_rejection"

_NEW_TASK_INTRO = {
    "easier_task": "Evo lakšeg zadatka.",
    "harder_task": "Evo težeg zadatka.",
    "next_task": "Evo sljedećeg zadatka.",
    "generate_task": "Evo zadatka.",
}


def _target_level_for(session, intent):
    """One lesson-independent progression policy owned by the server."""
    current = min(max(int(session.get("difficulty_level", 1)), 1), 3)
    if not session.get("current_task"):
        return 1
    if intent == "harder_task":
        return min(current + 1, 3)
    if intent == "easier_task":
        return max(current - 1, 1)
    if intent == "next_task":
        if session.get("last_result") == "full_solution":
            return max(current - 1, 1)
        if session.get("correct_streak", 0) >= 2 and not session.get("current_task_had_hint"):
            return min(current + 1, 3)
    return current


def _difficulty_levels_enabled():
    """Use the shared opt-in controller flag without importing Practice."""
    return config.practice_difficulty_levels_enabled()


# ---------------------------------------------------------------------------
# EKSPLICITNA UI AKCIJA NAD AKTIVNIM ZADATKOM (živi produkcijski nalaz)
# ---------------------------------------------------------------------------
# Niz iz produkcije: objavljen zadatak → „Ne znam — daj mi hint“ (hint uredan)
# → „Uradi ga ti“ → server je objavio POTPUNO NOV zadatak i pregazio aktivan.
#
# UZROK: ovaj put nikad nije čitao `turn["intent"]`. Frontend eksplicitno šalje
# `intent=solution_request` kad učenik pritisne to dugme, ali je taj signal
# ostajao neiskorišten — namjeru je odlučivao ISKLJUČIVO model iz slobodnog
# teksta „Uradi ga ti.“, a kad vrati `next_task`, `_run_text_turn` objavi paket.
#
# ZAŠTO OVO NE KRŠI „klijentu se ne vjeruje“: vrijednost se koristi SAMO da
# SUZI ono što server smije uraditi. Ona ne može odobriti nijednu objavu koja
# inače ne bi prošla, ne bira lekciju, ne dira ocjenjivanje i ne otkriva
# odgovor; lažna vrijednost može izazvati odbijanje, nikad publikaciju. Drugi
# uslov je SERVERSKA činjenica: zabrana važi samo dok aktivan zadatak postoji.
_UI_ACTION_INTENTS = {
    "solution_request": "full_solution_request",
    "hint_request": "hint_request",
}


def _explicit_ui_action(turn, session):
    """Namjera iz šeme koju je učenik dugmetom izričito tražio, ili prazno.

    Prazno znači „kucana poruka“ — tada se ništa ne mijenja u odnosu na raniji
    tok i namjeru i dalje određuje model."""
    if not session.get("current_task"):
        return ""
    return _UI_ACTION_INTENTS.get((turn.get("intent") or "").strip().lower(), "")


def canonicalize_task_lesson_title(task, context):
    """Make the server-owned LessonContext the sole display-title authority.

    The model must still provide the exact selected lesson ID.  Once that ID
    is known to be correct, its repeated title is only a non-authoritative
    display copy, so harmless spelling or formatting drift cannot reject an
    otherwise Reviewer-approved package.
    """
    if task is None or task.selected_lesson_id != context.topic_id:
        return task
    if task.selected_lesson_title == context.title:
        return task
    return task.model_copy(update={"selected_lesson_title": context.title})


def _canonicalize_draft_lesson_title(draft, context):
    task = getattr(draft, "new_task", None)
    canonical_task = canonicalize_task_lesson_title(task, context)
    return draft if canonical_task is task else draft.model_copy(update={"new_task": canonical_task})


def validate_task_package(task, context, target_level=None):
    """Universal package invariants; semantic lesson judgement stays with Reviewer."""
    task = canonicalize_task_lesson_title(task, context)
    validate_task(task)
    if task.selected_lesson_id != context.topic_id:
        raise UnifiedOutputError("task lesson ID does not match selected lesson")
    # Structured generation always requires a complete package, but only the
    # explicitly enabled controller owns target validation and rubric policy.
    if target_level is not None:
        if task.target_difficulty_level != target_level:
            raise UnifiedOutputError("task target difficulty does not match server target")
        validate_difficulty_evidence(task)
    return task


def _structured_signature_record(task, context):
    canonical = task.task_signature.canonical_json()
    return {
        "lesson_id": context.topic_id,
        "structured_signature": canonical,
        "structured_signature_hash": task.task_signature.digest(),
    }


def _is_duplicate_structured_signature(record, prior_records):
    return any(
        previous.get("lesson_id") == record["lesson_id"]
        and previous.get("structured_signature_hash") == record["structured_signature_hash"]
        for previous in (prior_records or [])
    )

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
    """Sanitizacija + terminologija + math-safety. Baca UnifiedOutputError.

    Sam niz koraka živi u `package_preflight.safe_visible_text` — JEDNA
    implementacija, koju preflight koristi bez izuzetka, a objava uz izuzetak."""
    cleaned, safe = package_preflight.safe_visible_text(raw, allow_wrap=allow_wrap)
    if not safe:
        raise UnifiedOutputError(f"nebezbjedan matematički zapis [{where}]")
    return cleaned


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


def _reject_if_semantic_contract_violated(text, context, where):
    """ODBRANA U DUBINI (Faza 4B): isti detektor koji je već pokrenut u
    preflightu i u invarijanti nad recenzentovim paketom, sada i NEPOSREDNO
    PRIJE OBJAVE — dakle prije ijedne mutacije sesije.

    Ne uvodi novi prag: `unsupported` i dalje nikad ne odbija, a blokira samo
    lekcija čiji ugovor je izričito `blocking`."""
    contract = getattr(context, "semantic_contract", None)
    if contract is None or not getattr(contract, "blocking", False):
        return
    detection = semantic_detectors.detect(contract, text)
    if detection.status == semantic_detectors.STATUS_FAIL:
        raise UnifiedOutputError(
            f"{detection.code}: {detection.reason} [{where}]")


def _validate_task_server_side(task, context):
    """Sve deterministe koje su i ranije štitile objavljen zadatak.

    Vraća (tekst_zadatka, sanitizovani_tekstovi_opcija)."""
    task_text = _safe_text(task.text, "tekst zadatka")
    _reject_if_inconsistent(task_text, "tekst zadatka")
    _reject_if_geometry_invalid(task_text, context, "tekst zadatka")
    _reject_if_semantic_contract_violated(task_text, context, "tekst zadatka")

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
    if expected.strip() != correct_text.strip():
        raise UnifiedOutputError("expected answer does not match marked option")
    solution = _safe_text(task.solution, "solution")
    _reject_if_inconsistent(solution, "solution")

    # USKI MATEMATIČKI ORAKL NEPOSREDNO PRIJE OBJAVE (živi produkcijski nalaz,
    # lekcija o pravilima djeljivosti): objavljen je MCQ bez ijednog tačnog
    # odgovora — nijedna od četiri ponuđene opcije nije zadovoljavala uslov. Isti
    # `mcq_integrity` nalaz preflight već računa i šalje recenzentu, ali je to
    # bila JEDINA kapija na ovom putu — sve ostalo u ovoj funkciji provjerava
    # zapis i međusobnu dosljednost polja, nikad samu matematiku ponuđenih
    # opcija. Legacy put ovaj orakl odavno pokreće u `_apply_new_task`; ovdje
    # nedostajao. Nalaz ovdje znači fail closed prije IJEDNE mutacije sesije i
    # bez trećeg poziva; za neprimjenjiv oblik orakl i dalje ćuti.
    mcq_failure, _mcq_result = mcq_integrity.publication_failure(
        task_text, option_texts, task.correct_option_index, expected,
    )
    if mcq_failure:
        raise UnifiedOutputError(f"mcq_integrity: {mcq_failure}")

    return task_text, option_texts, expected, solution


# ---------------------------------------------------------------------------
# DVA POZIVA
# ---------------------------------------------------------------------------

def _call_tutor(llm, context, session, student_message, trusted_verdict, ui_action=""):
    instructions = tutor_prompts.build_tutor_instructions(context)
    input_text = tutor_prompts.build_tutor_input(
        context, session, student_message, trusted_verdict, ui_action
    )
    return llm.tutor_turn(instructions, input_text)


def _call_reviewer(llm, context, session, student_message, draft, trusted_verdict,
                   preflight_block="", ui_action=""):
    instructions = tutor_prompts.build_reviewer_instructions(context)
    draft_json = draft.model_dump_json(indent=None, exclude_none=True)
    input_text = tutor_prompts.build_reviewer_input(
        context, session, student_message, draft_json, trusted_verdict,
        preflight_block, ui_action,
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
        session["correct_streak"] = (
            session["correct_streak"] + 1 if not session.get("current_task_had_hint") else 0
        )
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

    # PRVI pogrešan klik ne smije otkriti odgovor — isto pravilo kao za slobodan
    # tekst. Drugi pogrešan klik ga otkriva NAMJERNO (vidi niže), pa je gate
    # aktivan samo dok otkrivanje još nije zasluženo.
    if not is_correct and wrong_before < 1:
        reply = _guard_answer_leak(session, request_id, context, "answer_attempt", reply,
                                   student_message=turn["student_message"])

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
    ui_action = _explicit_ui_action(turn, session)

    final, calls = _two_call(
        llm, context, session, turn["student_message"], request_id, None, ui_action
    )
    if final is None:
        return _error_response(active_task_before)

    try:
        reply = _safe_text(final.reply, "reply")
        _reject_if_inconsistent(reply, "reply")
        _reject_if_geometry_invalid(reply, context, "reply")

        task_text = active_task_before
        if final.intent in TASK_INTENTS:
            # ODBRANA U DUBINI: `_two_call` isti nacrt već odbija prije
            # recenzenta, ali objava je posljednja tačka prije mutacije sesije
            # i ne smije ovisiti o tome da je raniji sloj odradio svoje.
            if ui_action:
                raise UnifiedOutputError(
                    f"ui_action_forbids_new_task: {ui_action} vs {final.intent}")
            target_level = (_target_level_for(session, final.intent)
                            if _difficulty_levels_enabled() else None)
            canonical_task = validate_task_package(final.new_task, context, target_level)
            if canonical_task is not final.new_task:
                final = final.model_copy(update={"new_task": canonical_task})
            task_text = _publish_task(session, context, final, request_id, target_level)
            intro_intent = final.intent if target_level is not None else "generate_task"
            intro = _NEW_TASK_INTRO.get(intro_intent, _NEW_TASK_INTRO["generate_task"])
            answer = intro + "\n\nZadatak: " + task_text
        else:
            answer = _compose_visible_help(final, reply, context)
            # Treći hint po prompt ljestvici SMIJE pokazati cijeli postupak i
            # rezultat; `hint_level` je ovdje još broj RANIJE datih hintova.
            answer = _guard_answer_leak(
                session, request_id, context, final.intent, answer,
                student_message=turn["student_message"],
                is_hint_ladder_top=(final.intent == "hint_request"
                                    and session["hint_level"] >= config.MAX_HINT_LEVEL - 1),
            )
            if final.intent == "hint_request":
                session["hint_level"] = min(
                    session["hint_level"] + 1, config.MAX_HINT_LEVEL
                )
                session["current_task_had_hint"] = True
            elif final.intent == "full_solution_request":
                session["last_result"] = "full_solution"
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


def _compose_visible_help(final, reply, context):
    """Sastavi ono što učenik STVARNO vidi za hint/rješenje.

    ZAŠTO POSTOJI (ručni test, 2026-08-03): na „Ne znam“ je model najavu stavio
    u `reply` („Evo ti uputa koja će ti pomoći.“), a KORISTAN sadržaj u zasebno
    polje `hint`. Frontend prikazuje isključivo `answer`, pa je učenik dobio
    obećanje hinta bez hinta. Isti rizik nosi `worked_solution`.

    Zato se korisno polje PRIPAJA vidljivom tekstu kad ga tekst već ne sadrži.
    Polja i dalje prolaze istu sanitizaciju kao svaki vidljivi tekst."""
    extra = None
    if final.intent == "hint_request":
        extra = (final.hint or "").strip()
    elif final.intent == "full_solution_request":
        extra = (final.worked_solution or "").strip()
    if not extra:
        return reply

    safe_extra = _safe_text(extra, final.intent)
    _reject_if_inconsistent(safe_extra, final.intent)
    _reject_if_geometry_invalid(safe_extra, context, final.intent)

    # Model je ponekad već ugradio hint u reply — tada se ne duplira.
    if safe_extra and safe_extra in reply:
        return reply
    return (reply.rstrip() + "\n\n" + safe_extra).strip() if reply.strip() else safe_extra


# ---------------------------------------------------------------------------
# ZAŠTITA OD CURENJA ODGOVORA (živi nalaz B53, dvije kampanje)
# ---------------------------------------------------------------------------
# Na pogrešan slobodan pokušaj („Mislim da je rješenje x=3.“) tutor je vratio
# cijeli postupak i doslovno committed rješenje $x=7$ — koje je istovremeno bilo
# i označena opcija na ekranu. Zadatak je ostao otvoren, pa je učeniku preostalo
# samo da klikne ponuđeni odgovor. Ponovilo se i na drugi pokušaj.
#
# Legacy Practice put ovo hvata (`feedback.shape_first_wrong_feedback`), a
# univerzalni put je pri pivotu ostao BEZ ijednog determinističkog anti-leak
# sloja. Ovdje se koristi ISTI `feedback.leaks_answer`, ne novi detektor.
#
# Gate je namjerno uzak, jer treći korak istog scenarija (koristan hint bez
# rezultata) NIJE procurio i mora ostati netaknut:
#   • primjenjuje se samo uz AKTIVAN i NEZAVRŠEN zadatak,
#   • nikad na `full_solution_request` (tamo je otkrivanje ispravno),
#   • nikad na treći hint (prompt ljestvica ga izričito traži),
#   • nikad kad ista vrijednost već stoji u tekstu zadatka — tada je to
#     prepričavanje, ne curenje, i ne može se dokazati.
# Na pozitivan nalaz NEMA drugog poziva modela: opasan tekst se zamjenjuje
# sigurnim sljedećim korakom, a serverski verdikt i stanje ostaju netaknuti.
_LEAK_GUARDED_INTENTS = frozenset({
    "answer_attempt", "hint_request", "explanation_request", "clarification",
})

LEAK_BLOCKED_REPLY = "Hajde da to riješimo korak po korak.\n\n" + feedback.GENERIC_HINT


def _committed_answer(session):
    """Tačna opcija i očekivani odgovor — serverska istina, nikad modelova."""
    correct_id = session.get("correct_option_id") or ""
    marked = next((option.get("text", "") for option in (session.get("current_options") or [])
                   if isinstance(option, dict) and option.get("id") == correct_id), "")
    return marked, session.get("expected_answer_summary") or ""


def _reveals_committed_answer(session, text, student_message=""):
    """True samo kad se curenje MOŽE dokazati nad serverski committed odgovorom.

    Dvije eksplicitne iznimke — u obje vrijednost NIJE došla od tutora:
      • već stoji u tekstu samog zadatka (prepričavanje, ne otkrivanje),
      • učenik ju je sam napisao (uključujući TAČAN pokušaj, koji se smije
        potvrditi)."""
    marked, expected = _committed_answer(session)
    if not marked and not expected:
        return False
    task = session.get("current_task") or ""
    # `task` ide detektoru da broj koji već stoji u zadatku ne bi bio dokaz.
    if not feedback.leaks_answer(text, marked, expected, task_text=task):
        return False
    if feedback.leaks_answer(task, marked, expected):
        return False
    return not feedback.leaks_answer(student_message or "", marked, expected, task_text=task)


def _guard_answer_leak(session, request_id, context, intent, answer, *,
                       student_message="", is_hint_ladder_top=False):
    """Vrati bezbjedan tekst umjesto onog koji otkriva committed odgovor."""
    if intent not in _LEAK_GUARDED_INTENTS or is_hint_ladder_top:
        return answer
    if not session.get("current_task") or session.get("task_completed"):
        return answer
    if not _reveals_committed_answer(session, answer, student_message):
        return answer
    logger.warning(
        "tutor_answer_leak_blocked request_id=%s topic=%s intent=%s hint_level=%s",
        request_id, context.topic_id, intent, session.get("hint_level"),
    )
    return LEAK_BLOCKED_REPLY


def _publish_task(session, context, final, request_id, target_level=None):
    """Provjeri i primijeni nov zadatak. Baca UnifiedOutputError (fail closed)."""
    task = final.new_task
    task_text, option_texts, expected, solution = _validate_task_server_side(task, context)

    current_options, correct_option_id = _shuffle_options(
        option_texts, task.correct_option_index
    )
    signature_record = _structured_signature_record(task, context)
    if _is_duplicate_structured_signature(signature_record, session["recent_task_signatures"]):
        raise UnifiedOutputError("duplicate structured task signature")
    session["current_task"] = task_text
    session["expected_answer_summary"] = expected
    session["solution_summary"] = solution
    if target_level is not None:
        session["difficulty"] = difficulty_level.LEVEL_TO_LABEL[target_level]
        session["difficulty_level"] = target_level
    session["hint_level"] = 0
    session["recent_tasks"].append(task_text)
    session["current_options"] = current_options
    session["correct_option_id"] = correct_option_id
    session["wrong_option_ids"] = []
    session["task_completed"] = False
    session["current_task_had_hint"] = False
    session["last_choice_turn_id"] = ""
    session["last_choice_response"] = None

    # Napredovanje: oblik zadatka za ovu lekciju dolazi iz konteksta lekcije.
    family = context.primary_family
    if family:
        session["current_family"] = family
        recent = session["recently_used_families"]
        if not recent or recent[-1] != family:
            recent.append(family)
    session["current_task_signature"] = signature_record
    session["current_task_difficulty_evidence"] = task.difficulty_evidence.model_dump()
    session["recent_task_signatures"].append(signature_record)
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


def _two_call(llm, context, session, student_message, request_id, trusted_verdict,
              ui_action=""):
    """Tutor → Reviewer. Vraća (final_draft | None, broj_poziva).

    Nijedna grana ne pravi treći poziv. Kad Tutor nacrt ne može ni da se
    isparsira, DRUGI POZIV SE NE DEŠAVA — nema šta da se recenzira.

    `ui_action` je namjera koju je učenik izričito tražio dugmetom nad AKTIVNIM
    zadatkom (vidi `_explicit_ui_action`). Kad postoji, nov zadatak se u ovom
    turnu ne smije izdati — pa se nacrt s takvom namjerom odbija ODMAH, prije
    recenzenta: nema šta recenzirati kad paket ionako ne smije biti objavljen."""
    calls = 0
    try:
        tutor_result = _call_tutor(
            llm, context, session, student_message, trusted_verdict, ui_action
        )
        calls += 1
        _log_sdk_entry(request_id, context, "tutor", calls, tutor_result)
    except LLMError as error:
        logger.warning(
            "tutor_call request_id=%s topic=%s stage=tutor call=1 %s",
            request_id, context.topic_id, failure_diagnostics_kv(error),
        )
        return None, calls

    draft = normalize_for_intent(tutor_result.output)
    has_active_task = bool(session["current_task"])
    try:
        validate_final(draft, has_active_task=has_active_task)
    except UnifiedOutputError as error:
        # Neupotrebljiv nacrt: recenzent se NE poziva (nema validnog predmeta),
        # pa turn staje na jednom pozivu.
        _log_rejection(request_id, context, "tutor_draft", error, draft.intent)
        return None, calls

    # Reviewer sees the canonical server title; raw Tutor output remains on
    # the wrapper for safe offline diagnostics only.
    draft = _canonicalize_draft_lesson_title(draft, context)

    # Only task publication needs the independent generation Reviewer.  A
    # normal answer, hint, explanation, or full-solution turn is complete
    # after Tutor semantically interpreted the free-form student message.
    if draft.intent not in TASK_INTENTS:
        return draft, calls

    # KONTINUITET AKTIVNOG ZADATKA JE SERVERSKA ODLUKA, NE MODELOVA.
    if ui_action:
        _log_rejection(
            request_id, context, "ui_action_forbids_new_task",
            f"ui_action={ui_action} draft_intent={draft.intent}", draft.intent,
        )
        return None, calls

    # ------------------------------------------------------------------
    # PREFLIGHT NAD TUTOROVIM NACRTOM (živi gate 00bbd45)
    # ------------------------------------------------------------------
    # Isti deterministički validatori koje objava ionako pokreće, ali SADA —
    # dok drugi poziv još nije napravljen. Nalaz se NE koristi za odbijanje
    # nacrta (nacrt s nalazom je upravo ono što recenzent treba da popravi),
    # nego ulazi u recenzentov ulaz kao serverska činjenica.
    draft_issues = package_preflight.collect_package_issues(
        draft.new_task, contract=context.semantic_contract)
    if draft_issues:
        logger.info(
            "tutor_draft_preflight request_id=%s topic=%s intent=%s issues=%s",
            request_id, context.topic_id, draft.intent,
            package_preflight.describe_issues(draft_issues),
        )

    try:
        reviewer_result = _call_reviewer(
            llm, context, session, student_message, draft, trusted_verdict,
            package_preflight.format_for_reviewer(draft_issues),
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

    final = normalize_for_intent(reviewer.final)
    if final.new_task is not None:
        # The first-call wrapper retains the Tutor's self-description. Only
        # the independently returned Reviewer evidence becomes authoritative.
        final = final.model_copy(update={
            "new_task": final.new_task.model_copy(update={
                "difficulty_evidence": reviewer.reviewed_difficulty_evidence,
            }),
        })
    try:
        validate_final(final, has_active_task=has_active_task)
    except UnifiedOutputError as error:
        _log_rejection(request_id, context, "reviewer_final", error, final.intent)
        return None, calls

    # Defense in depth for a Reviewer-provided final package title.
    final = _canonicalize_draft_lesson_title(final, context)

    # ------------------------------------------------------------------
    # INVARIJANTA NAD RECENZENTOVIM KONAČNIM PAKETOM (živi gate 00bbd45)
    # ------------------------------------------------------------------
    # Isti skup provjera, sad nad onim što bi se STVARNO objavilo. Odbija se
    # PRIJE ijedne mutacije sesije, i to sa specifičnim kodom — da se u gateu
    # razlikuje od obične završne validacije. Provjera u objavi OSTAJE
    # (odbrana u dubini): ovo je raniji, precizniji sloj, ne zamjena.
    if final.new_task is not None:
        final_issues = package_preflight.collect_package_issues(
            final.new_task, contract=context.semantic_contract)
        if final_issues:
            # `unchanged=True` znači: recenzent je vidio nalaz i vratio paket s
            # POTPUNO ISTIM nalazima — dakle nije ni pokušao ispravku.
            unchanged = bool(draft_issues) and (
                package_preflight.describe_issues(final_issues)
                == package_preflight.describe_issues(draft_issues))
            _log_rejection(
                request_id, context, "reviewer_final_mcq",
                f"{REVIEWER_FINAL_INTEGRITY_CODE}: decision={reviewer.decision} "
                f"in_tutor_preflight={bool(draft_issues)} unchanged={unchanged} "
                f"{package_preflight.describe_issues(final_issues)}",
                final.intent,
            )
            return None, calls

    # Netačne provjere koje po matrici autoriteta NE obaraju turn i dalje se
    # LOGUJU — signal se ne smije izgubiti samo zato što ne blokira.
    diagnostic = reviewer_authority.diagnostic_failed_checks(reviewer.checks)
    if diagnostic:
        logger.info(
            "reviewer_non_blocking_checks request_id=%s topic=%s decision=%s checks=%s",
            request_id, context.topic_id, reviewer.decision, ",".join(diagnostic),
        )
    if reviewer.decision == "correct":
        logger.info(
            "tutor_corrected request_id=%s topic=%s intent=%s",
            request_id, context.topic_id, final.intent,
        )
    return final, calls
