"""Flask blueprint za /api/ai-tutor/* — prevod stvarnog frontend ugovora
(templates/index.html) u čisti 'turn' dict i nazad. Bez pedagogije ovdje.
"""
import json
import logging

from flask import Blueprint, Response, current_app, jsonify, request

from matbot import config
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore

logger = logging.getLogger("matbot.api")

ai_tutor_bp = Blueprint("ai_tutor", __name__, url_prefix="/api/ai-tutor")

NON_PRACTICE_MESSAGE = (
    "Ovaj način rada još nije dostupan u novoj verziji. "
    "Vrati se preko dugmeta Nazad i izaberi „Vježbaj sa mnom“ — tu sam spreman za zadatke."
)
IMAGE_NOT_SUPPORTED_MESSAGE = (
    "Slike još nisu podržane u ovoj verziji. Prepiši mi zadatak tekstom pa nastavljamo."
)
EMPTY_MESSAGE_PROMPT = "Upiši poruku ili zadatak pa pokušaj ponovo."
TOO_LONG_MESSAGE = "Poruka je preduga. Skrati je pa pošalji ponovo."


def _get_store():
    store = current_app.config.get("MATBOT_SESSION_STORE")
    if store is None:
        store = SessionStore()
        current_app.config["MATBOT_SESSION_STORE"] = store
    return store


def _get_llm():
    llm = current_app.config.get("MATBOT_LLM")
    if llm is None:
        from matbot.llm import OpenAIPracticeLLM

        llm = OpenAIPracticeLLM()
        current_app.config["MATBOT_LLM"] = llm
    return llm


def _str_field(payload, key, limit):
    val = payload.get(key)
    if not isinstance(val, str):
        return ""
    return val.strip()[:limit]


def _parse_chat_request():
    """Vraća (payload dict | None, has_image bool). Podržava JSON i multipart
    (polje 'payload' = JSON string, polje 'image' = fajl) — tačno kako frontend šalje."""
    if request.content_type and request.content_type.startswith("multipart/"):
        raw = request.form.get("payload", "")
        try:
            payload = json.loads(raw) if raw else None
        except ValueError:
            payload = None
        has_image = bool(request.files.get("image"))
        return (payload if isinstance(payload, dict) else None), has_image
    payload = request.get_json(silent=True)
    return (payload if isinstance(payload, dict) else None), False


def _build_turn(payload):
    """Očisti i ograniči SAMO polja koja server koristi. Model nikad ne vidi
    session_id/client_turn_id; klijent nikad ne diktira interna rješenja."""
    try:
        grade = int(payload.get("grade", 0))
    except (TypeError, ValueError):
        grade = 0
    return {
        "session_id": _str_field(payload, "session_id", 128) or "anon",
        "grade": grade,
        "selected_topic": _str_field(payload, "selected_topic", 64),
        "selected_oblast": _str_field(payload, "selected_oblast", 120),
        "student_message": _str_field(payload, "student_message", config.MAX_MESSAGE_CHARS),
        "intent": _str_field(payload, "intent", 40),
        "difficulty_request": _str_field(payload, "difficulty_request", 20),
        "interaction_phase": _str_field(payload, "interaction_phase", 40),
        "last_tutor_task": _str_field(payload, "last_tutor_task", config.MAX_TASK_CHARS),
    }


def _simple_response(answer, mode):
    """Kontrolisan odgovor bez AI poziva i bez promjene stanja."""
    return {
        "status": "ready",
        "answer": answer,
        "answer_verdict": None,
        "last_tutor_task": "",
        "next_state": {"v": 1, "correct_streak": 0, "hint_level": 0},
        "session_mode": mode,
        "effective_topic": "",
    }


def _chat_turn():
    """Zajednička logika za /chat i /chat/stream. Uvijek vraća JSON-spreman dict."""
    payload, has_image = _parse_chat_request()
    if payload is None:
        return {"answer": EMPTY_MESSAGE_PROMPT, "last_tutor_task": ""}

    mode = _str_field(payload, "mode", 20) or "practice"
    raw_message = payload.get("student_message")
    if isinstance(raw_message, str) and len(raw_message) > config.MAX_MESSAGE_CHARS:
        return {"answer": TOO_LONG_MESSAGE, "last_tutor_task": ""}

    if has_image:
        return {"answer": IMAGE_NOT_SUPPORTED_MESSAGE, "last_tutor_task": ""}

    if mode != "practice":
        return _simple_response(NON_PRACTICE_MESSAGE, mode)

    turn = _build_turn(payload)
    if not turn["student_message"]:
        return {"answer": EMPTY_MESSAGE_PROMPT, "last_tutor_task": ""}

    try:
        return run_practice_turn(_get_store(), _get_llm(), turn)
    except Exception:
        # Zadnja linija odbrane: interni exception NIKAD ne ide učeniku.
        logger.exception("chat_turn unexpected_error")
        return {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": ""}


@ai_tutor_bp.route("/chat", methods=["POST"])
def chat():
    return jsonify(_chat_turn())


@ai_tutor_bp.route("/chat/stream", methods=["POST"])
def chat_stream():
    """Faza 1: bez token-po-token delta događaja — jedan AI poziv pa jedan
    kompletan 'done' event u SSE formatu koji frontend već parsira."""
    result = _chat_turn()
    body = "event: done\ndata: " + json.dumps(result, ensure_ascii=False) + "\n\n"
    return Response(body, mimetype="text/event-stream")


@ai_tutor_bp.route("/feedback", methods=["POST"])
def feedback():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False}), 400
    session_id = payload.get("session_id")
    message_index = payload.get("message_index")
    verdict = payload.get("verdict")
    mode = payload.get("mode")
    topic = payload.get("topic")
    valid = (
        isinstance(session_id, str) and 0 < len(session_id) <= 128
        and isinstance(message_index, int) and message_index >= 0
        and verdict in ("up", "down")
        and isinstance(mode, str) and len(mode) <= 20
        and isinstance(topic, str) and len(topic) <= 64
    )
    if not valid:
        return jsonify({"ok": False}), 400
    # Faza 1: bez trajnog spremanja — samo validacija i potvrda.
    return jsonify({"ok": True})
