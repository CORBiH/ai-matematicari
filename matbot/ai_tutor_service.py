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

from typing import Any, Callable

from matbot.activity_log import log_student_activity
from matbot.content_loader import get_master, get_thinkific_map, normalize_value
from matbot.prompt_builder import (
    build_general_tutor_prompt,
    build_tutor_prompt,
    get_topic_context,
)
from matbot.topic_detector import detect_topic, is_vague_message
from matbot.topic_lookup import get_final_topic

DEFAULT_GRADE = 6
DEFAULT_MODEL = "gpt-5-mini"

# --- Phase 6: sigurnosni limiti ulaza (bez lomljenja normalne upotrebe) ----------
MAX_MESSAGE_CHARS = 4000
MAX_HISTORY_ITEMS = 5
MAX_HISTORY_ITEM_CHARS = 1500
MAX_LAST_TASK_CHARS = 1000

# max_tokens po modu (app._openai_chat podržava max_tokens parametar)
_MAX_TOKENS = {"quick": 250, "explain": 700, "practice": 700, "exam": 900}

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


def list_topics(master: dict | None = None) -> dict:
    """Phase 4 — lista tema za UI dropdown (GET /api/ai-tutor/topics).

    Učitava iz Phase 1 ``get_master`` (Excel je izvor istine; ništa nije
    hardkodirano). Vraća samo READY teme (ako postoji ``status`` kolona),
    sortirano i grupisano po oblasti::

        {
          "grade": 6,
          "topics":  [{"oblast": ..., "topic": ..., "display_name": ...}, ...],
          "grouped": {"Skupovi": [ ... ], ...},
        }
    """
    master = master if master is not None else get_master()
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
    topics.sort(key=lambda t: (t["oblast"], t["display_name"]))

    grouped: dict[str, list] = {}
    for t in topics:
        grouped.setdefault(t["oblast"], []).append(t)

    grades = {normalize_value(r.get("grade")) for r in ready if r.get("grade")}
    grade = int(next(iter(grades))) if len(grades) == 1 and next(iter(grades)).isdigit() else DEFAULT_GRADE

    return {"grade": grade, "topics": topics, "grouped": grouped}


def _extract_answer(resp: Any) -> str:
    """Izvuci tekst iz OpenAI odgovora (isti oblik kao postojeći app: choices[0].message.content)."""
    try:
        content = resp.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        return ""
    return content if isinstance(content, str) else (content or "")


def _sanitize_payload(payload: dict) -> dict:
    """Phase 6: sigurnosni limiti ulaza — poruka, historija i last_tutor_task se
    skraćuju; historija na zadnjih MAX_HISTORY_ITEMS stavki."""
    for key in ("student_message", "message"):
        val = payload.get(key)
        if isinstance(val, str) and len(val) > MAX_MESSAGE_CHARS:
            payload[key] = val[:MAX_MESSAGE_CHARS]
    val = payload.get("last_tutor_task")
    if isinstance(val, str) and len(val) > MAX_LAST_TASK_CHARS:
        payload["last_tutor_task"] = val[:MAX_LAST_TASK_CHARS]
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
) -> dict:
    """Obradi jedan /api/ai-tutor/chat zahtjev i vrati response dict.

    ``openai_chat`` mora imati potpis ``(model, messages, timeout=...)`` i vratiti
    objekt sa ``choices[0].message.content`` (tj. postojeći ``app._openai_chat``).
    """
    payload = dict(data or {})
    # Default: grade → 6 ako nije zadan (utiče na base prompt); mode default rješava builder.
    if not normalize_value(payload.get("grade")):
        payload["grade"] = DEFAULT_GRADE
    _sanitize_payload(payload)

    master = master if master is not None else get_master()
    tmap = tmap if tmap is not None else get_thinkific_map()

    lookup_result = get_final_topic(payload, master, tmap)

    # --- Phase 6: free_chat detekcija teme -------------------------------------
    # Tema je opcionalna: ako lookup ne nađe ništa, a poruka je KONKRETNA,
    # pokušaj detekciju (heuristike → LLM klasifikator). Detektovana tema se
    # validira kroz postojeći get_final_topic (nikad se ne izmišlja).
    general_answer = False
    if lookup_result["status"] == "unknown":
        student_msg = normalize_value(
            payload.get("student_message") or payload.get("message")
        )
        if student_msg and not is_vague_message(student_msg):
            detection = detect_topic(
                student_msg, master, tmap,
                openai_chat=openai_chat, model=model, timeout=timeout,
            )
            if detection["detected_topic"] != "unknown":
                payload["detected_topic"] = detection["detected_topic"]
                lookup_result = get_final_topic(payload, master, tmap)
            else:
                # konkretno pitanje bez prepoznate teme → odgovori bez topic
                # konteksta (final_topic ostaje "unknown", ništa se ne izmišlja)
                general_answer = True

    if general_answer:
        prompt_result = build_general_tutor_prompt(payload)
    else:
        prompt_result = build_tutor_prompt(payload, lookup_result, master, tmap)

    mode = prompt_result["mode"]          # već normalizovan (explain|practice|exam|quick)
    status = prompt_result["status"]      # ready|fallback|ambiguous|invalid

    if status == "ready":
        messages = [
            {"role": "system", "content": prompt_result["system_prompt"]},
            {"role": "user", "content": prompt_result["user_prompt"]},
        ]
        answer = _extract_answer(
            openai_chat(
                model, messages, timeout=timeout,
                max_tokens=_MAX_TOKENS.get(mode, 700),
            )
        )
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
