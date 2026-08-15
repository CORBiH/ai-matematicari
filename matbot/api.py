"""Flask blueprint za /api/ai-tutor/* — prevod stvarnog frontend ugovora
(templates/index.html) u čisti 'turn' dict i nazad. Bez pedagogije ovdje.

Sigurnosni gejt (token → IP rate limit → validacija → session rate limit →
per-session lock) postoji SAMO na endpointima koji troše OpenAI kredit ili
mijenjaju stanje: /chat, /chat/stream, /feedback. GET /topics ostaje javan —
ne troši OpenAI kredit i sadrži samo javne podatke o kurikulumu (obrazloženje
u završnom izvještaju security hardeninga).
"""
import json
import logging
import time

from flask import Blueprint, Response, current_app, jsonify, request

from matbot import auth, config, imageinput, kontrolni, validation
from matbot.explain import run_explain_turn
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.quick import run_quick_turn
from matbot.ratelimit import RateLimiter
from matbot.session_store import SessionStore
from matbot.turnlock import TurnLockRegistry

logger = logging.getLogger("matbot.api")

ai_tutor_bp = Blueprint("ai_tutor", __name__, url_prefix="/api/ai-tutor")

NON_PRACTICE_MESSAGE = (
    "Ovaj način rada još nije dostupan u novoj verziji. "
    "Vrati se preko dugmeta Nazad i izaberi „Vježbaj sa mnom“ — tu sam spreman za zadatke."
)
IMAGE_NOT_SUPPORTED_MESSAGE = (
    "Slike za sada radim samo u modu „Samo rezultat“. "
    "Prebaci se na taj mod i pošalji sliku ponovo, ili mi zadatak prepiši tekstom."
)
REQUEST_TOO_LARGE_MESSAGE = (
    "Poslani zahtjev je prevelik. Pošalji manju sliku (do 8 MB) ili kraću poruku."
)
EMPTY_MESSAGE_PROMPT = "Upiši poruku ili zadatak pa pokušaj ponovo."
TOO_LONG_MESSAGE = "Poruka je preduga. Skrati je pa pošalji ponovo."
AUTH_FRIENDLY_MESSAGE = "Sesija je istekla ili nije prepoznata. Osvježi stranicu i pokušaj ponovo."
RATE_LIMIT_MESSAGE = "Poslao si previše poruka u kratkom periodu. Sačekaj malo pa pokušaj ponovo."
TURN_IN_PROGRESS_MESSAGE = "Prethodni zahtjev za ovaj razgovor još nije završen. Sačekaj trenutak."


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


def _get_session_limiter():
    limiter = current_app.config.get("MATBOT_SESSION_LIMITER")
    if limiter is None:
        limiter = RateLimiter(config.SESSION_LIMIT_PER_MINUTE, config.SESSION_LIMIT_PER_HOUR)
        current_app.config["MATBOT_SESSION_LIMITER"] = limiter
    return limiter


def _get_ip_limiter():
    limiter = current_app.config.get("MATBOT_IP_LIMITER")
    if limiter is None:
        limiter = RateLimiter(config.IP_LIMIT_PER_MINUTE, config.IP_LIMIT_PER_HOUR)
        current_app.config["MATBOT_IP_LIMITER"] = limiter
    return limiter


def _get_turn_locks():
    locks = current_app.config.get("MATBOT_TURN_LOCKS")
    if locks is None:
        locks = TurnLockRegistry()
        current_app.config["MATBOT_TURN_LOCKS"] = locks
    return locks


def _client_ip():
    # Produkcijski VPS potvrđen: Nginx je JEDINI reverse proxy ispred ove
    # aplikacije i postavlja X-Forwarded-For. app.py omotava app.wsgi_app u
    # ProxyFix(x_for=1, x_proto=1) (vidi app.py), koji PRIJE nego što zahtjev
    # uopšte stigne ovdje prepisuje request.remote_addr u stvarnu klijentsku
    # adresu (uzima tačno JEDNU, najdesniju vrijednost iz X-Forwarded-For —
    # onu koju je dodao nginx, ne ono što je klijent sam ubacio). Zato je
    # request.remote_addr ovdje i dalje ispravan izvor, bez direktnog čitanja
    # headera i bez rizika da klijent lažira dodatne hopove.
    return request.remote_addr or "unknown"


def _auth_error():
    return {"error": "AUTH_REQUIRED", "detail": AUTH_FRIENDLY_MESSAGE}


def _rate_limit_error(retry_after):
    return {"error": "RATE_LIMITED", "detail": RATE_LIMIT_MESSAGE, "retry_after": retry_after}


def _str_field(payload, key, limit):
    val = payload.get(key)
    if not isinstance(val, str):
        return ""
    return val.strip()[:limit]


def _parse_chat_request():
    """Vraća (payload dict | None, file_count int). Podržava JSON i multipart
    (polje 'payload' = JSON string, polje 'image' = fajl) — tačno kako frontend šalje.

    `file_count` broji SVA file polja u zahtjevu, ne samo `image`: napadač
    smije poslati proizvoljna imena polja, pa je jedini ispravan test „koliko
    fajlova je uopšte stiglo“. Tijelo je već ograničeno na
    config.MAX_REQUEST_BYTES (Flask MAX_CONTENT_LENGTH) i parsira se isključivo
    u memoriju (matbot/request_limits.py) — ovdje se NIŠTA ne dekodira."""
    if request.content_type and request.content_type.startswith("multipart/"):
        raw = request.form.get("payload", "")
        try:
            payload = json.loads(raw) if raw else None
        except ValueError:
            payload = None
        file_count = imageinput.count_uploaded_files(request.files)
        return (payload if isinstance(payload, dict) else None), file_count
    payload = request.get_json(silent=True)
    return (payload if isinstance(payload, dict) else None), 0


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
        "interaction_type": _str_field(payload, "interaction_type", 20),
        "selected_option_id": _str_field(payload, "selected_option_id", 4),
        "client_turn_id": _str_field(payload, "client_turn_id", 128),
        # Explain-only kontekst (practice ih ignoriše): historija je već
        # strukturno ograničena u validation.validate_chat_payload.
        "last_tutor_message": _str_field(payload, "last_tutor_message", 600),
        "conversation_history": payload.get("conversation_history"),
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


def _guarded_chat_turn():
    """Zajednička logika za /chat i /chat/stream. Vraća (status_code, dict).

    Redoslijed provjera (najjeftinije prvo; slika se DEKODIRA ZADNJA):
      0) MAX_CONTENT_LENGTH na HTTP nivou (app.py)                  → 413
      1) token (header — jedina provjera koja NE dira tijelo)       → 401
      2) IP rate limit (širi, flood-zaštita cijelog endpointa)      → 429
      3) parsiranje payloada (bounded, in-memory) + meke provjere
      4) strukturna validacija (grade/mode/history/id-jevi/topic)   → 400
      5) session rate limit (SAMO za stvarne AI turnove)            → 429
      6) per-session concurrency lock (isti AI turnovi)             → 409
      7) validacija + normalizacija slike (Pillow)                  → 400/413
      8) run_practice_turn / run_explain_turn / run_quick_turn — TAČNO JEDAN
         poziv modela (uvijek finally otpušta lock)

    Tačka 7 je namjerno POSLIJE 5 i 6: neautorizovan, prigušen ili paralelan
    zahtjev nikad ne troši CPU na dekodiranje slike. Token provjera (1) ostaje
    ispred parsiranja tijela (3) jer čita samo header — time neautentikovan
    zahtjev ne plati ni parsiranje, a garancija „ništa se ne dekodira prije
    rate limita“ ostaje netaknuta.
    """
    token = request.headers.get(auth.TOKEN_HEADER, "")
    try:
        auth.verify_token(token)
    except auth.TokenError as e:
        logger.info("auth_failed category=%s", e.code)
        return 401, _auth_error()

    ip_allowed, ip_retry_after = _get_ip_limiter().check("ip:" + _client_ip())
    if not ip_allowed:
        logger.info("rate_limited bucket=ip retry_after=%s", ip_retry_after)
        return 429, _rate_limit_error(ip_retry_after)

    payload, file_count = _parse_chat_request()
    if payload is None:
        return 200, {"answer": EMPTY_MESSAGE_PROMPT, "last_tutor_task": ""}

    mode = _str_field(payload, "mode", 20) or "practice"
    raw_message = payload.get("student_message")
    if isinstance(raw_message, str) and len(raw_message) > config.MAX_MESSAGE_CHARS:
        return 200, {"answer": TOO_LONG_MESSAGE, "last_tutor_task": ""}

    # Slika je podržana ISKLJUČIVO u modu „Samo rezultat“ (quick). Vježbaj sa
    # mnom i Objasni mi ostaju nepromijenjeni — kontrolisana poruka, bez
    # dekodiranja slike i bez ijednog poziva modela.
    if file_count and mode != "quick":
        return 200, {"answer": IMAGE_NOT_SUPPORTED_MESSAGE, "last_tutor_task": ""}

    try:
        validation.validate_chat_payload(payload)
    except validation.ValidationError as e:
        logger.info("validation_failed code=%s", e.code)
        return 400, {"error": e.code, "detail": e.detail}

    if mode not in ("practice", "explain", "quick"):
        return 200, _simple_response(NON_PRACTICE_MESSAGE, mode)

    turn = _build_turn(payload)
    # Prazna poruka je dozvoljena SAMO usko: mod „Samo rezultat“ + tačno jedan
    # priložen fajl. Tada je slika sam zadatak, a serversku podrazumijevanu
    # instrukciju sastavlja Quick tok (matbot/quick.py) — ne klijent i ne
    # prikazuje se kao da ju je učenik napisao. Tekstualni zahtjev bez poruke
    # ostaje odbijen kao i do sada.
    image_only_allowed = (mode == "quick" and file_count == 1)
    if not turn["student_message"] and not image_only_allowed:
        return 200, {"answer": EMPTY_MESSAGE_PROMPT, "last_tutor_task": ""}

    # Od ovdje nadalje je stvarni (skupi) AI turn (practice ILI explain) —
    # session limit i concurrency lock štite baš ovaj put, ne canned odgovore.
    session_id = turn["session_id"]
    sess_allowed, sess_retry_after = _get_session_limiter().check("sess:" + session_id)
    if not sess_allowed:
        logger.info("rate_limited bucket=session retry_after=%s", sess_retry_after)
        return 429, _rate_limit_error(sess_retry_after)

    turn_locks = _get_turn_locks()
    if not turn_locks.try_acquire(session_id):
        logger.info("turn_in_progress")
        return 409, {"error": "TURN_IN_PROGRESS", "detail": TURN_IN_PROGRESS_MESSAGE}

    try:
        # Tek OVDJE (iza auth-a, oba rate limita i locka) trošimo CPU na
        # dekodiranje slike. Odbijen upload → 400/413 i NULA poziva modela.
        image = None
        if file_count:
            try:
                storage = imageinput.extract_single_image(request.files)
                image = imageinput.validate_image_upload(storage)
            except imageinput.ImageRejected as e:
                logger.info("image_rejected category=%s detail=%s", e.category, e.detail)
                return e.http_status, {"error": "IMAGE_REJECTED", "detail": e.message}

        if mode == "explain":
            return 200, run_explain_turn(_get_llm(), turn)
        if mode == "quick":
            return 200, run_quick_turn(_get_llm(), turn, image=image)
        return 200, run_practice_turn(_get_store(), _get_llm(), turn)
    except Exception:
        # Zadnja linija odbrane: interni exception NIKAD ne ide učeniku.
        logger.exception("chat_turn unexpected_error")
        return 200, {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": ""}
    finally:
        turn_locks.release(session_id)


@ai_tutor_bp.route("/chat", methods=["POST"])
def chat():
    # Faza 4H: ukupno HTTP vrijeme zahtjeva — samo status i milisekunde,
    # nikad sadržaj. Razlika (chat_request_timing − tutor_turn_diagnostics
    # total_ms) je mrežno/serijalizacijsko/guard vrijeme ovog sloja.
    started = time.perf_counter()
    status_code, result = _guarded_chat_turn()
    response = jsonify(result)
    if status_code == 429 and "retry_after" in result:
        response.headers["Retry-After"] = str(result["retry_after"])
    logger.info("chat_request_timing endpoint=chat status=%s total_ms=%s",
                status_code, int((time.perf_counter() - started) * 1000))
    return response, status_code


@ai_tutor_bp.route("/chat/stream", methods=["POST"])
def chat_stream():
    """Faza 1: bez token-po-token delta događaja — jedan AI poziv pa jedan
    kompletan 'done' event u SSE formatu koji frontend već parsira.

    Bezbjednosne provjere (token/rate-limit/validacija/lock) NIKAD ne smiju
    proći kao SSE 200 — na blokadi vraćamo običan JSON sa pravim HTTP statusom,
    a frontend to prepoznaje po content-type/status i sam pada nazad na
    /chat (isti guard tamo ponovo blokira na isti način, sigurno)."""
    started = time.perf_counter()
    status_code, result = _guarded_chat_turn()
    if status_code != 200:
        response = jsonify(result)
        if status_code == 429 and "retry_after" in result:
            response.headers["Retry-After"] = str(result["retry_after"])
        return response, status_code
    body = "event: done\ndata: " + json.dumps(result, ensure_ascii=False) + "\n\n"
    logger.info("chat_request_timing endpoint=stream status=200 total_ms=%s",
                int((time.perf_counter() - started) * 1000))
    return Response(body, mimetype="text/event-stream")


def _get_exam_store():
    store = current_app.config.get("MATBOT_EXAM_STORE")
    if store is None:
        store = kontrolni.KontrolniStore()
        current_app.config["MATBOT_EXAM_STORE"] = store
    return store


@ai_tutor_bp.route("/exam/start", methods=["POST"])
def exam_start():
    """„Sutra imam kontrolni“: generisanje testa od 5 MCQ pitanja.

    ISTI guard lanac kao /chat (token → IP limit → validacija → session limit →
    per-session lock) jer ovaj endpoint troši do DVA poziva modela. Ključ
    odgovora ostaje na serveru — odgovor nosi samo tekst i opcije."""
    started = time.perf_counter()
    token = request.headers.get(auth.TOKEN_HEADER, "")
    try:
        auth.verify_token(token)
    except auth.TokenError as e:
        logger.info("auth_failed category=%s endpoint=exam_start", e.code)
        return jsonify(_auth_error()), 401

    ip_allowed, ip_retry_after = _get_ip_limiter().check("ip:" + _client_ip())
    if not ip_allowed:
        logger.info("rate_limited bucket=ip endpoint=exam_start retry_after=%s", ip_retry_after)
        response = jsonify(_rate_limit_error(ip_retry_after))
        response.headers["Retry-After"] = str(ip_retry_after)
        return response, 429

    payload = request.get_json(silent=True)
    try:
        session_id, _grade, _oblast, _relative = kontrolni.validate_start_payload(payload)
    except kontrolni.ExamValidationError as e:
        logger.info("validation_failed endpoint=exam_start code=%s", e.code)
        return jsonify({"error": e.code, "detail": e.detail}), 400

    sess_allowed, sess_retry_after = _get_session_limiter().check("sess:" + session_id)
    if not sess_allowed:
        logger.info("rate_limited bucket=session endpoint=exam_start retry_after=%s",
                    sess_retry_after)
        response = jsonify(_rate_limit_error(sess_retry_after))
        response.headers["Retry-After"] = str(sess_retry_after)
        return response, 429

    turn_locks = _get_turn_locks()
    if not turn_locks.try_acquire(session_id):
        logger.info("turn_in_progress endpoint=exam_start")
        return jsonify({"error": "TURN_IN_PROGRESS",
                        "detail": TURN_IN_PROGRESS_MESSAGE}), 409
    try:
        status_code, result = kontrolni.run_start(_get_exam_store(), _get_llm(), payload)
    except kontrolni.ExamValidationError as e:
        logger.info("validation_failed endpoint=exam_start code=%s", e.code)
        status_code, result = 400, {"error": e.code, "detail": e.detail}
    except Exception:
        # Ista posljednja odbrana kao /chat: interni izuzetak NIKAD učeniku.
        logger.exception("exam_start unexpected_error")
        status_code, result = 200, {"status": "failed",
                                    "message": kontrolni.GENERATION_FAILED_MESSAGE}
    finally:
        turn_locks.release(session_id)
    logger.info("chat_request_timing endpoint=exam_start status=%s total_ms=%s",
                status_code, int((time.perf_counter() - started) * 1000))
    return jsonify(result), status_code


@ai_tutor_bp.route("/exam/submit", methods=["POST"])
def exam_submit():
    """Serversko ocjenjivanje predanog testa — NULA poziva modela.

    Guard: token + IP limit (kao /feedback). Session limiter se namjerno NE
    troši: predaja ne pravi AI turn, a učenik ne smije ostati bez kredita za
    sljedeći test zato što je predao ovaj."""
    token = request.headers.get(auth.TOKEN_HEADER, "")
    try:
        auth.verify_token(token)
    except auth.TokenError as e:
        logger.info("auth_failed category=%s endpoint=exam_submit", e.code)
        return jsonify(_auth_error()), 401

    ip_allowed, ip_retry_after = _get_ip_limiter().check("ip:" + _client_ip())
    if not ip_allowed:
        logger.info("rate_limited bucket=ip endpoint=exam_submit retry_after=%s", ip_retry_after)
        response = jsonify(_rate_limit_error(ip_retry_after))
        response.headers["Retry-After"] = str(ip_retry_after)
        return response, 429

    payload = request.get_json(silent=True)
    try:
        status_code, result = kontrolni.run_submit(_get_exam_store(), payload)
    except kontrolni.ExamValidationError as e:
        logger.info("validation_failed endpoint=exam_submit code=%s", e.code)
        status_code, result = 400, {"error": e.code, "detail": e.detail}
    except Exception:
        logger.exception("exam_submit unexpected_error")
        status_code, result = 200, {"status": "failed",
                                    "message": kontrolni.GENERATION_FAILED_MESSAGE}
    return jsonify(result), status_code


@ai_tutor_bp.route("/feedback", methods=["POST"])
def feedback():
    # Feedback ne zove OpenAI, pa dobija samo token provjeru (bez posebnog
    # rate limita) — najmanja dovoljna zaštita za ovaj endpoint.
    try:
        auth.verify_token(request.headers.get(auth.TOKEN_HEADER, ""))
    except auth.TokenError as e:
        logger.info("auth_failed category=%s endpoint=feedback", e.code)
        return jsonify(_auth_error()), 401

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
