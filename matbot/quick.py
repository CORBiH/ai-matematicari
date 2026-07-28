"""Orkestracija JEDNOG Quick turna („Samo rezultat“): prompt → jedan AI poziv →
validacija → sanitizacija → response u frontend ugovoru.

Namjerno STATELESS na serveru, kao Explain: Quick nema aktivni zadatak, hint
nivo, streak, opcije ni session store. Kontekst je najviše 3 kratke prethodne
razmjene koje frontend već šalje. Greška AI poziva vraća kratku sigurnu poruku
BEZ 'status' i BEZ 'next_state' (frontend tada čuva svoje stanje — isti
mehanizam kao practice/explain).
"""
import logging
import uuid

from matbot import prompts
from matbot.llm import LLMError
from matbot.mathsafe import sanitize_and_validate_math_text
from matbot.practice import SAFE_ERROR_MESSAGE
from matbot.schema import InvalidOutputError, validate_quick_output
from matbot.topics import lesson_info

logger = logging.getLogger("matbot.quick")

MAX_HISTORY_MESSAGES = 6  # 3 razmjene (učenik + tutor) — isto ograničenje kao Explain


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


def run_quick_turn(llm, turn):
    """turn: očišćeni dict iz api.py. Vraća JSON-spreman dict."""
    request_id = uuid.uuid4().hex[:12]

    # Canonical podaci iz topics.json — samo kao mekan kontekst (ne ograničenje).
    lesson = lesson_info(turn["grade"], turn["selected_topic"])
    lesson_id = lesson["id"] if lesson else (turn["selected_topic"] or "")
    lesson_title = lesson["title"] if lesson else ""
    oblast = lesson["oblast"] if lesson else (turn["selected_oblast"] or "")

    instructions = prompts.build_quick_instructions(
        turn["grade"], lesson_title=lesson_title, oblast=oblast
    )
    input_text = prompts.build_quick_input(
        lesson_title=lesson_title,
        oblast=oblast,
        history=_clean_history(turn.get("conversation_history")),
        student_message=turn["student_message"],
    )

    try:
        result = llm.quick_turn(instructions, input_text)
        validate_quick_output(result.output)
    except LLMError as e:
        logger.warning("quick_turn request_id=%s category=%s", request_id, e.category)
        return {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": ""}
    except InvalidOutputError as e:
        logger.warning("quick_turn request_id=%s category=invalid_output detail=%s", request_id, e)
        return {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": ""}
    except Exception:
        logger.exception("quick_turn request_id=%s unexpected_error", request_id)
        return {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": ""}

    # Isti centralni safety boundary kao Practice/Explain (matbot/mathsafe.py)
    # — allow_whole_expression_wrap ostaje False: Quick je i dalje kratka
    # proza, ne cio-odgovor-u-$...$. Nebezbjedno nakon uskog repaira → odbij
    # cio odgovor, isti sigurni fallback kao za LLMError/InvalidOutputError
    # iznad, bez drugog AI poziva.
    answer, is_safe = sanitize_and_validate_math_text(result.output.reply.strip())
    if not is_safe:
        logger.warning("quick_turn request_id=%s category=unsafe_math_output", request_id)
        return {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": ""}

    logger.info(
        "quick_turn request_id=%s ok latency_ms=%s usage=%s",
        request_id, result.latency_ms, result.usage,
    )

    return {
        "status": "ready",
        "answer": answer,
        "answer_verdict": None,     # Quick NIKAD ne ocjenjuje
        "last_tutor_task": "",      # Quick NIKAD nema aktivni zadatak
        "next_state": {},           # bez Practice stanja (frontend ovo bezbjedno guta)
        "session_mode": "quick",
        "effective_topic": lesson_id or "",
    }
