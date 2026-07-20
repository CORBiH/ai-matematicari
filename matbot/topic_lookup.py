"""Phase 1 — lookup i validacija topic-a za modularni AI tutor (6. razred).

Implementira tačan prioritet iz handoff dokumenta
(``docs/handoff/IT_HANDOFF_6_RAZRED_MVP_SEND_READY_FINAL.docx`` §4 i
``AI_TUTOR_GUIDELINES...docx`` §4). Sav sadržaj dolazi iz Excel fajlova preko
``matbot.content_loader`` — ništa nije hardkodirano.

Dvije vrste struktuiranih rezultata:

1. Validatori (``validate_topic``, ``validate_detected_topic``) vraćaju:
       {"topic": str, "status": "found|invalid|unknown", "message": str}

2. Lookup (``find_lesson``, ``get_final_topic``) vraćaju puni rezultat:
       {
         "final_topic": str,          # validan topic ili "unknown"
         "status": "found|unknown|ambiguous|invalid",
         "source": "lesson_id|lesson_url|composite|selected_topic|detected_topic|fallback",
         "message": str,              # bosanski, spreman za UI
         "matches": list[dict],       # redovi iz MAP-a koji su odgovarali (za debug/UI)
       }

Pravilo 10: ``final_topic`` koji nije ``"unknown"`` UVIJEK postoji u
AI_MATH_CONTENT_MASTER.TOPICS prije nego ga vratimo.
"""
from __future__ import annotations

from typing import Any

from matbot.content_loader import (
    get_master,
    get_thinkific_map,
    normalize_value,
)

# --- Poruke (bosanski; usklađene sa handoff §8 fallback tabelom) ----------------
MSG_AMBIGUOUS = (
    "Pronašao sam više sličnih lekcija. Izaberi oblast/temu koju trenutno radiš."
)
MSG_LESSON_NOT_FOUND = (
    "Ne mogu automatski prepoznati lekciju. Izaberi oblast iz liste ili pošalji "
    "zadatak, pa ću ti pomoći."
)
MSG_LESSON_UNMAPPED = (
    "Lekcija je pronađena, ali još nije mapirana na temu. Izaberi oblast/temu."
)
MSG_LESSON_TOPIC_MISSING = (
    "Lekcija je mapirana na temu '{topic}' koja ne postoji u masteru."
)
MSG_LESSON_FOUND = "Lekcija je prepoznata i mapirana na temu."
MSG_SELECTED_OK = "Ručno izabrana tema je validna."
MSG_SELECTED_INVALID = "Izabrana tema '{topic}' ne postoji u masteru."
MSG_DETECTED_OK = "Prepoznata tema (detected_topic) postoji u dozvoljenoj listi."
MSG_DETECTED_UNKNOWN = (
    "AI nije siguran u temu (detected_topic = unknown). Izaberi oblast ili pošalji "
    "jasniji zadatak."
)
MSG_DETECTED_INVALID = (
    "Prepoznata tema '{topic}' ne postoji; AI ne smije izmišljati temu."
)
MSG_UNKNOWN_FALLBACK = MSG_LESSON_NOT_FOUND

# statusi za validate_detected_topic / final_topic
UNKNOWN = "unknown"


def _result(
    final_topic: str,
    status: str,
    source: str,
    message: str,
    matches: list[dict] | None = None,
) -> dict[str, Any]:
    return {
        "final_topic": final_topic,
        "status": status,
        "source": source,
        "message": message,
        "matches": matches or [],
    }


def _resolve_runtime_topic(grade: Any, raw: Any) -> str:
    """Canonical NPP id for a runtime topic identifier, or "".

    Imported lazily: ``topic_resolver`` reads the content loader, and this module
    is imported very early. Never raises — an unresolvable id simply stays
    unresolved, exactly as before.
    """
    try:
        from matbot import topic_resolver
        found = topic_resolver.resolve_topic(grade, raw)
        return found.npp_id if found else ""
    except Exception:
        return ""


def _norm(val: Any) -> str:
    """Payload vrijednosti normalizuj isto kao ćelije u fajlu (npr. int order → str)."""
    return normalize_value(val)


# --- Osnovna provjera topic-a ---------------------------------------------------

def topic_exists(topic_id: Any, master: dict[str, Any] | None = None) -> bool:
    """True ako ``topic_id`` postoji u AI_MATH_CONTENT_MASTER.TOPICS."""
    master = master if master is not None else get_master()
    tid = _norm(topic_id)
    return bool(tid) and tid in master["topic_ids"]


def validate_topic(
    topic_id: Any, master: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Validator: postoji li topic u masteru. Vraća validator-rezultat."""
    master = master if master is not None else get_master()
    tid = _norm(topic_id)
    if not tid:
        return {"topic": UNKNOWN, "status": "invalid", "message": "Tema nije zadana."}
    if tid in master["topic_ids"]:
        return {"topic": tid, "status": "found", "message": "Tema postoji u masteru."}
    return {
        "topic": UNKNOWN,
        "status": "invalid",
        "message": MSG_SELECTED_INVALID.format(topic=tid),
    }


def validate_detected_topic(
    topic_id: Any,
    master: dict[str, Any] | None = None,
    tmap: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validator za AI-prepoznatu temu (handoff §5).

    Dozvoljeno: tema koja postoji u TOPICS **ili** TOPIC_REFERENCE, ili doslovno
    ``"unknown"``. Sve ostalo je ``invalid`` (AI ne smije izmišljati temu)."""
    master = master if master is not None else get_master()
    tmap = tmap if tmap is not None else get_thinkific_map()
    tid = _norm(topic_id)
    if not tid or tid.lower() == UNKNOWN:
        return {
            "topic": UNKNOWN,
            "status": UNKNOWN,
            "message": MSG_DETECTED_UNKNOWN,
        }
    if tid in master["topic_ids"] or tid in tmap["topic_reference_ids"]:
        return {"topic": tid, "status": "found", "message": MSG_DETECTED_OK}
    return {
        "topic": UNKNOWN,
        "status": "invalid",
        "message": MSG_DETECTED_INVALID.format(topic=tid),
    }


# --- Lookup lekcije u THINKIFIC_MAP.MAP -----------------------------------------

def _matches_to_result(
    matches: list[dict], source: str
) -> dict[str, Any] | None:
    """Pretvori redove koji su odgovarali jednom ključu u rezultat.

    Vraća ``None`` ako nema poklapanja (pozivalac prelazi na sljedeći ključ).
    Ako svi redovi upućuju na JEDNU temu → found; više različitih tema → ambiguous;
    poklapanja bez teme → unknown (nemapirano)."""
    if not matches:
        return None
    distinct = {m.get("topic", "") for m in matches if m.get("topic")}
    if len(distinct) == 1:
        return _result(next(iter(distinct)), "found", source, MSG_LESSON_FOUND, matches)
    if len(distinct) > 1:
        return _result(UNKNOWN, "ambiguous", source, MSG_AMBIGUOUS, matches)
    return _result(UNKNOWN, UNKNOWN, source, MSG_LESSON_UNMAPPED, matches)


def find_lesson(
    payload: dict[str, Any], tmap: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Pronađi lekciju i njen topic po prioritetu ključeva (handoff §4):

    1. thinkific_lesson_id
    2. lesson_url
    3. course_name + section_name + lesson_order + lesson_title (kompozit)
    4. section_name + lesson_title (samo ako je jednoznačno)
    5. lesson_title sam (zadnji fallback; ako više različitih tema → ambiguous)
    """
    tmap = tmap if tmap is not None else get_thinkific_map()
    lessons = tmap["lessons"]

    # 1) thinkific_lesson_id (poslovno "lesson_id")
    lesson_id = _norm(payload.get("thinkific_lesson_id") or payload.get("lesson_id"))
    if lesson_id:
        res = _matches_to_result(
            [l for l in lessons if l.get("thinkific_lesson_id") == lesson_id],
            "lesson_id",
        )
        if res is not None:
            return res

    # 2) lesson_url
    lesson_url = _norm(payload.get("lesson_url"))
    if lesson_url:
        res = _matches_to_result(
            [l for l in lessons if l.get("lesson_url") == lesson_url], "lesson_url"
        )
        if res is not None:
            return res

    # 3) kompozitni ključ (svi dijelovi obavezni)
    course = _norm(payload.get("course_name"))
    section = _norm(payload.get("section_name"))
    order = _norm(payload.get("lesson_order"))
    title = _norm(payload.get("lesson_title"))
    if course and section and order and title:
        res = _matches_to_result(
            [
                l
                for l in lessons
                if l.get("course_name") == course
                and l.get("section_name") == section
                and l.get("lesson_order") == order
                and l.get("lesson_title") == title
            ],
            "composite",
        )
        if res is not None:
            return res

    # 4) section_name + lesson_title — koristi SAMO ako je jednoznačno
    if section and title:
        matches = [
            l
            for l in lessons
            if l.get("section_name") == section and l.get("lesson_title") == title
        ]
        distinct = {m.get("topic", "") for m in matches if m.get("topic")}
        if len(distinct) == 1:
            return _result(
                next(iter(distinct)), "found", "composite", MSG_LESSON_FOUND, matches
            )
        # ako je dvosmisleno, ne biramo tiho — pada na title-only koji prijavi ambiguity

    # 5) lesson_title sam — zadnji fallback
    if title:
        matches = [l for l in lessons if l.get("lesson_title") == title]
        if matches:
            distinct = {m.get("topic", "") for m in matches if m.get("topic")}
            if len(distinct) == 1:
                return _result(
                    next(iter(distinct)), "found", "fallback", MSG_LESSON_FOUND, matches
                )
            if len(distinct) > 1:
                return _result(UNKNOWN, "ambiguous", "fallback", MSG_AMBIGUOUS, matches)

    return _result(UNKNOWN, UNKNOWN, "fallback", MSG_LESSON_NOT_FOUND, [])


# --- Glavni resolver: final_topic -----------------------------------------------

_LESSON_FIELDS = (
    "thinkific_lesson_id",
    "lesson_id",
    "lesson_url",
    "course_name",
    "section_name",
    "lesson_order",
    "lesson_title",
)


def get_final_topic(
    payload: dict[str, Any],
    master: dict[str, Any] | None = None,
    tmap: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Odredi ``final_topic`` po prioritetu (handoff §4):

    1. Kontekst Thinkific lekcije (``entry_source == 'thinkific_lesson'`` ili
       prisutna lekcijska polja) → ``find_lesson``; prihvata se samo red sa
       ``status == 'mapped'``. Dvosmisleno → traži ručni izbor (status ambiguous).
    2. ``selected_topic`` ako je validan.
    3. ``detected_topic`` ako je validan ili ``"unknown"``.
    4. Inače ``unknown``.

    Ako je korisnik dao temu koja ne postoji (selected/detected), a ništa drugo se
    ne razriješi, vraća ``status == 'invalid'`` (informativnije od običnog unknown).
    Garancija (pravilo 10): svaki non-unknown ``final_topic`` postoji u masteru.
    """
    master = master if master is not None else get_master()
    tmap = tmap if tmap is not None else get_thinkific_map()

    entry_source = _norm(payload.get("entry_source"))
    has_lesson_keys = any(_norm(payload.get(k)) for k in _LESSON_FIELDS)

    if entry_source == "thinkific_lesson" or has_lesson_keys:
        lesson = find_lesson(payload, tmap)
        if lesson["status"] == "ambiguous":
            return lesson
        if lesson["status"] == "found":
            topic = lesson["final_topic"]
            # samo mapirani redovi se prihvataju (handoff: row.status == 'mapped')
            mapped_ok = any(
                m.get("topic") == topic and m.get("status", "").lower() == "mapped"
                for m in lesson["matches"]
            )
            if topic in master["topic_ids"] and mapped_ok:
                return lesson
            if topic not in master["topic_ids"]:
                return _result(
                    UNKNOWN,
                    "invalid",
                    lesson["source"],
                    MSG_LESSON_TOPIC_MISSING.format(topic=topic),
                    lesson["matches"],
                )
            # tema postoji ali red nije 'mapped' → tretiraj kao nepoznatu lekciju
        # nepoznata/nemapirana lekcija → nastavi na selected/detected

    invalid_msgs: list[str] = []

    selected = _norm(payload.get("selected_topic"))
    if selected:
        if selected in master["topic_ids"]:
            return _result(selected, "found", "selected_topic", MSG_SELECTED_OK, [])
        # A RUNTIME topic id (e.g. "29073") is not an NPP id, but it names a real
        # tema. Resolving it through the SAME canonical resolver the rest of the
        # pipeline uses keeps topic identity in one model — otherwise the lookup
        # rejects a topic the generator considers perfectly valid.
        canonical = _resolve_runtime_topic(payload.get("grade"), selected)
        if canonical and canonical in master["topic_ids"]:
            return _result(canonical, "found", "selected_topic_runtime_id",
                           MSG_SELECTED_OK, [])
        invalid_msgs.append(MSG_SELECTED_INVALID.format(topic=selected))

    detected = _norm(payload.get("detected_topic"))
    if detected:
        if detected.lower() == UNKNOWN:
            return _result(UNKNOWN, UNKNOWN, "detected_topic", MSG_DETECTED_UNKNOWN, [])
        # final_topic mora postojati u masteru (pravilo 10)
        if detected in master["topic_ids"]:
            return _result(detected, "found", "detected_topic", MSG_DETECTED_OK, [])
        invalid_msgs.append(MSG_DETECTED_INVALID.format(topic=detected))

    if invalid_msgs:
        return _result(UNKNOWN, "invalid", "fallback", " ".join(invalid_msgs), [])
    return _result(UNKNOWN, UNKNOWN, "fallback", MSG_UNKNOWN_FALLBACK, [])
