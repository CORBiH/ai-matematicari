"""Phase 2 — modularni prompt builder za 6. razred (MVP AI tutor).

Spaja izlaz Phase 1 (``content_loader`` + ``topic_lookup``) sa pedagoškim
sadržajem iz AI_MATH_CONTENT_MASTER u strukturiran prompt rezultat. **NE zove
OpenAI** i **ne radi mrežne/IO operacije** — čist je i deterministički.

Ništa iz sadržaja (teme, zadaci, greške, hintovi) NIJE hardkodirano: dolazi iz
``master_content`` (Excel je izvor istine). Hardkodiran je samo *tekst pravila
ponašanja* (globalne modularne smjernice i mode-instrukcije), isto kao što
``prompts.py`` drži pedagoška pravila.

Rezultat (``build_tutor_prompt`` / ``build_fallback_prompt``)::

    {
      "system_prompt": str,
      "user_prompt": str,
      "mode": "explain|practice|exam|quick",
      "final_topic": str,               # = effective_topic (ili "unknown")
      "opened_lesson_topic": str,       # tema otvorene Thinkific lekcije (iz lookup-a)
      "effective_topic": str,           # tema stvarno korištena za kontekst prompta
      "status": "ready|fallback|ambiguous|invalid",
      "topic_context_used": bool,
      "video_flow_used": bool,
      "topic_conflict": bool,
    }

Phase 2 (audit): system prompt za tutor putanju dolazi ISKLJUČIVO iz
``matbot.tutor_prompts`` (novi, razred-uslovni stack bez legacy baze iz
``prompts.py``). Legacy ``/submit`` i dalje koristi ``prompts.py`` — netaknuto.
"""
from __future__ import annotations

import re
from typing import Any

from matbot.content_loader import normalize_value
from matbot.tutor_prompts import (
    CHAT_FORMATTING_GUIDELINES,
    GLOBAL_MODULAR_GUIDELINES,
    LANGUAGE_TONE_GUIDELINES,
    build_tutor_system_prompt,
)
from matbot.tutor_prompts import global_modular_guidelines as _global_modular_guidelines

# --- Modovi ---------------------------------------------------------------------
VALID_MODES = ("explain", "practice", "exam", "quick")
DEFAULT_MODE = "explain"

# Kanonske vrijednosti + bosanski UI aliasi (mapirani nakon lower+underscore).
_MODE_ALIASES = {
    "explain": "explain",
    "objasni": "explain",
    "objasni_mi": "explain",
    "practice": "practice",
    "vjezba": "practice",
    "vjezbaj": "practice",
    "vjezbaj_sa_mnom": "practice",
    "exam": "exam",
    "kontrolni": "exam",
    "test": "exam",
    "sutra_imam_kontrolni": "exam",
    "quick": "quick",
    "brzo": "quick",
    "rezultat": "quick",
    "samo_rezultat": "quick",
}

# --- Polja topic konteksta (tačno prema handoff §4/§6) --------------------------
TOPIC_CONTEXT_FIELDS = (
    "lesson_scope",
    "common_mistake_1", "common_mistake_2", "common_mistake_3",
    "ai_if_mistake_1", "ai_if_mistake_2", "ai_if_mistake_3",
    "hint_method",
    "solved_example_problem",
    "solved_example_step_1", "solved_example_step_2",
    "solved_example_step_3", "solved_example_step_4",
    "solved_example_answer",
    "typical_task_1", "typical_task_2", "typical_task_3",
    "controlni_task_1", "controlni_task_2", "controlni_task_3",
    "controlni_trick", "controlni_warning",
    "forbidden_ai_behavior",
    "when_to_recommend_video",
    "exit_criteria",
)
_TOPIC_META_FIELDS = ("grade", "oblast", "display_name", "topic_type", "difficulty_level")

# NAPOMENA (Phase 2): GLOBAL_MODULAR_GUIDELINES, LANGUAGE_TONE_GUIDELINES i
# CHAT_FORMATTING_GUIDELINES sada žive u matbot.tutor_prompts (jedan izvor
# system-prompt teksta); ovdje su re-importovani radi kompatibilnosti.

# statusi
_STATUS_READY = "ready"
_FALLBACK_STATUS = {"unknown": "fallback", "ambiguous": "ambiguous", "invalid": "invalid"}


# --- Male pomoćne funkcije ------------------------------------------------------

def _collect(ctx: dict, keys: tuple[str, ...]) -> list[str]:
    return [ctx.get(k, "") for k in keys if ctx.get(k)]


def _compose_system_prompt(
    grade: Any,
    extra: list[str] | None = None,
    topic_context: dict | None = None,
) -> str:
    """Jedinstvena tačka sastavljanja system prompta za SVE modularne putanje.

    Phase 2 (audit): delegira na ``tutor_prompts.build_tutor_system_prompt``
    (razred-uslovni stack, bez legacy baze; konstrukcijska pravila ulaze samo
    kada ih tema traži — ``topic_context``)."""
    return build_tutor_system_prompt(grade, topic_context=topic_context, extra=extra)


# --- Javne pomoćne funkcije -----------------------------------------------------

def normalize_mode(mode: Any) -> str:
    """Vrati jedan od ``explain|practice|exam|quick``. Nepoznato/prazno → explain."""
    key = re.sub(r"[\s\-]+", "_", normalize_value(mode).lower())
    return _MODE_ALIASES.get(key, DEFAULT_MODE)


def trim_conversation_history(history: Any, limit: int = 5) -> list:
    """Zadnjih ``limit`` poruka. None/ne-lista → []. Redoslijed očuvan."""
    if not isinstance(history, list) or limit <= 0:
        return []
    return history[-limit:]


def get_topic_context(final_topic: Any, master_content: dict) -> dict:
    """Izvuci polja iz TOPICS reda za dati topic. ``{}`` ako je tema
    ``unknown``/nepostojeća (nikad ne izmišlja sadržaj)."""
    tid = normalize_value(final_topic)
    if not tid or tid.lower() == "unknown":
        return {}
    row = (master_content or {}).get("topics_by_id", {}).get(tid)
    if not row:
        return {}
    ctx: dict[str, str] = {"topic": tid}
    for f in _TOPIC_META_FIELDS:
        if row.get(f):
            ctx[f] = row[f]
    for f in TOPIC_CONTEXT_FIELDS:
        ctx[f] = row.get(f, "")
    return ctx


def get_oblast_topics(oblast: Any, master_content: dict) -> list[dict]:
    """READY topic redovi za datu oblast (case-insensitive poređenje naziva),
    u redoslijedu TOPICS sheeta. ``[]`` ako oblast ne postoji — nikad se ne
    izmišlja."""
    key = normalize_value(oblast).lower()
    if not key:
        return []
    return [
        r
        for r in (master_content or {}).get("topics", [])
        if r.get("topic")
        and normalize_value(r.get("oblast")).lower() == key
        and (not r.get("status") or normalize_value(r.get("status")).upper() == "READY")
    ]


def get_video_flow_context(
    payload: dict, final_topic: Any, master_content: dict
) -> dict | None:
    """VIDEO_FLOW red za temu — SAMO ako je ``entry_source == 'thinkific_lesson'``
    i postoji red sa istim ``topic``. Bira najprecizniji red: lesson_title, pa
    lesson_order, pa prvi red za tu temu. Inače ``None``."""
    payload = payload or {}
    if normalize_value(payload.get("entry_source")) != "thinkific_lesson":
        return None
    tid = normalize_value(final_topic)
    if not tid or tid.lower() == "unknown":
        return None
    rows = (master_content or {}).get("video_flow") or []
    topic_rows = [r for r in rows if r.get("topic") == tid]
    if not topic_rows:
        return None

    title = normalize_value(payload.get("lesson_title"))
    if title:
        exact = [r for r in topic_rows if r.get("lesson_title") == title]
        if exact:
            return exact[0]
    order = normalize_value(payload.get("lesson_order"))
    if order:
        by_order = [r for r in topic_rows if r.get("lesson_order") == order]
        if by_order:
            return by_order[0]
    return topic_rows[0]


def build_mode_instructions(mode: Any, final_topic: Any, topic_context: dict) -> str:
    """Mode-specifične instrukcije (bosanski). ``exam`` bez teme traži oblast."""
    mode = normalize_mode(mode)
    tid = normalize_value(final_topic)
    known = bool(topic_context) and bool(tid) and tid.lower() != "unknown"

    if mode == "practice":
        block = (
            "MOD: VJEŽBAJ (practice)\n"
            "- Daj TAČNO JEDAN zadatak i onda ČEKAJ odgovor učenika.\n"
            "- NE daji 10 zadataka odjednom.\n"
        )
        tasks = _collect(topic_context, ("typical_task_1", "typical_task_2", "typical_task_3"))
        if tasks:
            block += "- Izaberi JEDAN od ponuđenih tipičnih zadataka:\n"
            block += "".join(f"   • {t}\n" for t in tasks)
        return block

    if mode == "exam":
        if not known:
            return (
                "MOD: KONTROLNI (exam)\n"
                "- Tema kontrolnog NIJE poznata. PRVO pitaj učenika iz koje je "
                "OBLASTI/TEME kontrolni (npr. skupovi, djeljivost, razlomci, "
                "decimalni brojevi, kružnica/uglovi). NE pretpostavljaj temu.\n"
            )
        block = (
            "MOD: KONTROLNI (exam)\n"
            "- Daj TAČNO 3 kontrolna zadatka, zatim 1 trik i 1 upozorenje.\n"
            "- Format: zadaci kao numerisana lista 1., 2., 3., zatim red "
            "\"Trik:\" i red \"Upozorenje:\".\n"
        )
        c_tasks = _collect(
            topic_context, ("controlni_task_1", "controlni_task_2", "controlni_task_3")
        )
        if c_tasks:
            block += "- Kontrolni zadaci:\n"
            block += "".join(f"   • {t}\n" for t in c_tasks)
        trick = topic_context.get("controlni_trick", "")
        warning = topic_context.get("controlni_warning", "")
        if trick:
            block += f"- Trik: {trick}\n"
        if warning:
            block += f"- Upozorenje: {warning}\n"
        return block

    if mode == "quick":
        return (
            "MOD: SAMO REZULTAT (quick)\n"
            "- Odgovor mora biti KOMPAKTAN: SAMO rezultat + najviše JEDNA kratka "
            "rečenica provjere, sve u jednom kratkom pasusu.\n"
            "- Bez dugog objašnjenja, bez naslova i bez nizanja koraka.\n"
        )

    # explain (default)
    return (
        "MOD: OBJASNI (explain)\n"
        "- Budi razgovoran, ne repetitivan: prvo ideja/pristup u 2–3 kratke "
        "rečenice, zatim JEDAN kratak primjer ILI ponudi primjer pitanjem "
        "(npr. \"Hoćeš primjer?\").\n"
        "- NE prepričavaj cijelu lekciju odjednom i NE ispisuj sav sadržaj teme "
        "svaki put — podatke o temi koristi kao pomoć, ne kao skriptu.\n"
        "- Ako historija razgovora VEĆ sadrži objašnjenje ove teme, NE ponavljaj "
        "ga: nastavi razgovor, odgovori na follow-up ili daj sljedeći "
        "primjer/korak.\n"
        "- Ako učenik kratko potvrdi (\"može\", \"hoću\", \"nastavi\"), nastavi "
        "primjerom ili sljedećim korakom — bez ponavljanja objašnjenja.\n"
        "- Budi kratak; detaljno objašnjavaj SAMO ako učenik to izričito zatraži.\n"
        "- Ako nedostaje kontekst za rješavanje, postavi JEDNO kratko pitanje "
        "prije rješavanja.\n"
    )


def build_practice_followup_instructions(payload: dict, topic_context: dict) -> str:
    """Phase 4.3 — instrukcije kada je učenikova poruka ODGOVOR na prethodni
    practice zadatak (``payload.interaction_phase == 'answering_practice_task'``).

    Zamjenjuje standardne practice instrukcije: AI provjerava odgovor umjesto da
    postavlja novi zadatak ili ponavlja lekciju."""
    last_task = normalize_value((payload or {}).get("last_tutor_task"))[:600]
    block = (
        "MOD: VJEŽBA — PROVJERA ODGOVORA (practice follow-up)\n"
        "- Učenikova poruka je ODGOVOR na prethodno postavljeni zadatak — NE "
        "tretiraj je kao novo pitanje niti kao zahtjev za novi zadatak.\n"
        "- Provjeri tačnost odgovora koristeći ZADNJI ZADATAK i historiju razgovora.\n"
        "- Ako je TAČNO: kratko potvrdi (npr. \"Tačno!\"), u 1–2 rečenice objasni "
        "zašto, pa po želji daj JEDAN novi mali zadatak ili sljedeći korak.\n"
        "- Ako NIJE tačno: blago reci da nije tačno. Za kratak računski zadatak "
        "prikaži tačan račun i rezultat; za konceptualni zadatak daj JEDAN hint "
        "ili JEDAN sljedeći korak.\n"
        "- Ako učenik napiše \"ne znam\", \"objasni\" ili \"pomozi\": daj JEDAN "
        "vođeni hint ili JEDAN sljedeći korak — NE novi zadatak i NE cijelo "
        "rješenje odmah.\n"
        "- Ako učenik kratko potvrdi (npr. \"može\", \"da\", \"hajde\"): nastavi "
        "— daj sljedeći mali zadatak ili sljedeći korak.\n"
        "- NE ponavljaj isti zadatak osim ako je odgovor nejasan.\n"
        "- NE ponavljaj cijelo objašnjenje teme i NE počinji isti zadatak ispočetka.\n"
        "- Odgovor mora biti KRATAK i prirodan za chat: bez naslova poput "
        "\"### Tema\" i bez dugih lekcija.\n"
    )
    if last_task:
        block += f"ZADNJI ZADATAK (kojem učenik odgovara):\n{last_task}\n"
    return block


def build_continuation_instructions(payload: dict) -> str:
    """Phase 7.2 — učenikova poruka je KRATKA POTVRDA/nastavak ("može", "hoću",
    "nastavi", "daj primjer") poslije prethodnog odgovora tutora
    (``payload.interaction_phase == 'continuing_explanation'``).

    Zamjenjuje standardni mode blok: AI nastavlja od svoje zadnje poruke
    (``last_tutor_message``) umjesto da ponavlja objašnjenje teme ispočetka."""
    last_msg = normalize_value((payload or {}).get("last_tutor_message"))[:600]
    block = (
        "MOD: NASTAVAK RAZGOVORA (follow-up)\n"
        "- Učenikova poruka je kratka potvrda/nastavak (npr. \"može\", \"hoću\", "
        "\"nastavi\", \"daj primjer\") — NE tretiraj je kao novi zahtjev za "
        "objašnjenjem teme.\n"
        "- NASTAVI tačno tamo gdje je tvoja ZADNJA PORUKA stala; NE ponavljaj "
        "objašnjenje ni \"Ideja:\" blok ispočetka.\n"
        "- Ako je zadnja poruka ponudila primjer (npr. \"Hoćeš primjer?\"), daj "
        "JEDAN konkretan primjer korak po korak.\n"
        "- Ako je zadnja poruka ponudila zajedničko rješavanje, počni JEDAN "
        "vođeni primjer i uključi učenika.\n"
        "- Budi kratak i prirodan; po potrebi završi JEDNIM kratkim pitanjem za "
        "sljedeći korak.\n"
        "- NE ispisuj ponovo naslov teme (\"Tema:\") osim ako je zaista potrebno.\n"
    )
    if last_msg:
        block += f"ZADNJA PORUKA TUTORA (nastavi od nje):\n{last_msg}\n"
    return block


# --- Blokovi user prompta -------------------------------------------------------

_ENTRY_LABELS = (
    ("grade", "Razred"),
    ("entry_source", "Entry source"),
    ("course_name", "Kurs"),
    ("section_name", "Sekcija"),
    ("lesson_order", "Lesson order"),
    ("lesson_title", "Thinkific lekcija"),
    ("selected_topic", "Selected topic"),
    ("selected_oblast", "Selected oblast"),
    ("detected_topic", "Detected topic"),
)

_TOPIC_LABELS = (
    ("display_name", "Naziv teme"),
    ("oblast", "Oblast"),
    ("lesson_scope", "Opseg lekcije (lesson_scope)"),
    ("common_mistake_1", "Česta greška 1"),
    ("common_mistake_2", "Česta greška 2"),
    ("common_mistake_3", "Česta greška 3"),
    ("ai_if_mistake_1", "AI ako greška 1"),
    ("ai_if_mistake_2", "AI ako greška 2"),
    ("ai_if_mistake_3", "AI ako greška 3"),
    ("hint_method", "Metoda hinta"),
    ("solved_example_problem", "Riješeni primjer — zadatak"),
    ("solved_example_step_1", "Riješeni primjer — korak 1"),
    ("solved_example_step_2", "Riješeni primjer — korak 2"),
    ("solved_example_step_3", "Riješeni primjer — korak 3"),
    ("solved_example_step_4", "Riješeni primjer — korak 4"),
    ("solved_example_answer", "Riješeni primjer — odgovor"),
    ("typical_task_1", "Tipičan zadatak 1"),
    ("typical_task_2", "Tipičan zadatak 2"),
    ("typical_task_3", "Tipičan zadatak 3"),
    ("controlni_task_1", "Kontrolni zadatak 1"),
    ("controlni_task_2", "Kontrolni zadatak 2"),
    ("controlni_task_3", "Kontrolni zadatak 3"),
    ("controlni_trick", "Kontrolni trik"),
    ("controlni_warning", "Kontrolni upozorenje"),
    ("when_to_recommend_video", "Kada preporučiti video"),
    ("exit_criteria", "Kriterij izlaska"),
)

_VIDEO_LABELS = (
    ("sta_ucenik_upravo_naucio", "Šta je učenik upravo naučio"),
    ("ai_after_video", "AI poslije videa"),
    ("recommended_mode", "Preporučeni mod"),
)


def _build_entry_context(payload: dict, final_topic: str, mode: str) -> str:
    lines = ["KONTEKST:"]
    for key, label in _ENTRY_LABELS:
        val = normalize_value(payload.get(key))
        if val:
            lines.append(f"- {label}: {val}")
    lines.append(f"- Final topic: {final_topic}")
    lines.append(f"- Mod: {mode}")
    return "\n".join(lines)


# Phase 1 (audit): topic blok filtriran po modu — explain ne treba kontrolne
# zadatke, exam ne treba riješeni primjer itd. Manji prompt = jasnije instrukcije.
_META_FIELDS = ("display_name", "oblast", "lesson_scope")
_MISTAKE_FIELDS = (
    "common_mistake_1", "common_mistake_2", "common_mistake_3",
    "ai_if_mistake_1", "ai_if_mistake_2", "ai_if_mistake_3",
)
_SOLVED_FIELDS = (
    "solved_example_problem",
    "solved_example_step_1", "solved_example_step_2",
    "solved_example_step_3", "solved_example_step_4",
    "solved_example_answer",
)
_TYPICAL_FIELDS = ("typical_task_1", "typical_task_2", "typical_task_3")
_CONTROLNI_FIELDS = (
    "controlni_task_1", "controlni_task_2", "controlni_task_3",
    "controlni_trick", "controlni_warning",
)
_EXTRA_FIELDS = ("when_to_recommend_video", "exit_criteria")

_MODE_TOPIC_FIELDS = {
    "explain": frozenset(
        _META_FIELDS + _MISTAKE_FIELDS + ("hint_method",) + _SOLVED_FIELDS + _EXTRA_FIELDS
    ),
    "practice": frozenset(
        _META_FIELDS + _MISTAKE_FIELDS + ("hint_method",)
        + _TYPICAL_FIELDS + _SOLVED_FIELDS + _EXTRA_FIELDS
    ),
    "exam": frozenset(
        _META_FIELDS + _MISTAKE_FIELDS + ("hint_method",) + _CONTROLNI_FIELDS
    ),
    "quick": frozenset(_META_FIELDS),
}


def _build_topic_block(topic_context: dict, mode: str | None = None) -> str:
    """Topic blok za user prompt. ``mode`` filtrira polja po namjeni (Phase 1);
    ``None``/nepoznat mod zadržava staro ponašanje (sva polja)."""
    if not topic_context:
        return ""
    allowed = _MODE_TOPIC_FIELDS.get(mode)
    lines = ["PODACI O TEMI (iz AI_MATH_CONTENT_MASTER):"]
    for key, label in _TOPIC_LABELS:
        if allowed is not None and key not in allowed:
            continue
        val = topic_context.get(key, "")
        if val:
            lines.append(f"- {label}: {val}")
    return "\n".join(lines)


def _build_video_flow_block(vf: dict | None) -> str:
    if not vf:
        return ""
    lines = ["VIDEO_FLOW KONTEKST (učenik dolazi iz Thinkific lekcije):"]
    for key, label in _VIDEO_LABELS:
        val = normalize_value(vf.get(key))
        if val:
            lines.append(f"- {label}: {val}")
    return "\n".join(lines)


def _build_student_block(payload: dict) -> str:
    parts = []
    msg = normalize_value(payload.get("student_message") or payload.get("message"))
    if msg:
        parts.append(f"PORUKA UČENIKA:\n{msg}")
    ocr = normalize_value(
        payload.get("image_ocr_text")
        or payload.get("ocr_text")
        or payload.get("image_text")
    )
    if ocr:
        parts.append(f"TEKST ZADATKA SA SLIKE (OCR):\n{ocr}")
    return "\n\n".join(parts)


_ASSISTANT_ROLES = {"assistant", "bot", "tutor", "ai"}


def build_history_messages(history: Any) -> list[dict]:
    """Phase 2 (audit) — historija kao PRAVE chat poruke umjesto teksta u user
    promptu. Modeli znatno bolje prate tok dijaloga kroz role poruke.

    Vraća listu ``{"role": "user"|"assistant", "content": str}`` (zadnjih 5,
    prazno/nevalidno se preskače). Nepoznate role se tretiraju kao user."""
    messages: list[dict] = []
    for msg in trim_conversation_history(history):
        if isinstance(msg, dict):
            role = normalize_value(msg.get("role")).lower() or "user"
            content = normalize_value(
                msg.get("content") or msg.get("text") or msg.get("message")
            )
        else:
            role, content = "user", normalize_value(msg)
        if not content:
            continue
        messages.append({
            "role": "assistant" if role in _ASSISTANT_ROLES else "user",
            "content": content,
        })
    return messages


# --- Glavni builderi ------------------------------------------------------------

def build_tutor_prompt(
    payload: dict,
    lookup_result: dict,
    master_content: dict,
    thinkific_map: dict | None = None,  # rezervisano; nije potrebno u Phase 2
) -> dict:
    """Sagradi strukturiran prompt iz Phase 1 lookup rezultata + master sadržaja.

    ``lookup_result.status``: ``found`` → puni prompt (status ``ready``); ostalo
    (``unknown``/``ambiguous``/``invalid``) → ``build_fallback_prompt``.
    """
    payload = payload or {}
    lookup_result = lookup_result or {}
    master_content = master_content or {}

    status = normalize_value(lookup_result.get("status")).lower() or "unknown"
    if status != "found":
        reason = status if status in ("ambiguous", "invalid") else "unknown"
        return build_fallback_prompt(payload, reason)

    mode = normalize_mode(payload.get("mode"))
    # Phase 4.3: follow-up odgovor na practice zadatak uvijek ide kao practice.
    # Phase 7.2: kratka potvrda ("može", "nastavi") → nastavak, ne novo objašnjenje.
    interaction_phase = normalize_value(payload.get("interaction_phase")).lower()
    is_practice_followup = interaction_phase == "answering_practice_task"
    is_continuation = interaction_phase == "continuing_explanation"
    if is_practice_followup:
        mode = "practice"
    lesson_topic = normalize_value(lookup_result.get("final_topic"))
    topic_ids = master_content.get("topic_ids", set())

    # Konflikt tema (guidelines §9): otvorena lekcija vs. validan detected_topic.
    detected = normalize_value(payload.get("detected_topic"))
    is_lesson_ctx = normalize_value(payload.get("entry_source")) == "thinkific_lesson"
    topic_conflict = (
        is_lesson_ctx
        and bool(detected)
        and detected.lower() != "unknown"
        and detected in topic_ids
        and detected != lesson_topic
    )
    effective_topic = detected if topic_conflict else lesson_topic

    topic_context = get_topic_context(effective_topic, master_content)
    video_flow = get_video_flow_context(payload, effective_topic, master_content)

    # --- system prompt ---
    forbidden = topic_context.get("forbidden_ai_behavior", "")
    extra = (
        ["ZABRANJENO PONAŠANJE ZA OVU TEMU (forbidden_ai_behavior):\n- " + forbidden]
        if forbidden
        else None
    )
    system_prompt = _compose_system_prompt(
        payload.get("grade"), extra, topic_context=topic_context
    )

    # --- user prompt ---
    user_parts = [_build_entry_context(payload, effective_topic, mode)]
    if topic_conflict:
        user_parts.append(
            "NAPOMENA O NESLAGANJU TEME:\n"
            f"- Otvorena lekcija je tema '{lesson_topic}', ali zadatak izgleda kao "
            f"tema '{effective_topic}'.\n"
            "- Riješi zadatak prema STVARNOJ temi zadatka i KRATKO napomeni ovo "
            "neslaganje učeniku."
        )
    if is_practice_followup:
        mode_block = build_practice_followup_instructions(payload, topic_context)
    elif is_continuation:
        mode_block = build_continuation_instructions(payload)
    else:
        mode_block = build_mode_instructions(mode, effective_topic, topic_context)
    for block in (
        _build_topic_block(topic_context, mode=mode),
        _build_video_flow_block(video_flow),
        mode_block,
        _build_student_block(payload),
    ):
        if block:
            user_parts.append(block)
    user_prompt = "\n\n".join(user_parts).strip()

    return {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        # Phase 2: historija ide kao PRAVE role poruke (system → history → user)
        "history_messages": build_history_messages(payload.get("conversation_history")),
        "mode": mode,
        "final_topic": effective_topic,
        "opened_lesson_topic": lesson_topic,
        "effective_topic": effective_topic,
        "status": _STATUS_READY,
        "topic_context_used": bool(topic_context),
        "video_flow_used": bool(video_flow),
        "topic_conflict": topic_conflict,
    }


def build_general_tutor_prompt(payload: dict) -> dict:
    """Phase 6 — free_chat sa KONKRETNIM pitanjem, ali tema nije prepoznata.

    Gradi prompt BEZ topic konteksta (ništa se ne izmišlja): base tutor + modularna
    pravila + mode instrukcije + poruka učenika. Status je ``ready`` jer se OpenAI
    poziva; ``final_topic`` ostaje ``"unknown"``."""
    payload = payload or {}
    mode = normalize_mode(payload.get("mode"))
    # Phase 7.2: i bez prepoznate teme poštuj fazu interakcije (practice odgovor
    # ili nastavak razgovora) umjesto standardnog mode bloka.
    phase = normalize_value(payload.get("interaction_phase")).lower()
    if phase == "answering_practice_task":
        mode = "practice"
        mode_block = build_practice_followup_instructions(payload, {})
    elif phase == "continuing_explanation":
        mode_block = build_continuation_instructions(payload)
    else:
        mode_block = build_mode_instructions(mode, "unknown", {})

    system_prompt = _compose_system_prompt(payload.get("grade"))

    user_parts = [
        _build_entry_context(payload, "unknown", mode),
        "NAPOMENA (tema nije prepoznata):\n"
        "- Pitanje učenika je konkretno, ali tema nije pronađena u biblioteci tema.\n"
        f"- Odgovori na KONKRETNO pitanje koristeći gradivo {normalize_value(payload.get('grade')) or '6'}. razreda, kratko i "
        "korak po korak.\n"
        "- NE izmišljaj temu i ne spominji internu listu tema.",
        mode_block,
        _build_student_block(payload),
    ]
    user_prompt = "\n\n".join(p for p in user_parts if p).strip()

    return {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "history_messages": build_history_messages(payload.get("conversation_history")),
        "mode": mode,
        "final_topic": "unknown",
        "opened_lesson_topic": "unknown",
        "effective_topic": "unknown",
        "status": _STATUS_READY,
        "topic_context_used": False,
        "video_flow_used": False,
        "topic_conflict": False,
    }


def build_exam_oblast_prompt(payload: dict, master_content: dict) -> dict | None:
    """Phase 7 — priprema kontrolnog za CIJELU OBLAST (bez pojedinačne teme).

    Vraća ready prompt SAMO kada su ispunjeni svi uslovi: ``mode == exam``,
    ``selected_topic`` prazan, a ``selected_oblast`` postoji u masteru
    (case-insensitive). U svakom drugom slučaju vraća ``None`` i pozivalac
    nastavlja postojeći tok (topic-based exam ostaje netaknut).

    Kontekst su ISKLJUČIVO READY teme te oblasti iz mastera (display_name +
    ``controlni_*`` materijal) — teme se nikad ne izmišljaju. ``final_topic``
    ostaje ``"unknown"`` (pravilo 10: non-unknown final_topic mora postojati u
    TOPICS), a oblast se vraća kroz dodatni ključ ``exam_oblast``."""
    payload = payload or {}
    if normalize_mode(payload.get("mode")) != "exam":
        return None
    if normalize_value(payload.get("selected_topic")):
        return None
    oblast = normalize_value(payload.get("selected_oblast"))
    if not oblast:
        return None
    rows = get_oblast_topics(oblast, master_content or {})
    if not rows:
        return None
    canonical = normalize_value(rows[0].get("oblast")) or oblast

    # oblast (npr. "Osnovne geometrijske konstrukcije...") može tražiti
    # konstrukcijski blok i bez pojedinačne teme
    system_prompt = _compose_system_prompt(
        payload.get("grade"), topic_context={"oblast": canonical}
    )

    lines = [
        f"OBLAST KONTROLNOG: {canonical}",
        "TEME I KONTROLNI MATERIJAL OVE OBLASTI (iz AI_MATH_CONTENT_MASTER):",
    ]
    for r in rows:
        lines.append(f"- Tema: {r.get('display_name') or r['topic']}")
        for key, label in (
            ("controlni_task_1", "Kontrolni zadatak"),
            ("controlni_task_2", "Kontrolni zadatak"),
            ("controlni_task_3", "Kontrolni zadatak"),
            ("controlni_trick", "Trik"),
            ("controlni_warning", "Upozorenje"),
        ):
            val = normalize_value(r.get(key))
            if val:
                lines.append(f"   • {label}: {val}")
    oblast_block = "\n".join(lines)

    mode_block = (
        "MOD: KONTROLNI IZ OBLASTI (exam)\n"
        "- Učenik sutra ima kontrolni iz CIJELE OBLASTI, ne iz jedne lekcije.\n"
        "- Daj TAČNO 3 zadatka u stilu kontrolnog, IZBALANSIRANO iz RAZLIČITIH "
        "tema ove oblasti (koristi ponuđene kontrolne zadatke kao uzor).\n"
        "- Zatim navedi TAČNO 1 čest trik i TAČNO 1 upozorenje iz ponuđenog "
        "materijala.\n"
        "- Format: zadaci kao numerisana lista 1., 2., 3., zatim red "
        "\"Trik:\" i red \"Upozorenje:\".\n"
        "- Koristi ISKLJUČIVO teme i materijal iz bloka iznad — NE izmišljaj "
        "teme ni sadržaj.\n"
    )
    # Phase 7.2: "može"/"nastavi" usred exam sesije → nastavak od zadnje poruke,
    # ne ponovni ispis 3 zadatka (materijal oblasti ostaje kao kontekst).
    if normalize_value(payload.get("interaction_phase")).lower() == "continuing_explanation":
        mode_block = build_continuation_instructions(payload)

    user_parts = [
        _build_entry_context(payload, "unknown", "exam"),
        oblast_block,
        mode_block,
        _build_student_block(payload),
    ]
    user_prompt = "\n\n".join(p for p in user_parts if p).strip()

    return {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "history_messages": build_history_messages(payload.get("conversation_history")),
        "mode": "exam",
        "final_topic": "unknown",
        "opened_lesson_topic": "unknown",
        "effective_topic": "unknown",
        "status": _STATUS_READY,
        "topic_context_used": False,
        "video_flow_used": False,
        "topic_conflict": False,
        "oblast_context_used": True,
        "exam_oblast": canonical,
    }


def build_fallback_prompt(payload: dict, reason: Any) -> dict:
    """Prompt kada tema NIJE upotrebljiva. ``reason``:
    ``unknown`` → status ``fallback``; ``ambiguous`` → ``ambiguous``;
    ``invalid`` → ``invalid``. Nikad ne izmišlja temu."""
    payload = payload or {}
    reason = normalize_value(reason).lower() or "unknown"
    status = _FALLBACK_STATUS.get(reason, "fallback")
    mode = normalize_mode(payload.get("mode"))

    system_prompt = _compose_system_prompt(payload.get("grade"))

    if reason == "ambiguous":
        ask = (
            "Pronašao sam više sličnih lekcija i ne mogu sa sigurnošću odabrati temu.\n"
            "Zamoli učenika da izabere OBLAST/TEMU koju TRENUTNO radi (ručni izbor)."
        )
    elif reason == "invalid":
        ask = (
            "Tražena tema nije prepoznata kao validna tema iz mastera.\n"
            "NE izmišljaj temu. Zamoli učenika da izabere postojeću oblast/temu ili "
            "pošalje zadatak."
        )
    elif mode == "exam":
        ask = (
            "Tema kontrolnog nije poznata. Pitaj učenika iz koje je OBLASTI/TEME "
            "kontrolni (npr. skupovi, djeljivost, razlomci, decimalni brojevi, "
            "kružnica/uglovi). NE pretpostavljaj temu."
        )
    else:  # unknown
        ask = (
            "Ne mogu automatski prepoznati lekciju/temu.\n"
            "Zamoli učenika da izabere oblast iz liste ili pošalje zadatak, pa "
            "pomozi korak po korak."
        )

    user_prompt = "\n\n".join(
        [_build_entry_context(payload, "unknown", mode), "FALLBACK:\n" + ask]
    ).strip()

    return {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "history_messages": [],   # ne-ready ne zove model; ključ postoji radi oblika
        "mode": mode,
        "final_topic": "unknown",
        "opened_lesson_topic": "unknown",
        "effective_topic": "unknown",
        "status": status,
        "topic_context_used": False,
        "video_flow_used": False,
        "topic_conflict": False,
    }
