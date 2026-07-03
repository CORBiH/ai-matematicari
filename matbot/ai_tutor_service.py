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

from matbot.content_loader import get_master, get_thinkific_map, normalize_value
from matbot.prompt_builder import build_tutor_prompt, get_topic_context
from matbot.topic_lookup import get_final_topic

DEFAULT_GRADE = 6
DEFAULT_MODEL = "gpt-5-mini"

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


def _extract_answer(resp: Any) -> str:
    """Izvuci tekst iz OpenAI odgovora (isti oblik kao postojeći app: choices[0].message.content)."""
    try:
        content = resp.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        return ""
    return content if isinstance(content, str) else (content or "")


def _fallback_answer(lookup_result: dict) -> str:
    """Determinističan student-facing odgovor za ne-ready statuse (bez OpenAI-ja)."""
    return normalize_value(lookup_result.get("message")) or _DEFAULT_FALLBACK_ANSWER


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

    master = master if master is not None else get_master()
    tmap = tmap if tmap is not None else get_thinkific_map()

    lookup_result = get_final_topic(payload, master, tmap)
    prompt_result = build_tutor_prompt(payload, lookup_result, master, tmap)

    mode = prompt_result["mode"]          # već normalizovan (explain|practice|exam|quick)
    status = prompt_result["status"]      # ready|fallback|ambiguous|invalid

    if status == "ready":
        messages = [
            {"role": "system", "content": prompt_result["system_prompt"]},
            {"role": "user", "content": prompt_result["user_prompt"]},
        ]
        answer = _extract_answer(openai_chat(model, messages, timeout=timeout))
    else:
        # fallback/ambiguous/invalid → NE zovi OpenAI (deterministički bosanski tekst)
        answer = _fallback_answer(lookup_result)

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

    return {
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
