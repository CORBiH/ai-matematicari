"""Phase 3 — orkestracija za POST /api/ai-tutor/chat.

Spaja lanac: request payload → Phase 1 ``get_final_topic`` → Phase 2
``build_tutor_prompt`` → (samo za ``ready``) postojeći OpenAI poziv → strukturiran
JSON odgovor.

Modul je **čist i deterministički** za dati ``openai_chat`` callable: ne uvozi
``app.py`` (nema cikličnog importa), Flask ruta ubacuje ``app._openai_chat`` kao
zavisnost. Za ne-``ready`` statuse (``fallback``/``ambiguous``/``invalid``) OpenAI
se NE zove — vraća se determinističan bosanski fallback tekst.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from matbot.activity_log import log_student_activity
from matbot.content_loader import (
    get_master,
    get_thinkific_map,
    normalize_grade,
    normalize_value,
)
from matbot.prompt_builder import (
    build_exam_oblast_prompt,
    build_general_tutor_prompt,
    build_tutor_prompt,
    get_topic_context,
)
from matbot.topic_detector import detect_topic, is_vague_message
from matbot.topic_lookup import get_final_topic

log = logging.getLogger("matbot.ai_tutor")

DEFAULT_GRADE = 6
DEFAULT_MODEL = "gpt-5-mini"

# --- Phase 6: sigurnosni limiti ulaza (bez lomljenja normalne upotrebe) ----------
MAX_MESSAGE_CHARS = 4000
MAX_HISTORY_ITEMS = 5
MAX_HISTORY_ITEM_CHARS = 1500
MAX_LAST_TASK_CHARS = 1000

# max_tokens po modu (app._openai_chat podržava max_tokens parametar).
# Phase 1 (audit): quick 250→400 — reasoning modeli troše dio budžeta na
# razmišljanje, pa je 250 znao dati prazan/odsječen odgovor.
_MAX_TOKENS = {"quick": 400, "explain": 700, "practice": 700, "exam": 900}
# Retry budžet kad je odgovor prazan/odsječen (finish_reason == "length").
_RETRY_MAX_TOKENS_CAP = 1400

# Student-facing poruka kada model ni nakon retry-a ne vrati tekst.
_EMPTY_ANSWER_FALLBACK = (
    "Nisam uspio sastaviti odgovor. Pokušaj ponovo za koji trenutak ili "
    "preformuliši pitanje."
)

# recommended_mode: jednostavno mapiranje trenutnog moda u preporučeni sljedeći.
_RECOMMENDED_MODE = {
    "explain": "practice",
    "practice": "practice",
    "exam": "exam",
    "quick": "explain",
}

# Ako lookup nema poruku, koristi ovaj generički student-facing fallback (bosanski).
_DEFAULT_FALLBACK_ANSWER = (
    "Ne mogu automatski prepoznati temu. Izaberi oblast/temu koju trenutno radiš "
    "ili pošalji zadatak, pa ću ti pomoći korak po korak."
)


def list_topics(master: dict | None = None, grade: int | str = DEFAULT_GRADE) -> dict:
    """Phase 4 — lista tema za UI dropdown (GET /api/ai-tutor/topics).

    Učitava iz Phase 1 ``get_master`` (Excel je izvor istine; ništa nije
    hardkodirano). Vraća samo READY teme (ako postoji ``status`` kolona).

    Phase 1 (audit): redoslijed = redoslijed TOPICS sheeta (nastavni redoslijed),
    NE abecedni. ``oblast_order`` nosi redoslijed oblasti kroz JSON (nizovi
    garantovano čuvaju redoslijed; ključevi objekta ne moraju)::

        {
          "grade": 6,
          "topics":  [{"oblast": ..., "topic": ..., "display_name": ...}, ...],
          "grouped": {"Skupovi": [ ... ], ...},
          "oblast_order": ["Skupovi", "N i N0 Skupovi", ...],
        }
    """
    g = normalize_grade(grade)
    master = master if master is not None else get_master(grade=g)
    rows = master.get("topics", [])

    ready = [
        r
        for r in rows
        if r.get("topic")
        and (not r.get("status") or normalize_value(r.get("status")).upper() == "READY")
    ]

    topics = [
        {
            "oblast": r.get("oblast", ""),
            "topic": r["topic"],
            "display_name": r.get("display_name") or r["topic"],
        }
        for r in ready
    ]

    grouped: dict[str, list] = {}
    oblast_order: list[str] = []
    for t in topics:
        if t["oblast"] not in grouped:
            oblast_order.append(t["oblast"])
        grouped.setdefault(t["oblast"], []).append(t)

    grades = {normalize_value(r.get("grade")) for r in ready if r.get("grade")}
    grade = int(next(iter(grades))) if len(grades) == 1 and next(iter(grades)).isdigit() else g

    return {"grade": grade, "topics": topics, "grouped": grouped, "oblast_order": oblast_order}


def _extract_answer(resp: Any) -> str:
    """Izvuci tekst iz OpenAI odgovora (isti oblik kao postojeći app: choices[0].message.content)."""
    try:
        content = resp.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        return ""
    return content if isinstance(content, str) else (content or "")


def _finish_reason(resp: Any) -> str | None:
    """finish_reason prvog choice-a, defenzivno (mockovi ga ne moraju imati)."""
    try:
        return getattr(resp.choices[0], "finish_reason", None)
    except (AttributeError, IndexError, TypeError):
        return None


def _call_model_with_retry(
    openai_chat: Callable, model: str, messages: list, timeout: float | None, mode: str
) -> str:
    """Phase 1 (audit): jedan poziv + JEDAN retry sa većim max_tokens ako je
    odgovor prazan ili odsječen (``finish_reason == "length"``). Ako i retry
    zakaže, vraća prijateljsku bosansku poruku umjesto praznog stringa.

    Prvi poziv namjerno propušta izuzetke (ruta vraća postojeći 500 odgovor);
    samo retry je zaštićen try/except-om."""
    cap = _MAX_TOKENS.get(mode, 700)
    resp = openai_chat(model, messages, timeout=timeout, max_tokens=cap)
    answer = _extract_answer(resp)
    finish = _finish_reason(resp)

    if answer.strip() and finish != "length":
        return answer

    retry_cap = min(cap * 2, _RETRY_MAX_TOKENS_CAP)
    log.warning(
        "ai_tutor: prazan/odsječen odgovor (finish_reason=%s, mode=%s, max_tokens=%s) "
        "— retry sa max_tokens=%s", finish, mode, cap, retry_cap,
    )
    try:
        retry_resp = openai_chat(model, messages, timeout=timeout, max_tokens=retry_cap)
        retry_answer = _extract_answer(retry_resp)
        if retry_answer.strip():
            return retry_answer
        log.warning(
            "ai_tutor: retry također prazan (finish_reason=%s, mode=%s)",
            _finish_reason(retry_resp), mode,
        )
    except Exception:
        log.exception("ai_tutor: retry poziv nije uspio (mode=%s)", mode)

    # odsječen ali neprazan prvi odgovor je bolji od generičke poruke
    return answer if answer.strip() else _EMPTY_ANSWER_FALLBACK


def _sanitize_payload(payload: dict) -> dict:
    """Phase 6: sigurnosni limiti ulaza — poruka, historija i last_tutor_task se
    skraćuju; historija na zadnjih MAX_HISTORY_ITEMS stavki."""
    for key in ("student_message", "message"):
        val = payload.get(key)
        if isinstance(val, str) and len(val) > MAX_MESSAGE_CHARS:
            payload[key] = val[:MAX_MESSAGE_CHARS]
    for key in ("last_tutor_task", "last_tutor_message"):
        val = payload.get(key)
        if isinstance(val, str) and len(val) > MAX_LAST_TASK_CHARS:
            payload[key] = val[:MAX_LAST_TASK_CHARS]
    hist = payload.get("conversation_history")
    if isinstance(hist, list):
        trimmed = []
        for item in hist[-MAX_HISTORY_ITEMS:]:
            if isinstance(item, dict):
                item = dict(item)
                for ck in ("content", "text", "message"):
                    cv = item.get(ck)
                    if isinstance(cv, str) and len(cv) > MAX_HISTORY_ITEM_CHARS:
                        item[ck] = cv[:MAX_HISTORY_ITEM_CHARS]
            elif isinstance(item, str) and len(item) > MAX_HISTORY_ITEM_CHARS:
                item = item[:MAX_HISTORY_ITEM_CHARS]
            trimmed.append(item)
        payload["conversation_history"] = trimmed
    else:
        payload["conversation_history"] = []
    return payload


def _oblast_list(master: dict) -> str:
    """Lista oblasti iz mastera (redoslijed sheeta, bez hardkodiranja)."""
    seen: list[str] = []
    for row in master.get("topics", []):
        o = row.get("oblast")
        if o and o not in seen:
            seen.append(o)
    return ", ".join(seen)


def _fallback_answer(lookup_result: dict, mode: str, master: dict) -> str:
    """Determinističan student-facing odgovor za ne-ready statuse (bez OpenAI-ja).

    Phase 6: mode-specifično — exam pita oblast kontrolnog (lista oblasti dolazi
    iz mastera), practice traži temu/zadatak, quick traži konkretan zadatak."""
    status = normalize_value(lookup_result.get("status")).lower()
    if status in ("ambiguous", "invalid"):
        return normalize_value(lookup_result.get("message")) or _DEFAULT_FALLBACK_ANSWER

    oblasti = _oblast_list(master)
    if mode == "exam":
        return (
            f"Iz koje oblasti je kontrolni? Na primjer: {oblasti}. "
            "Napiši mi oblast ili izaberi temu iz liste, pa ću te pripremiti."
        )
    if mode == "practice":
        return (
            f"Koju temu želiš vježbati? Izaberi temu iz liste (oblasti: {oblasti}) "
            "ili mi pošalji konkretan zadatak."
        )
    if mode == "quick":
        return "Pošalji mi konkretan zadatak (tekst zadatka), pa ću ti dati samo rezultat."
    return (
        "Napiši mi konkretno pitanje ili zadatak, ili izaberi oblast/temu iz "
        f"liste ({oblasti}), pa ću ti pomoći korak po korak."
    )


def handle_chat(
    data: dict,
    openai_chat: Callable,
    master: dict | None = None,
    tmap: dict | None = None,
    *,
    model: str = DEFAULT_MODEL,
    timeout: float | None = None,
    image_bytes: bytes | None = None,
    image_data_url: str | None = None,
    ocr_image: Callable | None = None,
    vision_model: str | None = None,
) -> dict:
    """Obradi jedan /api/ai-tutor/chat zahtjev i vrati response dict.

    ``openai_chat`` mora imati potpis ``(model, messages, timeout=...)`` i vratiti
    objekt sa ``choices[0].message.content`` (tj. postojeći ``app._openai_chat``).

    Phase 6.2 — slika zadatka: ``ocr_image`` je postojeći legacy OCR
    (``app.mathpix_ocr_to_text``, injektovan — ništa se ne piše iznova). Ako OCR
    da tekst, ide normalan tekstualni put (detekcija teme + topic prompt). Ako ne,
    slika ide Vision modelu (``image_data_url``) uz isti modularni system prompt.
    """
    payload = dict(data or {})
    # Default: grade -> 6 ako nije zadan; grade bira master/map i base prompt.
    payload["grade"] = normalize_grade(payload.get("grade") or DEFAULT_GRADE)
    _sanitize_payload(payload)

    grade = payload["grade"]
    master = master if master is not None else get_master(grade=grade)
    tmap = tmap if tmap is not None else get_thinkific_map(grade=grade)

    # --- Phase 6.2: slika zadatka — prvo pokušaj OCR (postojeći legacy Mathpix) --
    has_image = bool(image_bytes or image_data_url)
    if has_image and image_bytes is not None and ocr_image is not None:
        try:
            ocr_text, _conf = ocr_image(image_bytes)
        except Exception:
            ocr_text = None
        if ocr_text:
            payload["image_ocr_text"] = normalize_value(ocr_text)[:MAX_MESSAGE_CHARS]

    lookup_result = get_final_topic(payload, master, tmap)

    # --- Phase 7: kontrolni za CIJELU OBLAST (selected_oblast bez teme) ---------
    # Ima prednost nad free_chat detekcijom: auto-poruka "Sutra imam kontrolni..."
    # ne smije pokrenuti LLM klasifikator teme. None ako uslovi nisu ispunjeni
    # (nevalidna oblast pada na postojeći exam fallback koji pita oblast).
    exam_oblast_prompt = None
    if lookup_result["status"] == "unknown":
        exam_oblast_prompt = build_exam_oblast_prompt(payload, master)

    # --- Phase 6: free_chat detekcija teme -------------------------------------
    # Tema je opcionalna: ako lookup ne nađe ništa, a poruka (ili OCR teksta
    # slike) je KONKRETNA, pokušaj detekciju (heuristike → LLM klasifikator).
    # Detektovana tema se validira kroz get_final_topic (nikad se ne izmišlja).
    general_answer = False
    if exam_oblast_prompt is None and lookup_result["status"] == "unknown":
        student_msg = normalize_value(
            payload.get("student_message") or payload.get("message")
        )
        ocr_text = normalize_value(payload.get("image_ocr_text"))
        phase = normalize_value(payload.get("interaction_phase")).lower()
        last_task = normalize_value(payload.get("last_tutor_task"))
        is_practice_followup = phase == "answering_practice_task"
        combined_parts = (student_msg, ocr_text)
        if is_practice_followup and last_task:
            combined_parts = (last_task, student_msg, ocr_text)
        combined = " ".join(x for x in combined_parts if x)
        # Phase 7.2: kratka potvrda ("može", "nastavi") kao nastavak razgovora —
        # nikad fallback i nikad LLM klasifikator teme; opći prompt + historija.
        is_continuation = phase == "continuing_explanation"
        if is_continuation and combined:
            general_answer = True
        elif combined and (has_image or not is_vague_message(combined)):
            detection = detect_topic(
                combined, master, tmap,
                openai_chat=openai_chat, model=model, timeout=timeout,
            )
            if detection["detected_topic"] != "unknown":
                payload["detected_topic"] = detection["detected_topic"]
                lookup_result = get_final_topic(payload, master, tmap)
            else:
                # konkretno pitanje bez prepoznate teme → odgovori bez topic
                # konteksta (final_topic ostaje "unknown", ništa se ne izmišlja)
                general_answer = True
        elif has_image and not combined:
            # slika bez teksta i bez OCR-a → Vision odgovor bez teme
            general_answer = True

    if exam_oblast_prompt is not None:
        prompt_result = exam_oblast_prompt
    elif general_answer:
        prompt_result = build_general_tutor_prompt(payload)
    else:
        prompt_result = build_tutor_prompt(payload, lookup_result, master, tmap)

    mode = prompt_result["mode"]          # već normalizovan (explain|practice|exam|quick)
    status = prompt_result["status"]      # ready|fallback|ambiguous|invalid

    if status == "ready":
        # Slika bez uspješnog OCR-a → multimodalna poruka Vision modelu; inače
        # čisti tekst (OCR tekst je već u user_promptu kroz _build_student_block).
        if image_data_url and not normalize_value(payload.get("image_ocr_text")):
            user_content: Any = [
                {"type": "text", "text": prompt_result["user_prompt"]},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ]
            use_model = vision_model or model
        else:
            user_content = prompt_result["user_prompt"]
            use_model = model
        messages = [
            {"role": "system", "content": prompt_result["system_prompt"]},
            {"role": "user", "content": user_content},
        ]
        answer = _call_model_with_retry(openai_chat, use_model, messages, timeout, mode)
    else:
        # fallback/ambiguous/invalid → NE zovi OpenAI (deterministički bosanski tekst)
        answer = _fallback_answer(lookup_result, mode, master)

    effective_topic = prompt_result.get("effective_topic") or prompt_result.get(
        "final_topic", "unknown"
    )
    topic_context = get_topic_context(effective_topic, master)

    entry_source_used = normalize_value(payload.get("entry_source")) or normalize_value(
        lookup_result.get("source")
    )
    parent_report_signal = (
        "needs_work"
        if (mode in ("practice", "exam") or status == "fallback")
        else "neutral"
    )

    response = {
        "answer": answer,
        "final_topic": prompt_result.get("final_topic", "unknown"),
        "opened_lesson_topic": prompt_result.get("opened_lesson_topic", "unknown"),
        "effective_topic": effective_topic,
        "entry_source_used": entry_source_used,
        "topic_conflict": bool(prompt_result.get("topic_conflict", False)),
        "recommended_mode": _RECOMMENDED_MODE.get(mode, "practice"),
        "recommend_video": bool(topic_context.get("when_to_recommend_video")),
        "parent_report_signal": parent_report_signal,
        "status": status,
        "mode": mode,
    }

    # Phase 5: minimalni activity log (samo metapodaci — bez poruka/odgovora).
    # Greška u logovanju NIKAD ne smije srušiti tutor odgovor.
    try:
        log_student_activity(payload, response)
    except Exception:
        pass

    return response
