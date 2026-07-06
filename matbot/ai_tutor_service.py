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
import re
from typing import Any, Callable

from matbot.activity_log import log_student_activity
from matbot.answer_checker import (
    check_practice_answer,
    detect_referenced_items,
    summarize_result,
)
from matbot.bosnian import to_ijekavica
from matbot.content_loader import (
    get_master,
    get_thinkific_map,
    normalize_grade,
    normalize_value,
)
from matbot.image_result_verifier import (
    augment_saved_image_context,
    correction_preface_from_context,
    extract_image_tasks,
    format_image_verification_for_context,
    ocr_from_saved_context,
    verify_image_result_answer,
)
from matbot.prompt_builder import (
    build_exam_oblast_prompt,
    build_general_tutor_prompt,
    build_tutor_prompt,
    get_topic_context,
)
from matbot.topic_detector import detect_topic, fold_diacritics, is_vague_message
from matbot.topic_lookup import get_final_topic

log = logging.getLogger("matbot.ai_tutor")

# --- Phase 2 (audit): slika — kada OCR NIJE dovoljan pa treba i Vision ------------
# Mathpix vraća (text, confidence). Za geometriju/dijagrame tekst je često
# nepotpun (labele bez figure), pa slika ide modelu ZAJEDNO sa OCR tekstom.
OCR_CONFIDENCE_MIN = 0.75
OCR_MIN_CHARS = 12

_GEOMETRY_HINTS_RE = None  # lijeno kompajlirano u _looks_geometric


def _looks_geometric(text: str) -> bool:
    """Heuristika: tekst (poruka + OCR + tema) upućuje na geometriju/dijagram."""
    global _GEOMETRY_HINTS_RE
    if _GEOMETRY_HINTS_RE is None:
        import re
        _GEOMETRY_HINTS_RE = re.compile(
            r"troug|cetveroug|cetvoroug|paralelogram|trapez|romb|kvadrat|"
            r"pravougaonik|\bug(ao|la|lu|lovi|love|lova)\b|uglomjer|kruznic|"
            r"\bkrug\b|precnik|poluprecnik|tetiv|tangent|simetri|vektor|"
            r"konstrukcij|izometrij|translacij|rotacij|nacrta|skic|dijagram|"
            r"koordinat|\bgraf|povrsin|\bobim\b|slika prikazuje|na slici"
        )
    return bool(_GEOMETRY_HINTS_RE.search(fold_diacritics(text)))


def _image_needs_vision(ocr_text: str, ocr_conf: float, probe_text: str) -> bool:
    """True ako sliku treba poslati modelu i pored OCR teksta.

    - bez OCR teksta → uvijek Vision (postojeće ponašanje);
    - nizak confidence ili prekratak tekst → OCR je vjerovatno nepotpun;
    - geometrijski signal (poruka/OCR/tema) → figura nosi informaciju koju
      tekst nema.
    Čist tekstualni zadatak sa sigurnim OCR-om ostaje tekstualni (jeftinije i
    brže — bez Visiona)."""
    if not ocr_text:
        return True
    if ocr_conf < OCR_CONFIDENCE_MIN:
        return True
    if len(ocr_text.strip()) < OCR_MIN_CHARS:
        return True
    return _looks_geometric(probe_text)

DEFAULT_GRADE = 6
DEFAULT_MODEL = "gpt-5-mini"

# --- Phase 6: sigurnosni limiti ulaza (bez lomljenja normalne upotrebe) ----------
MAX_MESSAGE_CHARS = 4000
MAX_HISTORY_ITEMS = 5
MAX_HISTORY_ITEM_CHARS = 1500
MAX_LAST_TASK_CHARS = 1000
MAX_IMAGE_CONTEXT_CHARS = 2000
# Audit: anti-ponavljanje zadataka — frontend šalje zadnje date zadatke.
MAX_RECENT_TASKS = 6
MAX_RECENT_TASK_CHARS = 300

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

_NEXT_EXPECTED_ACTIONS = {
    "answer_task",
    "continue_confirmation",
    "choose_next",
    "ask_followup",
    "none",
}
_PENDING_ACTION_TYPES = {
    "continue_image_test",
    "generate_similar_task",
    "explain_task",
}
_PENDING_ACTION_SOURCES = {
    "image_context",
    "practice",
    "general",
    "current_task",
}
_ACTIVE_TASK_KINDS = {
    "practice",
    "image_test",
    "explanation",
}

_SHORT_AFFIRMATIVE_RE = re.compile(
    r"^(da|moze|mozes|nastavi|hajde|ajde|ok|okej|u\s?redu|yes)[\s.!?]*$"
)
_SHORT_NEGATIVE_RE = re.compile(
    r"^(ne|nemoj|stani|dosta|no)[\s.!?]*$"
)
_YES_NO_TASK_RE = re.compile(
    r"\b(da\s+li|je\s+li|jesu\s+li|tacno\s+ili\s+netacno|"
    r"odgovori\s+(?:sa\s+)?da\s+ili\s+ne|da/ne)\b"
)

_IMAGE_FOLLOWUP_RE = re.compile(
    r"\b("
    r"slik\w*|zadat\w*|zadac\w*|pitanj\w*|rezultat\w*|postupak|"
    r"prv\w*|drug\w*|trec\w*|cetvrt\w*|pet\w*|"
    r"kako\s+si|urad\w*|rijesi\w*|dobio|dobila|objasni|ne\s+razumijem|"
    r"korak|nastav\w*|dalje|sljedec\w*|sve"
    r")\b|\b\d{1,2}\s*[.)]"
)


def _is_image_followup_message(text: Any) -> bool:
    """Follow-up koji se prirodno poziva na prethodni zadatak sa slike."""
    folded = fold_diacritics(text)
    return bool(_IMAGE_FOLLOWUP_RE.search(folded))


# --- Eksplicitna namjera učenika (stil + obim) — nadjačava UI mod -------------------
# State-driven kontrakt: namjera se čita iz PORUKE UČENIKA (ne iz odgovora
# modela) i primjenjuje PRIJE rutiranja teme/prompta.

_STYLE_STEP_RE = re.compile(
    r"korak\s+po\s+korak|objasni\s+(?:mi\s+)?postupak|postupak\s+rjesavanja|"
    r"rijesi\s+detaljno|detaljno\s+(?:objasni|rijesi|uradi)|s[av]\s+postupkom"
)
_STYLE_RESULT_RE = re.compile(
    r"samo\s+rezultat|samo\s+rjesenj\w*|samo\s+odgovor\w*|bez\s+postupka"
)
_SOLVE_ALL_RE = re.compile(
    r"(?:uradi|rijesi|izracunaj|zavrsi)\w*\s+(?:mi\s+)?(?:i\s+)?"
    r"(?:sve|svaki|cijeli|kompletn\w*)|sve\s+zadatke|cijeli\s+test"
)


def detect_explicit_intent(text: Any) -> dict:
    """{"style": "step_by_step"|"result_only"|None, "solve_all": bool}."""
    folded = fold_diacritics(text)
    style = None
    if _STYLE_STEP_RE.search(folded):
        style = "step_by_step"
    elif _STYLE_RESULT_RE.search(folded):
        style = "result_only"
    return {"style": style, "solve_all": bool(_SOLVE_ALL_RE.search(folded))}


def _apply_explicit_intent(payload: dict) -> None:
    """Postavi ``explicit_style``/``solve_all`` i po potrebi nadjačaj UI mod.

    Radi na ORIGINALNOJ poruci (prije confirmation-rewrite-a). NE dira mod
    kada je poruka odgovor na zadatak (ocjenjivanje) ili eksplicitni intent."""
    if normalize_value(payload.get("intent")):
        return
    phase = normalize_value(payload.get("interaction_phase")).lower()
    if phase == "answering_practice_task":
        return
    intent = detect_explicit_intent(
        payload.get("student_message") or payload.get("message")
    )
    if intent["style"]:
        payload["explicit_style"] = intent["style"]
    if intent["solve_all"]:
        payload["solve_all"] = True
    mode = normalize_value(payload.get("mode")).lower()
    # "korak po korak" u modu Rezultat → objašnjenje; "samo rezultat" → quick.
    if intent["style"] == "step_by_step" and mode in ("quick", "rezultat", "samo_rezultat", "brzo"):
        payload["mode"] = "explain"
    elif intent["style"] == "result_only":
        payload["mode"] = "quick"


def _empty_pending_action() -> dict:
    return {"type": None, "source": None, "next_item": None}


def _empty_next_state() -> dict:
    return {
        "expected_user_action": "none",
        "pending_action": _empty_pending_action(),
        "active_task_kind": None,
        "image_test": None,
    }


# Oznaka stavke sa slike: "3" (int) ili pod-oznaka poput "5.c" (string).
_ITEM_LABEL_RE = re.compile(r"^\d{1,3}(?:\.[a-zčć0-9])?$")


def _normalize_next_item(value: Any) -> int | str | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        item = int(value)
        return item if 0 < item < 1000 else None
    except (TypeError, ValueError):
        label = normalize_value(value).lower()
        return label if _ITEM_LABEL_RE.fullmatch(label) else None


def _item_out(label: str) -> int | str:
    """Kanonski oblik oznake za response: čisti broj → int, "5.c" → string."""
    return int(label) if str(label).isdigit() else str(label)


def _normalize_image_test(raw: Any) -> dict | None:
    """Validiraj ``image_test`` pod-stanje iz klijenta; None = nevalidno/nema."""
    if not isinstance(raw, dict):
        return None
    labels = [
        normalize_value(x).lower()[:8]
        for x in (raw.get("item_labels") or [])
        if normalize_value(x)
    ][:20]
    labels = [l for l in labels if _ITEM_LABEL_RE.fullmatch(l)]
    if not labels:
        return None
    solved = [
        normalize_value(x).lower()[:8]
        for x in (raw.get("solved") or [])
        if normalize_value(x)
    ][:20]
    solved = [s for s in solved if s in labels]
    next_item = _normalize_next_item(raw.get("next_item"))
    style = normalize_value(raw.get("style")).lower()
    return {
        "item_labels": labels,
        "solved": solved,
        "next_item": next_item,
        "style": style if style in ("step_by_step", "result_only") else None,
    }


def _normalize_pending_action(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return _empty_pending_action()
    action_type = normalize_value(raw.get("type")).lower()
    source = normalize_value(raw.get("source")).lower()
    return {
        "type": action_type if action_type in _PENDING_ACTION_TYPES else None,
        "source": source if source in _PENDING_ACTION_SOURCES else None,
        "next_item": _normalize_next_item(raw.get("next_item")),
    }


def _has_pending_action(action: dict | None) -> bool:
    return bool(action and action.get("type"))


def _normalize_next_state(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return _empty_next_state()
    expected = normalize_value(raw.get("expected_user_action")).lower()
    active = normalize_value(raw.get("active_task_kind")).lower()
    return {
        "expected_user_action": expected if expected in _NEXT_EXPECTED_ACTIONS else "none",
        "pending_action": _normalize_pending_action(raw.get("pending_action")),
        "active_task_kind": active if active in _ACTIVE_TASK_KINDS else None,
        # image_test pod-stanje putuje kroz klijenta netaknuto (state-driven tok)
        "image_test": _normalize_image_test(raw.get("image_test")),
    }


def _previous_next_state(payload: dict) -> dict:
    return _normalize_next_state(
        payload.get("previous_next_state") or payload.get("tutor_state")
    )


def _pending_action_from_payload(payload: dict) -> dict:
    pending = _normalize_pending_action(payload.get("pending_action"))
    if _has_pending_action(pending):
        return pending
    return _previous_next_state(payload).get("pending_action") or _empty_pending_action()


def _short_confirmation_kind(text: Any) -> str:
    folded = fold_diacritics(text).strip()
    if _SHORT_AFFIRMATIVE_RE.fullmatch(folded):
        return "affirmative"
    if _SHORT_NEGATIVE_RE.fullmatch(folded):
        return "negative"
    return ""


def _task_allows_yes_no_answer(task: Any) -> bool:
    return bool(_YES_NO_TASK_RE.search(fold_diacritics(task)))


def _confirmation_intent(payload: dict) -> str:
    explicit = normalize_value(payload.get("intent")).lower()
    if explicit in ("continue_confirmation", "decline_confirmation"):
        return explicit

    previous = _previous_next_state(payload)
    if previous.get("expected_user_action") == "continue_confirmation":
        kind = _short_confirmation_kind(
            payload.get("student_message") or payload.get("message")
        )
        if kind == "affirmative":
            return "continue_confirmation"
        if kind == "negative":
            return "decline_confirmation"
    return ""


def _direct_prompt_result(payload: dict) -> dict:
    mode = normalize_value(payload.get("mode")).lower()
    if mode not in _MAX_TOKENS:
        mode = "explain"
    return {
        "system_prompt": "",
        "user_prompt": "",
        "history_messages": [],
        "mode": mode,
        "final_topic": "unknown",
        "opened_lesson_topic": "unknown",
        "effective_topic": "unknown",
        "status": "ready",
        "topic_context_used": False,
        "video_flow_used": False,
        "topic_conflict": False,
    }


def _natural_confirmation_clarifier() -> str:
    return (
        "Može. Samo mi reci šta želiš dalje: da nastavim objašnjenje, "
        "dam sličan zadatak ili da provjerim tvoj konkretan odgovor."
    )


def _decline_confirmation_answer() -> str:
    return (
        "U redu, neću nastaviti taj korak. Napiši mi šta želiš sljedeće: "
        "novi zadatak, objašnjenje ili samo rezultat."
    )


def _rewrite_confirmation_payload(payload: dict, action: dict) -> None:
    action_type = action.get("type")
    next_item = action.get("next_item")
    payload["_skip_answer_check"] = True
    payload["pending_action"] = action

    if action_type == "continue_image_test":
        payload["mode"] = "explain"
        payload["interaction_phase"] = "continuing_explanation"
        if next_item:
            payload["student_message"] = (
                f"Nastavi sa zadatkom {next_item} iz prethodne slike. "
                "Ne ponavljaj prethodno riješeni zadatak; kreni na taj zadatak."
            )
        else:
            payload["student_message"] = (
                "Nastavi sa sljedećim zadatkom iz prethodne slike. "
                "Ne ponavljaj prethodno riješeni zadatak."
            )
        payload.setdefault(
            "last_tutor_message",
            "Tutor je tražio potvrdu za nastavak zadataka sa slike.",
        )
        return

    if action_type == "generate_similar_task":
        payload["mode"] = "practice"
        payload["interaction_phase"] = ""
        payload["student_message"] = (
            "Da, daj mi jedan sličan novi zadatak za vježbu. "
            "Ne ocjenjuj ovu potvrdu kao odgovor."
        )
        return

    if action_type == "explain_task":
        payload["mode"] = "explain"
        payload["interaction_phase"] = "continuing_explanation"
        task = normalize_value(payload.get("last_tutor_task"))
        if task:
            payload["student_message"] = (
                "Objasni prethodni zadatak korak po korak. "
                "Ne ocjenjuj ovu potvrdu kao odgovor.\n\n"
                f"PRETHODNI ZADATAK:\n{task[:600]}"
            )
        else:
            payload["student_message"] = (
                "Objasni prethodni zadatak ili prethodni korak. "
                "Ne ocjenjuj ovu potvrdu kao odgovor."
            )
        payload.setdefault(
            "last_tutor_message",
            "Tutor je tražio potvrdu za dodatno objašnjenje.",
        )


def _apply_confirmation_contract(payload: dict) -> None:
    intent = _confirmation_intent(payload)
    if intent == "decline_confirmation":
        payload["_skip_answer_check"] = True
        payload["intent"] = intent
        payload["pending_action"] = _pending_action_from_payload(payload)
        payload["_direct_answer"] = _decline_confirmation_answer()
        return

    if intent == "continue_confirmation":
        payload["intent"] = intent
        action = _pending_action_from_payload(payload)
        if _has_pending_action(action):
            _rewrite_confirmation_payload(payload, action)
        else:
            payload["_skip_answer_check"] = True
            payload["pending_action"] = action
            payload["_direct_answer"] = _natural_confirmation_clarifier()
        return

    phase = normalize_value(payload.get("interaction_phase")).lower()
    student = payload.get("student_message") or payload.get("message")
    if (
        phase == "answering_practice_task"
        and _short_confirmation_kind(student)
        and not _task_allows_yes_no_answer(payload.get("last_tutor_task"))
    ):
        payload["_skip_answer_check"] = True
        payload["_direct_answer"] = _natural_confirmation_clarifier()


# --- image_test: deterministička mašina stanja za zadatke sa slike ------------------

_CONTINUE_SIGNAL_RE = re.compile(r"\b(nastav\w*|sljedec\w*|dalje|idemo)\b")


def _resolve_image_test_state(payload: dict) -> dict | None:
    """Odredi image_test stanje za OVAJ potez — state-driven, nikad iz proze.

    Izvor zadataka je ISKLJUČIVO OCR (svježa slika ili sačuvani
    ``last_image_context``); odgovor modela se NE parsira za ovu odluku.
    ``None`` = potez nije korak image_test toka (normalan tok netaknut).

    Ulazi u koračanje samo na jasan signal: potvrda ``continue_image_test``,
    eksplicitno "sve zadatke"/"korak po korak" uz sliku, referenca na
    konkretan zadatak ("nastavi na treći zadatak") ili "nastavi" uz
    postojeće image_test stanje."""
    if normalize_value(payload.get("interaction_phase")).lower() == "answering_practice_task":
        return None      # učenik odgovara na practice zadatak — ne otimaj tok

    fresh_ocr = normalize_value(payload.get("image_ocr_text"))
    saved_ctx = normalize_value(payload.get("last_image_context"))
    ocr = fresh_ocr or (ocr_from_saved_context(saved_ctx) if saved_ctx else "")
    if not ocr:
        return None
    items = extract_image_tasks(ocr)
    if len(items) < 2:
        return None      # jedan zadatak sa slike = običan tok, bez koračanja
    labels = [str(it["label"]).lower() for it in items]
    tasks_by_label = {str(it["label"]).lower(): it["task"] for it in items}

    prev = _previous_next_state(payload).get("image_test") or {}
    prev_solved = [s for s in prev.get("solved") or [] if s in labels]
    pending = _pending_action_from_payload(payload)
    style = (
        normalize_value(payload.get("explicit_style")).lower()
        or normalize_value(prev.get("style")).lower()
        or ("result_only"
            if normalize_value(payload.get("mode")).lower() == "quick"
            else "step_by_step")
    )
    if style not in ("step_by_step", "result_only"):
        style = "step_by_step"

    message = normalize_value(payload.get("student_message") or payload.get("message"))
    numeric = [int(l) for l in labels if l.isdigit()]
    refs = detect_referenced_items(message, numeric) if numeric else set()

    current: str | None = None
    if (
        normalize_value(payload.get("intent")).lower() == "continue_confirmation"
        and pending.get("type") == "continue_image_test"
    ):
        pn = pending.get("next_item")
        current = str(pn).lower() if pn is not None else None
    elif payload.get("solve_all") or normalize_value(payload.get("explicit_style")) == "step_by_step":
        current = None                       # → prva neriješena stavka ispod
    elif refs:
        current = str(min(refs))             # "nastavi na treći zadatak" → 3
    elif prev and _CONTINUE_SIGNAL_RE.search(fold_diacritics(message)):
        pn = prev.get("next_item")
        current = str(pn).lower() if pn is not None else None
    else:
        return None

    unsolved = [l for l in labels if l not in prev_solved]
    if current is None or current not in labels:
        current = unsolved[0] if unsolved else None
    if current is None:
        return None                          # sve riješeno — image tok je gotov
    return {
        "labels": labels,
        "solved": prev_solved,
        "current": current,
        "style": style,
        "current_task": normalize_value(tasks_by_label.get(current, ""))[:500],
    }


_TASK_ACTION_RE = re.compile(
    r"\b("
    r"zadatak|izracunaj|rijesi|uporedi|usporedi|poredi|odredi|nadji|"
    r"izaberi|oznaci|nacrtaj|konstruisi|konstruiraj|pomnozi|podijeli|"
    r"saberi|oduzmi|skrati|pretvori|zaokruzi|dopuni|napisi|koristi|"
    r"koliko|koji|koja|koje|da\s+li"
    r")\b"
)
_TASK_SIGNAL_RE = re.compile(
    r"\d|[<>=+\-*/:^]|\b("
    r"nzd|nzs|prethodnik|sljedbenik|skup|ugao|razlom|decimal|"
    r"stepen|procent|prava|duz|tacka|trougao|cetverougao"
    r")\b"
)
_TASK_LABEL_RE = re.compile(r"\bzadatak(?:\s+za\s+vjezbu)?\s*[:.\-]\s*")
_TASK_LABEL_ONLY_RE = re.compile(
    r"^(?:evo\s+)?(?:jedan\s+|mali\s+|sljedeci\s+|slican\s+)?"
    r"(?:zadatak|primjer)\s*:?\s*$"
)
_TASK_CONTINUATION_RE = re.compile(
    r"^(koji|koja|koje|koristi|upisi|napisi|odgovori|objasni|"
    r"zaokruzi|izaberi|pazi)\b"
)


def _clean_task_candidate(text: Any, limit: int = MAX_LAST_TASK_CHARS) -> str:
    """Normalize a visible assistant task without inventing or rewriting it."""
    raw = normalize_value(text).replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for line in raw.splitlines():
        line = re.sub(r"^\s*(?:[-*\u2022]+|\d+[.)])\s*", "", line).strip()
        if line:
            lines.append(line)
    cleaned = "\n".join(lines).strip()
    folded = fold_diacritics(cleaned)
    label = _TASK_LABEL_RE.search(folded)
    if label and label.end() < len(cleaned) - 2:
        cleaned = cleaned[label.end():].strip()
    return cleaned[:limit]


# Prelazne/veznične fraze ("Odlično, idemo na sljedeći zadatak!") NISU zadatak
# i ne smiju nikad postati last_tutor_task. Odbacuju se samo kada nema
# matematičkog signala (pravi zadaci gotovo uvijek imaju broj/operator/pojam).
_TRANSITION_TEXT_RE = re.compile(
    r"^(?:odlicno|super|bravo|sjajno|top|dobro|ok|okej|u\s+redu|hajde|ajmo|"
    r"hajmo|idemo|nastavljamo|nastavimo|nastavi)\b"
)
_CONTINUE_OFFER_RE = re.compile(
    r"\b(?:zelis|hoces|hocemo|da\s+li|mozemo)\b.{0,40}"
    r"\b(?:nastavi\w*|sljedec\w*|dalje|jos)\b"
)


def _looks_like_practice_task_text(text: Any) -> bool:
    folded = fold_diacritics(text)
    if not folded or len(folded) < 4:
        return False
    if _TASK_LABEL_ONLY_RE.fullmatch(folded):
        return False
    if re.search(
        r"\b(zelis|hoces|hocemo|treba|mogu)\b.*"
        r"\b(slican|slicni|novi|nov)\b.*\b(zadatak|zadatke|primjer)\b",
        folded,
    ):
        return False
    has_signal = bool(_TASK_SIGNAL_RE.search(folded))
    if not has_signal and (
        _TRANSITION_TEXT_RE.match(folded) or _CONTINUE_OFFER_RE.search(folded)
    ):
        return False
    has_action = bool(_TASK_ACTION_RE.search(folded))
    return has_action and (has_signal or len(folded.split()) >= 4)


_NUMBERED_TASK_LINE_RE = re.compile(r"^\s*(\d{1,2})[.)]\s+(.+)$")


def _extract_numbered_tasks(raw: str, limit: int) -> str:
    """Audit: exam odgovor sadrži VIŠE numerisanih zadataka (1., 2., 3.) —
    sačuvaj ih SVE sa numeracijom, da provjera odgovora "1) ... 2) ..." zna
    koje stavke postoje (ranije se pamtio samo prvi zadatak)."""
    tasks: list[tuple[int, str]] = []
    for line in raw.splitlines():
        m = _NUMBERED_TASK_LINE_RE.match(line)
        if not m:
            continue
        n, body = int(m.group(1)), m.group(2).strip()
        if _looks_like_practice_task_text(body) and (
            any(ch.isdigit() for ch in body) or body.rstrip().endswith("?")
        ):
            tasks.append((n, body))
    numbers = [n for n, _b in tasks]
    if len(tasks) < 2 or numbers[0] != 1 or any(b <= a for a, b in zip(numbers, numbers[1:])):
        return ""
    return "\n".join(f"{n}. {b}" for n, b in tasks)[:limit]


def extract_practice_task(answer: Any, limit: int = 600, mode: str | None = None) -> str:
    """Best-effort extraction of the exact visible task from an assistant answer.

    The response text remains the source of truth; this only turns the visible
    task into structured metadata so the browser can carry it into the next turn.
    ``mode == "exam"`` prvo pokušava više numerisanih zadataka odjednom.
    """
    raw = normalize_value(answer).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return ""

    if mode == "exam":
        numbered = _extract_numbered_tasks(raw, limit)
        if numbered:
            return numbered

    paragraphs = [
        _clean_task_candidate(p, limit)
        for p in re.split(r"\n\s*\n", raw)
        if normalize_value(p)
    ]
    for paragraph in paragraphs:
        if _looks_like_practice_task_text(paragraph):
            return paragraph[:limit]

    lines = [_clean_task_candidate(line, limit) for line in raw.splitlines()]
    lines = [line for line in lines if line]
    for i, line in enumerate(lines):
        folded = fold_diacritics(line)
        if _TASK_LABEL_ONLY_RE.fullmatch(folded) and i + 1 < len(lines):
            nxt = lines[i + 1]
            if _looks_like_practice_task_text(nxt):
                return nxt[:limit]
        if _looks_like_practice_task_text(line):
            return line[:limit]

    compact = re.sub(r"\s+", " ", raw)
    sentences = re.findall(r"[^.!?]+(?:[.!?]+|$)", compact)
    for i, sentence in enumerate(sentences):
        candidate = _clean_task_candidate(sentence, limit)
        if not _looks_like_practice_task_text(candidate):
            continue
        parts = [candidate]
        for nxt in sentences[i + 1:i + 4]:
            nxt_clean = _clean_task_candidate(nxt, limit)
            folded_next = fold_diacritics(nxt_clean)
            if (
                _TASK_CONTINUATION_RE.search(folded_next)
                or _looks_like_practice_task_text(nxt_clean)
            ):
                parts.append(nxt_clean)
            else:
                break
        return " ".join(parts).strip()[:limit]

    return ""


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
    val = payload.get("last_image_context")
    if isinstance(val, str) and len(val) > MAX_IMAGE_CONTEXT_CHARS:
        payload["last_image_context"] = val[:MAX_IMAGE_CONTEXT_CHARS]
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
    # anti-ponavljanje: lista nedavno datih zadataka (samo stringovi, skraćeno)
    recent = payload.get("recent_tasks")
    if isinstance(recent, list):
        payload["recent_tasks"] = [
            normalize_value(t)[:MAX_RECENT_TASK_CHARS]
            for t in recent[-MAX_RECENT_TASKS:]
            if isinstance(t, str) and normalize_value(t)
        ]
    else:
        payload["recent_tasks"] = []
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


def _prepare_chat(
    data: dict,
    openai_chat: Callable,
    master: dict | None,
    tmap: dict | None,
    *,
    model: str,
    timeout: float | None,
    image_bytes: bytes | None,
    image_data_url: str | None,
    ocr_image: Callable | None,
    vision_model: str | None,
) -> dict:
    """Sve PRIJE glavnog model poziva: sanitizacija, OCR, lookup teme,
    detekcija, prompt, messages. Zajedničko za sync (handle_chat) i
    streaming (handle_chat_stream) put — jedna logika, dva transporta."""
    payload = dict(data or {})
    # Default: grade -> 6 ako nije zadan; grade bira master/map i tutor prompt.
    payload["grade"] = normalize_grade(payload.get("grade") or DEFAULT_GRADE)
    _sanitize_payload(payload)
    # Eksplicitna namjera (stil/obim) se čita iz ORIGINALNE poruke, prije nego
    # što je confirmation contract eventualno zamijeni sintetičkom.
    _apply_explicit_intent(payload)
    _apply_confirmation_contract(payload)

    if payload.get("_direct_answer") is not None:
        master = master if master is not None else get_master(grade=payload["grade"])
        tmap = tmap if tmap is not None else get_thinkific_map(grade=payload["grade"])
        prompt_result = _direct_prompt_result(payload)
        return {
            "payload": payload,
            "master": master,
            "lookup_result": {"status": "found", "source": "intent_contract"},
            "prompt_result": prompt_result,
            "mode": prompt_result["mode"],
            "status": prompt_result["status"],
            "effective_topic": "unknown",
            "topic_context": {},
            "messages": None,
            "use_model": model,
            "direct_answer": payload.get("_direct_answer"),
        }

    student_for_image_ctx = normalize_value(
        payload.get("student_message") or payload.get("message")
    )
    if (
        normalize_value(payload.get("last_image_context"))
        and student_for_image_ctx
        and _is_image_followup_message(student_for_image_ctx)
    ):
        payload["last_image_context"] = augment_saved_image_context(
            payload["last_image_context"]
        )

    # --- Audit: deterministička provjera odgovora PRIJE prompta -----------------
    # Kada je poruka odgovor na prethodni zadatak, kod sam izračuna i uporedi
    # rezultat(e) gdje god može (razlomci, mješoviti brojevi, komplement,
    # pretvaranje, direktan račun). Presuda ulazi u prompt i model je NE SMIJE
    # mijenjati — time nestaje klasa grešaka "tačan odgovor proglašen netačnim".
    if (
        not payload.get("_skip_answer_check")
        and normalize_value(payload.get("interaction_phase")).lower() == "answering_practice_task"
    ):
        _task = normalize_value(payload.get("last_tutor_task"))
        _student = normalize_value(payload.get("student_message") or payload.get("message"))
        if _task and _student:
            payload["answer_check"] = check_practice_answer(_task, _student)

    grade = payload["grade"]
    master = master if master is not None else get_master(grade=grade)
    tmap = tmap if tmap is not None else get_thinkific_map(grade=grade)

    # --- Phase 6.2: slika zadatka — prvo pokušaj OCR (postojeći legacy Mathpix) --
    has_image = bool(image_bytes or image_data_url)
    payload["has_image"] = has_image
    ocr_conf = 0.0
    if has_image and image_bytes is not None and ocr_image is not None:
        try:
            ocr_text, ocr_conf = ocr_image(image_bytes)
        except Exception:
            ocr_text, ocr_conf = None, 0.0
        if ocr_text:
            payload["image_ocr_text"] = normalize_value(ocr_text)[:MAX_MESSAGE_CHARS]

    # --- image_test: deterministički korak kroz zadatke sa slike -----------------
    # Stanje se gradi iz OCR-a + prethodnog next_state (nikad iz proze odgovora);
    # prompt builder ga koristi kao mode blok, _next_state_for_response kao izvor
    # sljedećeg koraka.
    image_test = _resolve_image_test_state(payload)
    if image_test:
        payload["_image_test"] = image_test

    lookup_result = get_final_topic(payload, master, tmap)

    # --- Phase 7: kontrolni za CIJELU OBLAST (selected_oblast bez teme) ---------
    exam_oblast_prompt = None
    if lookup_result["status"] == "unknown":
        exam_oblast_prompt = build_exam_oblast_prompt(payload, master)

    # --- Phase 6: free_chat detekcija teme (heuristike → LLM klasifikator) -------
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
                general_answer = True
        elif has_image and not combined:
            general_answer = True
        elif (
            normalize_value(payload.get("last_image_context"))
            and student_msg
            and _is_image_followup_message(student_msg)
        ):
            general_answer = True

    if exam_oblast_prompt is not None:
        prompt_result = exam_oblast_prompt
    elif general_answer:
        prompt_result = build_general_tutor_prompt(payload)
    else:
        prompt_result = build_tutor_prompt(payload, lookup_result, master, tmap)

    mode = prompt_result["mode"]
    status = prompt_result["status"]
    effective_topic = prompt_result.get("effective_topic") or prompt_result.get(
        "final_topic", "unknown"
    )
    topic_context = get_topic_context(effective_topic, master)

    messages = None
    use_model = model
    if status == "ready":
        # Phase 2 (audit): slika ide modelu kad OCR NIJE dovoljan — bez OCR-a,
        # nizak confidence, prekratak tekst ILI geometrijski/dijagramski signal.
        # Siguran čisto-tekstualni OCR ostaje tekstualni (brže i jeftinije).
        ocr_text_norm = normalize_value(payload.get("image_ocr_text"))
        if image_data_url:
            probe = " ".join(x for x in (
                normalize_value(payload.get("student_message") or payload.get("message")),
                ocr_text_norm,
                topic_context.get("display_name", ""),
                topic_context.get("oblast", ""),
                normalize_value(effective_topic),
            ) if x)
            send_image = _image_needs_vision(ocr_text_norm, ocr_conf, probe)
        else:
            send_image = False

        if send_image:
            user_content: Any = [
                {"type": "text", "text": prompt_result["user_prompt"]},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ]
            use_model = vision_model or model
        else:
            user_content = prompt_result["user_prompt"]
            use_model = model
        # Phase 2: historija kao PRAVE role poruke (system → history → user).
        messages = [{"role": "system", "content": prompt_result["system_prompt"]}]
        messages.extend(prompt_result.get("history_messages") or [])
        messages.append({"role": "user", "content": user_content})

    return {
        "payload": payload,
        "master": master,
        "lookup_result": lookup_result,
        "prompt_result": prompt_result,
        "mode": mode,
        "status": status,
        "effective_topic": effective_topic,
        "topic_context": topic_context,
        "messages": messages,
        "use_model": use_model,
    }


def _make_image_context(payload: dict, answer: str) -> str:
    """Kompaktan tekst koji frontend može vratiti u sljedećem follow-upu."""
    if not (payload.get("has_image") or payload.get("image_ocr_text")):
        return ""
    parts: list[str] = []
    ocr = normalize_value(payload.get("image_ocr_text"))
    if ocr:
        parts.append("TEKST SA SLIKE (OCR):\n" + ocr[:MAX_MESSAGE_CHARS])
    msg = normalize_value(payload.get("student_message") or payload.get("message"))
    if msg:
        parts.append("PORUKA UČENIKA UZ SLIKU:\n" + msg[:500])
    verification = format_image_verification_for_context(
        payload.get("image_result_verification")
    )
    if verification:
        parts.append(verification)
    if answer:
        answer_limit = 800 if verification else 1200
        parts.append("ODGOVOR TUTORA NA SLIKU:\n" + normalize_value(answer)[:answer_limit])
    return "\n\n".join(parts).strip()[:MAX_IMAGE_CONTEXT_CHARS]


def _pending_action_from_answer(payload: dict, answer: str) -> dict:
    if payload.get("_direct_answer") is not None:
        return _empty_pending_action()

    folded = fold_diacritics(answer)
    has_question = "?" in answer or bool(
        re.search(r"\b(zelis|hoces|hocemo|mogu|treba|da\s+li)\b", folded)
    )
    has_image_context = bool(
        payload.get("has_image")
        or normalize_value(payload.get("image_ocr_text"))
        or normalize_value(payload.get("last_image_context"))
    )

    if has_question and has_image_context:
        m = re.search(
            r"\b(?:nastavim|nastavimo|nastaviti|dalje|sljedec\w*)\b"
            r".{0,80}\bzadat\w*\s*(\d{1,3})\b",
            folded,
        )
        if not m:
            m = re.search(
                r"\bzadat\w*\s*(\d{1,3})\b.{0,80}"
                r"\b(?:nastavim|nastavimo|nastaviti|dalje|sljedec\w*)\b",
                folded,
            )
        if m:
            return {
                "type": "continue_image_test",
                "source": "image_context",
                "next_item": _normalize_next_item(m.group(1)),
            }
        if re.search(r"\b(?:nastavim|nastavimo|nastaviti|dalje|sljedec\w*)\b", folded):
            return {
                "type": "continue_image_test",
                "source": "image_context",
                "next_item": None,
            }

    if has_question and re.search(
        r"\b(slican|slicni|novi|nov)\b.{0,80}\b(zadatak|zadatke|primjer)\b",
        folded,
    ):
        return {
            "type": "generate_similar_task",
            "source": "practice",
            "next_item": None,
        }

    if has_question and re.search(
        r"\b(objasnim|objasnjenje|postupak|korak\s+po\s+korak)\b",
        folded,
    ):
        return {
            "type": "explain_task",
            "source": "current_task" if normalize_value(payload.get("last_tutor_task")) else "general",
            "next_item": None,
        }

    return _empty_pending_action()


def _next_state_for_response(
    payload: dict,
    answer: str,
    *,
    mode: str,
    status: str,
    task_text: str,
) -> dict:
    if status != "ready":
        return _empty_next_state()

    # image_test ima APSOLUTNU prednost i računa se iz stanja, ne iz proze:
    # stavka koju smo u OVOM potezu dali modelu postaje "riješena", sljedeća
    # neriješena ide u pending_action.next_item.
    img = payload.get("_image_test")
    if img:
        solved = list(img.get("solved") or [])
        current = img.get("current")
        if current and current not in solved:
            solved.append(current)
        unsolved = [l for l in img.get("labels") or [] if l not in solved]
        if unsolved:
            nxt = unsolved[0]
            return {
                "expected_user_action": "continue_confirmation",
                "pending_action": {
                    "type": "continue_image_test",
                    "source": "image_context",
                    "next_item": _item_out(nxt),
                },
                "active_task_kind": "image_test",
                "image_test": {
                    "item_labels": list(img.get("labels") or []),
                    "solved": solved,
                    "next_item": _item_out(nxt),
                    "style": img.get("style"),
                },
            }
        # sve stavke riješene → image tok završen, dalje normalna logika

    if task_text and mode in ("practice", "exam"):
        return {
            "expected_user_action": "answer_task",
            "pending_action": _empty_pending_action(),
            "active_task_kind": "practice",
            "image_test": None,
        }

    pending = _pending_action_from_answer(payload, answer)
    if _has_pending_action(pending):
        active = {
            "continue_image_test": "image_test",
            "generate_similar_task": "practice",
            "explain_task": "explanation",
        }.get(pending.get("type"))
        return {
            "expected_user_action": "continue_confirmation",
            "pending_action": pending,
            "active_task_kind": active,
            "image_test": None,
        }

    return _empty_next_state()


def _finalize_response(prep: dict, answer: str) -> dict:
    """Sastavi response dict + activity log (zajedničko za oba puta)."""
    payload = prep["payload"]
    prompt_result = prep["prompt_result"]
    mode, status = prep["mode"], prep["status"]

    # Audit: jezička zaštita — vrlo česti ekavski oblici ("deo", "rešenje") →
    # ijekavica. Streaming klijent na kraju ponovo renderuje answer iz "done"
    # događaja, pa ispravka važi i za streamane odgovore.
    answer = to_ijekavica(answer)
    correction_preface = correction_preface_from_context(
        payload.get("last_image_context", "")
    )
    if correction_preface and "Ranije sam pogrešno napisao" not in answer:
        answer = correction_preface + "\n\n" + answer
    image_verification = None
    if status == "ready" and normalize_value(payload.get("image_ocr_text")):
        answer, image_verification = verify_image_result_answer(
            payload.get("image_ocr_text"), answer
        )
        if image_verification:
            payload["image_result_verification"] = image_verification

    entry_source_used = normalize_value(payload.get("entry_source")) or normalize_value(
        prep["lookup_result"].get("source")
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
        "effective_topic": prep["effective_topic"],
        "entry_source_used": entry_source_used,
        "topic_conflict": bool(prompt_result.get("topic_conflict", False)),
        "recommended_mode": _RECOMMENDED_MODE.get(mode, "practice"),
        "recommend_video": bool(prep["topic_context"].get("when_to_recommend_video")),
        "parent_report_signal": parent_report_signal,
        "status": status,
        "mode": mode,
    }
    image_context = _make_image_context(payload, answer)
    if image_context:
        response["image_context"] = image_context
    if image_verification:
        response["image_verification"] = image_verification
    # Tokom image_test toka odgovor NIKAD ne postaje last_tutor_task — aktivni
    # "zadatak" je stavka sa slike i živi u next_state.image_test, ne u prozi.
    task_text = (
        ""
        if payload.get("_direct_answer") is not None or payload.get("_image_test")
        else (extract_practice_task(answer, mode=mode) if status == "ready" else "")
    )
    if task_text:
        response["last_tutor_task"] = task_text
    response["next_state"] = _next_state_for_response(
        payload, answer, mode=mode, status=status, task_text=task_text
    )

    # Audit: sažetak determinističke provjere u response (telemetrija/testovi).
    check = payload.get("answer_check")
    check_summary = summarize_result(check) if check is not None else None
    if check_summary:
        response["answer_check"] = check_summary

    # Phase 5: minimalni activity log — greška NIKAD ne ruši tutor odgovor.
    try:
        log_student_activity(payload, response)
    except Exception:
        pass

    return response


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
    """
    prep = _prepare_chat(
        data, openai_chat, master, tmap,
        model=model, timeout=timeout,
        image_bytes=image_bytes, image_data_url=image_data_url,
        ocr_image=ocr_image, vision_model=vision_model,
    )

    if prep.get("direct_answer") is not None:
        answer = prep["direct_answer"]
    elif prep["status"] == "ready":
        answer = _call_model_with_retry(
            openai_chat, prep["use_model"], prep["messages"], timeout, prep["mode"]
        )
    else:
        # fallback/ambiguous/invalid → NE zovi OpenAI (deterministički bosanski tekst)
        answer = _fallback_answer(prep["lookup_result"], prep["mode"], prep["master"])

    return _finalize_response(prep, answer)


# Student-facing poruka kada se stream prekine prije prvog teksta.
_STREAM_ERROR_ANSWER = (
    "Došlo je do greške u toku odgovora. Pokušaj ponovo za koji trenutak."
)


def handle_chat_stream(
    data: dict,
    openai_chat: Callable,
    openai_chat_stream: Callable,
    master: dict | None = None,
    tmap: dict | None = None,
    *,
    model: str = DEFAULT_MODEL,
    timeout: float | None = None,
    vision_model: str | None = None,
):
    """Phase 2 (audit) — streaming varijanta handle_chat-a (generator događaja).

    Yield-a dictove transport-agnostički (Flask ruta ih serializuje u SSE):
      {"event": "delta", "data": {"delta": str}}   — komad teksta
      {"event": "done",  "data": {...}}            — puni response dict (kao handle_chat)
      {"event": "error", "data": {"detail": str}}  — greška prije ijednog teksta

    ``openai_chat`` služi za NE-streaming pomoćne pozive (LLM klasifikator teme);
    ``openai_chat_stream(model, messages, timeout=, max_tokens=)`` je generator
    tekst-delti. Slike NISU podržane u streaming putu (idu na non-streaming
    endpoint) — v1 ograničenje, dokumentovano.

    Napomena: retry-na-prazan-odgovor iz sync puta ovdje NE postoji (deltas su
    već poslani klijentu); prazan stream vraća prijateljsku poruku u done.
    """
    prep = _prepare_chat(
        data, openai_chat, master, tmap,
        model=model, timeout=timeout,
        image_bytes=None, image_data_url=None,
        ocr_image=None, vision_model=vision_model,
    )

    if prep.get("direct_answer") is not None:
        yield {"event": "done", "data": _finalize_response(prep, prep["direct_answer"])}
        return

    if prep["status"] != "ready":
        answer = _fallback_answer(prep["lookup_result"], prep["mode"], prep["master"])
        yield {"event": "done", "data": _finalize_response(prep, answer)}
        return

    assembled: list[str] = []
    try:
        for delta in openai_chat_stream(
            prep["use_model"], prep["messages"],
            timeout=timeout, max_tokens=_MAX_TOKENS.get(prep["mode"], 700),
        ):
            if delta:
                assembled.append(delta)
                yield {"event": "delta", "data": {"delta": delta}}
    except Exception:
        log.exception("ai_tutor stream: prekid toka (mode=%s)", prep["mode"])
        if not assembled:
            yield {"event": "error", "data": {"detail": _STREAM_ERROR_ANSWER}}
            return
        # djelimičan odgovor je stigao — završi sa onim što imamo

    answer = "".join(assembled).strip()
    if not answer:
        log.warning("ai_tutor stream: prazan odgovor (mode=%s)", prep["mode"])
        answer = _EMPTY_ANSWER_FALLBACK
        yield {"event": "delta", "data": {"delta": answer}}

    yield {"event": "done", "data": _finalize_response(prep, answer)}
