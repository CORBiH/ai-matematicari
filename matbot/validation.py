"""Mala, fokusirana validacija chat payloada — bez validation frameworka.

Provjerava SAMO ono što ranije nije bilo pokriveno (grade enum, mode enum,
strukturna ograničenja conversation_history, format session_id/client_turn_id,
postojanje selected_topic u topics.json). Postojeće provjere (dužina poruke,
slika, prazna poruka) ostaju gdje jesu u matbot/api.py — ne dupliramo ih ovdje.
"""
import re

from matbot import config
from matbot.topics import lesson_info

ALLOWED_GRADES = (6, 7, 8, 9)
ALLOWED_MODES = {"practice", "explain", "quick", "exam"}
ALLOWED_INTERACTION_TYPES = {"choice_answer", "student_question"}
_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")
_OPTION_ID_RE = re.compile(r"^[a-d]$")


class ValidationError(Exception):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _check_id_format(payload, key):
    val = payload.get(key)
    if val is None:
        return
    if not isinstance(val, str) or not _ID_RE.match(val):
        raise ValidationError(f"INVALID_{key.upper()}", "Zahtjev nije prepoznat. Osvježi stranicu i pokušaj ponovo.")


def _check_history(payload):
    history = payload.get("conversation_history")
    if history is None:
        return
    if not isinstance(history, list) or len(history) > config.MAX_HISTORY_ITEMS:
        raise ValidationError("HISTORY_TOO_LARGE", "Historija razgovora je prevelika. Osvježi stranicu.")
    for item in history:
        if isinstance(item, dict):
            content = item.get("content")
        elif isinstance(item, str):
            content = item
        else:
            continue
        if isinstance(content, str) and len(content) > config.MAX_HISTORY_CHARS_PER_ITEM:
            raise ValidationError("HISTORY_ITEM_TOO_LONG", "Jedna stavka historije razgovora je preduga.")


def validate_chat_payload(payload):
    """Baca ValidationError na prvi problem. Ne vraća ništa — pozivalac i
    dalje čita polja iz payloada kao i ranije (ovo je samo gejt prije toga)."""
    try:
        grade = int(payload.get("grade"))
    except (TypeError, ValueError):
        raise ValidationError("INVALID_GRADE", "Razred nije prepoznat.")
    if grade not in ALLOWED_GRADES:
        raise ValidationError("INVALID_GRADE", "Razred mora biti 6, 7, 8 ili 9.")

    mode = payload.get("mode")
    if mode is not None and mode != "" and (not isinstance(mode, str) or mode not in ALLOWED_MODES):
        raise ValidationError("INVALID_MODE", "Nepoznat način rada.")

    _check_id_format(payload, "session_id")
    _check_id_format(payload, "client_turn_id")
    _check_history(payload)

    interaction_type = payload.get("interaction_type")
    if interaction_type is not None and interaction_type != "" and interaction_type not in ALLOWED_INTERACTION_TYPES:
        raise ValidationError("INVALID_INTERACTION_TYPE", "Nepoznat tip interakcije.")
    if interaction_type == "choice_answer":
        selected_option_id = payload.get("selected_option_id")
        if not isinstance(selected_option_id, str) or not _OPTION_ID_RE.match(selected_option_id):
            raise ValidationError("INVALID_OPTION_ID", "Izbor nije prepoznat. Osvježi stranicu i pokušaj ponovo.")

    selected_topic = payload.get("selected_topic")
    if mode == "practice" and not selected_topic:
        raise ValidationError(
            "MISSING_TOPIC", "Izaberi lekciju prije pokretanja vježbe."
        )
    if selected_topic:
        if not isinstance(selected_topic, str) or not lesson_info(grade, selected_topic):
            raise ValidationError("UNKNOWN_TOPIC", "Izabrana lekcija nije prepoznata za ovaj razred.")
