"""Orkestracija JEDNOG Quick turna („Samo rezultat“): prompt → jedan AI poziv →
validacija → sanitizacija → response u frontend ugovoru.

Namjerno STATELESS na serveru, kao Explain: Quick nema aktivni zadatak, hint
nivo, streak, opcije ni session store. Kontekst je najviše 3 kratke prethodne
razmjene koje frontend već šalje. Greška AI poziva vraća kratku sigurnu poruku
BEZ 'status' i BEZ 'next_state' (frontend tada čuva svoje stanje — isti
mehanizam kao practice/explain).
"""
import logging
import re
import unicodedata
import uuid

from matbot import geometry_rules, geometrycheck, imagecheck, prompts
from matbot.llm import LLMError, failure_diagnostics_kv
from matbot.mathcheck import find_numeric_inconsistencies
from matbot.mathsafe import normalize_result_math_transport, sanitize_and_validate_math_text
from matbot.practice import SAFE_ERROR_MESSAGE
from matbot.rules import OFF_TOPIC_ANSWER
from matbot.schema import (
    InvalidOutputError,
    validate_quick_image_output,
    validate_quick_output,
)
from matbot.terminology import normalize_terminology
from matbot.topics import lesson_info

logger = logging.getLogger("matbot.quick")

MAX_HISTORY_MESSAGES = 6  # 3 razmjene (učenik + tutor) — isto ograničenje kao Explain

_REPAIR_MESSAGE_PREFIXES = (
    "sta pricas",
    "ne razumijem",
    "nije mi jasno",
    "kakve to veze ima",
    "to nisam pitao",
    "sta to znaci",
)
_REPAIR_STANDALONE_MESSAGES = ("pojasni", "pojasni mi")
_REPAIR_ACKNOWLEDGEMENT_PREFIXES = (
    "izvini",
    "izvinjavam se",
    "oprosti",
    "zao mi je",
    "u pravu si",
    "nisam bio jasan",
    "nisam bila jasna",
    "nisam se jasno izrazio",
    "nisam se jasno izrazila",
)
REPAIR_ACKNOWLEDGEMENT = "Izvini — prethodni odgovor nije bio dovoljno jasan. "


def _normalized_conversation_phrase(value):
    """Lowercase/diacritics/punctuation normalization for narrow prose intents."""
    folded = unicodedata.normalize("NFKD", value or "")
    without_marks = "".join(char for char in folded if not unicodedata.combining(char))
    words_only = re.sub(r"[^\w\s]", " ", without_marks.lower(), flags=re.UNICODE)
    return " ".join(words_only.split())


def is_conversational_repair_message(message: str) -> bool:
    """Recognize only a small allowlist of Bosnian confusion/repair messages."""
    normalized = _normalized_conversation_phrase(message)
    return normalized in _REPAIR_STANDALONE_MESSAGES or any(
        normalized == phrase or normalized.startswith(phrase + " ")
        for phrase in _REPAIR_MESSAGE_PREFIXES
    )


# ---------------------------------------------------------------------------
# DIREKTNO PITANJE O SATU (živi nalaz D35-4, poziv 28 kampanje od 35)
# ---------------------------------------------------------------------------
# Na „Sastanak je u 12:30. Koliko je sati?“ model je vratio DOSLOVNO
# rules.OFF_TOPIC_ANSWER — primijenio je zajedničko pravilo „poruka je potpuno
# van matematike“ na sasvim običnо pitanje o vremenu. Aplikacija je ispravno
# odbila da „12:30“ protumači kao dijeljenje, ali je učenik dobio odbijanje
# umjesto odgovora.
#
# Opseg je NAMJERNO uzak: Quick ne postaje opšti asistent. Prepoznaje se samo
# direktno pitanje o satu koje sadrži ISPRAVAN HH:MM (sati 00-23, minute 00-59)
# I jednu od nekoliko fiksnih fraza o vremenu. „27:90“ nije validno vrijeme;
# „60:15“ u računu ostaje dijeljenje; dvotačka u URL-u ili u prozi se ne dira.
_CLOCK_TIME_RE = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
_CLOCK_QUESTION_PHRASES = (
    "koliko je sati",
    "koliko je sad sati",
    "u koliko sati",
    "koje je vrijeme",
    "koje je vreme",
    "vrijeme je",
    "vreme je",
    "na satu",
)


def direct_clock_time_question(message):
    """Vrati „HH:MM“ kad je poruka direktno pitanje o vremenu na satu, inače
    None. Traži se I validno vrijeme I fiksna fraza — nijedno samo za sebe nije
    dovoljno, da račun poput „$60:15=4$“ nikad ne bi bio protumačen kao sat."""
    text = message or ""
    match = _CLOCK_TIME_RE.search(text)
    if not match:
        return None
    normalized = _normalized_conversation_phrase(text)
    if not any(phrase in normalized for phrase in _CLOCK_QUESTION_PHRASES):
        return None
    return "%s:%s" % (match.group(1), match.group(2))


def _is_bare_math_wrapped_time(reply, clock_time):
    """True kad je CIO odgovor samo prepoznato vrijeme umotano u $...$ — npr.
    „$12:30$“. Uže od toga ne može biti: bilo kakva dodatna rečenica znači da je
    model stvarno nešto objasnio i njegov odgovor se ne dira."""
    stripped = (reply or "").strip().rstrip(".")
    return stripped in ("$%s$" % clock_time, "$$%s$$" % clock_time)


def clock_time_answer(clock_time):
    """Serverski odgovor za prepoznato pitanje o satu. Namjerno brojčan (bez
    tablica brojeva riječima) — deterministički tačan za svaki validan HH:MM."""
    hours, minutes = clock_time.split(":")
    return "Vrijeme je %s (%d sati i %d minuta)." % (clock_time, int(hours), int(minutes))


def _begins_with_repair_acknowledgement(reply: str) -> bool:
    normalized = _normalized_conversation_phrase(reply)
    return any(
        normalized == phrase or normalized.startswith(phrase + " ")
        for phrase in _REPAIR_ACKNOWLEDGEMENT_PREFIXES
    )


# ---------------------------------------------------------------------------
# STROGA KAPIJA ČITLJIVOSTI SLIKE (živi nalaz D35-6, poziv 35 kampanje od 35)
# ---------------------------------------------------------------------------
# Na slici jednačine desna strana je bila NAMJERNO prekrivena; model je ipak
# vratio „$x=5$“ — broj koji na slici ne postoji. Prompt je već tada zabranjivao
# pogađanje, ali prompt nije garancija.
#
# Zato odgovor sa slike stiže do učenika SAMO kad model sam prijavi da je sve
# potrebno bilo jasno vidljivo i da je siguran. Svaka druga kombinacija znači da
# se predloženi matematički odgovor ODBACUJE — ne popravlja se, ne traži se
# ponovo (nema drugog poziva modela ni OCR-a) — i vraća se kratka serverska
# poruka.
IMAGE_UNREADABLE_MESSAGE = (
    "Ne mogu pouzdano pročitati sve potrebne vrijednosti sa slike. "
    "Pošalji jasniju sliku ili prepiši zadatak."
)
IMAGE_MULTIPLE_TASKS_MESSAGE = (
    "Na slici vidim više zadataka. Napiši koji da riješim."
)
IMAGE_NON_MATH_MESSAGE = (
    "Na slici ne vidim matematički zadatak. Pošalji sliku zadatka ili ga prepiši."
)


def _image_gate_issue(out):
    """Vrati interni kod razloga zašto odgovor sa slike NE smije do učenika,
    ili None kad su svi uslovi ispunjeni."""
    if out.readability == "multiple_tasks":
        return "multiple_tasks"
    if out.readability == "non_math":
        return "non_math"
    if out.readability != "clear":
        return "not_clear"
    if not out.all_required_symbols_visible:
        return "missing_required_symbol"
    if out.answer_confidence != "high":
        return "low_confidence"
    if (out.uncertainty_reason or "").strip():
        return "uncertainty_reported"
    return None


def _image_gate_message(issue):
    if issue == "multiple_tasks":
        return IMAGE_MULTIPLE_TASKS_MESSAGE
    if issue == "non_math":
        return IMAGE_NON_MATH_MESSAGE
    return IMAGE_UNREADABLE_MESSAGE


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


def run_quick_turn(llm, turn, image=None):
    """turn: očišćeni dict iz api.py. Vraća JSON-spreman dict.

    `image`: matbot.imageinput.ValidatedImage ili None. Slika VAŽI SAMO ZA OVAJ
    turn — server je nigdje ne pamti, ne upisuje u historiju i ne šalje u
    sljedećim pozivima. Ako kasnija poruka ponovo treba sliku, učenik je mora
    priložiti iznova (frontend čisti prilog nakon uspješnog odgovora)."""
    request_id = uuid.uuid4().hex[:12]

    # Canonical podaci iz topics.json — samo kao mekan kontekst (ne ograničenje).
    lesson = lesson_info(turn["grade"], turn["selected_topic"])
    lesson_id = lesson["id"] if lesson else (turn["selected_topic"] or "")
    lesson_title = lesson["title"] if lesson else ""
    oblast = lesson["oblast"] if lesson else (turn["selected_oblast"] or "")

    # Slika bez teksta: instrukciju postavlja SERVER (vidi prompts). Klijent je
    # ne diktira, i u promptu je jasno označena kao aplikacijska, ne kao
    # rečenica učenika.
    student_message = turn["student_message"]
    server_default_instruction = bool(image is not None and not student_message)
    if server_default_instruction:
        student_message = prompts.QUICK_IMAGE_DEFAULT_INSTRUCTION

    repair_intent = is_conversational_repair_message(student_message)
    clock_time = direct_clock_time_question(student_message)
    instructions = prompts.build_quick_instructions(
        turn["grade"], lesson_title=lesson_title, oblast=oblast,
        repair_intent=repair_intent, image_present=image is not None,
    )
    input_text = prompts.build_quick_input(
        lesson_title=lesson_title,
        oblast=oblast,
        history=_clean_history(turn.get("conversation_history")),
        student_message=student_message,
        image_present=image is not None,
        server_default_instruction=server_default_instruction,
    )

    if image is not None:
        # Ograničeni metapodaci (ValidatedImage.log_metadata) — bez bajtova,
        # bez base64, bez data URL-a, bez EXIF-a, bez imena fajla.
        logger.info("quick_turn request_id=%s image_in %s", request_id, image.log_metadata())

    try:
        result = llm.quick_turn(instructions, input_text, image=image)
        if image is not None:
            validate_quick_image_output(result.output)
        else:
            validate_quick_output(result.output)
    except LLMError as e:
        logger.warning(
            "quick_turn request_id=%s category=%s mode=quick %s",
            request_id, e.category, failure_diagnostics_kv(e),
        )
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
    # STROGA KAPIJA ČITLJIVOSTI + nezavisna provjera računa za sliku. Oboje
    # radi na INTERNIM poljima koja nikad ne izlaze iz servera.
    if image is not None:
        gate_issue = _image_gate_issue(result.output)
        if gate_issue is not None:
            logger.info(
                "quick_turn request_id=%s image_gate=%s readability=%s task_type=%s confidence=%s",
                request_id, gate_issue, result.output.readability,
                result.output.task_type, result.output.answer_confidence,
            )
            return {"answer": _image_gate_message(gate_issue), "last_tutor_task": ""}

        # Deterministička provjera podržanih porodica. Za podržanu porodicu
        # objavljivanje traži supported ∧ engaged ∧ verified — „provjera se nije
        # izvršila“ NIKAD ne znači „provjereno“ (živi nalaz D35T-2). Nepodržana
        # porodica ide dalje kroz opšte provjere, uz istu strogu kapiju
        # čitljivosti — i dalje bez ijednog dodatnog poziva modela.
        verification = imagecheck.verify_image_answer(result.output)
        if verification.supported and not verification.may_publish:
            logger.warning(
                "quick_turn request_id=%s category=invalid_output detail=image_check "
                "task_type=%s engaged=%s verified=%s code=%s",
                request_id, result.output.task_type, verification.engaged,
                verification.verified, verification.code,
            )
            return {"answer": IMAGE_UNREADABLE_MESSAGE, "last_tutor_task": ""}

    raw_reply = result.output.reply.strip()

    # Serverski fallback POSLIJE jedinog poziva (nikad drugi poziv): kad je
    # poruka prepoznata kao direktno pitanje o satu, a model je vratio ili
    # generičko odbijanje „nije matematika“ ili GOLO vrijeme umotano u $...$,
    # odgovor sastavlja server. Drugi slučaj je živi nalaz kampanje od 14
    # poziva: model je vratio „$12:30$“, a unutar matematike „:“ je u ovom
    # projektu oznaka za dijeljenje (matbot/rules.py), pa taj zapis učeniku
    # sugeriše „12 podijeljeno sa 30“ umjesto vremena.
    if clock_time and (raw_reply.strip() == OFF_TOPIC_ANSWER
                       or _is_bare_math_wrapped_time(raw_reply, clock_time)):
        logger.info("quick_turn request_id=%s clock_time_fallback", request_id)
        raw_reply = clock_time_answer(clock_time)

    if repair_intent and not _begins_with_repair_acknowledgement(raw_reply):
        raw_reply = REPAIR_ACKNOWLEDGEMENT + raw_reply
    transported, transport_safe = normalize_result_math_transport(raw_reply)
    answer, is_safe = sanitize_and_validate_math_text(transported)
    if not transport_safe or not is_safe:
        logger.warning("quick_turn request_id=%s category=unsafe_math_output", request_id)
        return {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": ""}
    answer = normalize_terminology(answer)

    # Numerička dosljednost lanca jednakosti (matbot/mathcheck.py). Quick vraća
    # gotov rezultat, pa je pogrešan račun ovdje najvidljiviji učeniku.
    numeric_issues = find_numeric_inconsistencies(answer)
    if numeric_issues:
        logger.warning("quick_turn request_id=%s category=invalid_output detail=%s",
                       request_id, numeric_issues[0])
        return {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": ""}

    # Geometrijska notacija (matbot/geometrycheck.py). Quick vraća GOTOV
    # rezultat, pa je pogrešna oznaka ovdje najvidljivija učeniku. Opseg i
    # figure dolaze iz CANONICAL lekcije — kad lekcija nije izabrana (Quick to
    # dozvoljava), opseg je prazan i provjera se preskače umjesto da se pogađa.
    geometry_scope, geometry_figures = geometry_rules.route_geometry_topic(oblast, lesson_title)
    geometry_issues = geometrycheck.find_geometry_issues(answer, geometry_scope, geometry_figures)
    if geometry_issues:
        logger.warning("quick_turn request_id=%s category=invalid_output detail=geometry_notation:%s",
                       request_id, ",".join(geometry_issues))
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
