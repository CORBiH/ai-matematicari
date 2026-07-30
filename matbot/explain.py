"""Orkestracija JEDNOG Explain turna („Objasni mi“): prompt → jedan AI poziv →
validacija → sanitizacija → response u frontend ugovoru.

Namjerno STATELESS na serveru: Explain nema aktivni zadatak, hint nivo, streak
ni session store — sav potreban kontekst je conversation_history koju frontend
već šalje (max 5 poruka; ovdje koristimo najviše 3 razmjene). Greška AI poziva
vraća kratku sigurnu poruku BEZ 'status' i BEZ 'next_state' (frontend tada čuva
svoje stanje — isti mehanizam kao practice).
"""
import logging
import uuid

from matbot import config, prompts
from matbot.llm import LLMError, failure_diagnostics_kv
from matbot.mathcheck import find_numeric_inconsistencies
from matbot.mathsafe import sanitize_and_validate_math_text
from matbot.practice import SAFE_ERROR_MESSAGE
from matbot.schema import InvalidOutputError, validate_explain_output
from matbot.terminology import normalize_terminology
from matbot.topics import lesson_info

logger = logging.getLogger("matbot.explain")

MAX_HISTORY_MESSAGES = 6  # 3 razmjene (učenik + tutor)


def _clean_history(raw_history):
    """Frontend šalje [{'role','content'}, ...]. Zadrži samo validne stavke,
    isjeci sadržaj i uzmi najviše zadnje 3 razmjene. Klijentski sadržaj se
    tretira kao nepouzdan tekst (ide samo u prompt, nikad u stanje)."""
    if not isinstance(raw_history, list):
        return []
    cleaned = []
    for item in raw_history:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str) or not content.strip():
            continue
        cleaned.append({"role": role, "content": content.strip()[:400]})
    return cleaned[-MAX_HISTORY_MESSAGES:]


def run_explain_turn(llm, turn):
    """turn: očišćeni dict iz api.py. Vraća JSON-spreman dict."""
    request_id = uuid.uuid4().hex[:12]

    # Canonical podaci iz topics.json — klijentskom selected_oblast se NE
    # vjeruje (ista politika kao practice).
    lesson = lesson_info(turn["grade"], turn["selected_topic"])
    lesson_id = lesson["id"] if lesson else (turn["selected_topic"] or "")
    lesson_title = lesson["title"] if lesson else ""
    oblast = lesson["oblast"] if lesson else (turn["selected_oblast"] or "")

    instructions = prompts.build_explain_instructions(
        turn["grade"], lesson_title=lesson_title, oblast=oblast
    )
    input_text = prompts.build_explain_input(
        lesson_title=lesson_title,
        oblast=oblast,
        history=_clean_history(turn.get("conversation_history")),
        student_message=turn["student_message"],
        interaction_phase=turn["interaction_phase"],
        last_tutor_message=turn.get("last_tutor_message", ""),
    )

    try:
        result = llm.explain_turn(instructions, input_text)
        validate_explain_output(result.output)
    except LLMError as e:
        logger.warning(
            "explain_turn request_id=%s category=%s mode=explain %s",
            request_id, e.category, failure_diagnostics_kv(e),
        )
        return {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": ""}
    except InvalidOutputError as e:
        logger.warning("explain_turn request_id=%s category=invalid_output detail=%s", request_id, e)
        return {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": ""}
    except Exception:
        logger.exception("explain_turn request_id=%s unexpected_error", request_id)
        return {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": ""}

    # Isti centralni safety boundary kao Practice (matbot/mathsafe.py) —
    # allow_whole_expression_wrap ostaje False: Explain je proza, nikad se ne
    # umata cio odgovor u $...$, samo se pokušava usko/sigurno popraviti
    # (izolovan \frac{a}{b}, doslovni "\n"). Ako i dalje nebezbjedno nakon
    # toga → odbij CIO odgovor, isti sigurni fallback kao za LLMError/
    # InvalidOutputError iznad, bez drugog AI poziva.
    answer, is_safe = sanitize_and_validate_math_text(result.output.reply.strip())
    if not is_safe:
        logger.warning("explain_turn request_id=%s category=unsafe_math_output", request_id)
        return {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": ""}
    answer = normalize_terminology(answer)

    # Numerička dosljednost lanca jednakosti (matbot/mathcheck.py) — živi nalaz
    # je bio baš u Explain modu ($\frac{3\cdot16\sqrt{3}}{2}=48\sqrt{3}$).
    # Bez drugog AI poziva: dokazana nedosljednost → isti sigurni fallback.
    numeric_issues = find_numeric_inconsistencies(answer)
    if numeric_issues:
        logger.warning("explain_turn request_id=%s category=invalid_output detail=%s",
                       request_id, numeric_issues[0])
        return {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": ""}

    logger.info(
        "explain_turn request_id=%s ok latency_ms=%s usage=%s",
        request_id, result.latency_ms, result.usage,
    )

    return {
        "status": "ready",
        "answer": answer,
        "answer_verdict": None,     # Explain NIKAD ne ocjenjuje
        "last_tutor_task": "",      # Explain NIKAD nema aktivni zadatak
        "next_state": {},           # bez Practice stanja (frontend ovo bezbjedno guta)
        "session_mode": "explain",
        "effective_topic": lesson_id or "",
    }
