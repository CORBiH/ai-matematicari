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

from matbot import config, geometry_rules, geometrycheck, imagecheck, prompts, quick_context
from matbot.llm import LLMError, failure_diagnostics_kv
from matbot.mathcheck import find_numeric_inconsistencies
from matbot.mathsafe import (lacks_meaningful_content,
                             normalize_result_math_transport,
                             sanitize_and_validate_math_text)
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
    # KALIBRACIJA ZA SOL (2026-08-15): raniji uslov „uncertainty_reason mora
    # biti prazan“ bio je pretup — temeljit model istinito napomene i bezazlene
    # stvari o kadru (izrez uz ivicu, susjedni rub stranice), pa je pošten opis
    # ubijao objavu iako je SVAKI potreban matematički simbol bio jasan.
    # Blokira STRUKTURNI signal `math_content_uncertain` (definicija u
    # matbot/schema.py + prompt): nečitljiva cifra, predznak, eksponent,
    # nazivnik, znak nejednakosti ili jedinica → blokada, kao i do sada.
    # Napomena o kadru uz math_content_uncertain=False je informativna (log).
    if out.math_content_uncertain:
        return "math_content_uncertain"
    return None


def _image_gate_message(issue):
    if issue == "multiple_tasks":
        return IMAGE_MULTIPLE_TASKS_MESSAGE
    if issue == "non_math":
        return IMAGE_NON_MATH_MESSAGE
    return IMAGE_UNREADABLE_MESSAGE


# ---------------------------------------------------------------------------
# NAMJERA UČENIKA (v2, 2026-08-16) — mala, deterministička klasifikacija.
#
# „Samo rezultat“ opisuje PODRAZUMIJEVANI oblik odgovora, a ne zabranu. Server
# ovdje NE rješava matematiku i ne pogađa značenje — bira samo (a) ugovor
# prompta, (b) dozvoljenu dužinu odgovora i (c) da li se aktivan zadatak mijenja.
# Sve što nije prepoznato ostaje RESULT, dakle zatečeno ponašanje.
# ---------------------------------------------------------------------------

INTENT_RESULT = "result"
INTENT_EXPLAIN = "explain"
INTENT_VERIFY = "verify"
INTENT_SUBTASK = "subtask"

_EXPLAIN_MARKERS = (
    "objasni", "objasnite", "objasnjenje", "kako si", "kako se", "kako to",
    "kako dobijem", "kako doci", "zasto", "pokazi postupak", "pokazi korake",
    "postupak", "korak po korak", "izvedi", "detaljno", "sta sam pogrijesio",
    "sta sam pogresio", "gdje sam pogrijesio", "drugim nacinom", "drugi nacin",
)
_VERIFY_MARKERS = (
    "provjeri", "provjerite", "je li tacno", "jel tacno", "da li je tacno",
    "je li ovo tacno", "jesam li", "da li sam", "tacno li je", "provjera",
)
# Poruka koja nosi RAČUN (operator, „2x“, razlomak) je zadatak, ne oznaka.
_MATH_CONTENT_RE = re.compile(r"[=+*/^:]|\d\s*[a-zA-Z]|\\frac|\d\s*[-−]\s*\d")


def classify_quick_intent(message, has_context=False):
    """Vrati (namjera, oznaka_podzadatka). Namjerno uska i konzervativna."""
    normalized = _normalized_conversation_phrase(message)
    if not normalized:
        return INTENT_RESULT, ""
    label = quick_context.requested_task_label(message) if has_context else ""
    if any(marker in normalized for marker in _VERIFY_MARKERS):
        return INTENT_VERIFY, label
    if any(marker in normalized for marker in _EXPLAIN_MARKERS):
        return INTENT_EXPLAIN, label
    # Gola oznaka („Treći.“, „pod b)“, „zadatak 3“) mijenja AKTIVAN zadatak.
    # Poruka koja SAMA nosi matematiku nikad nije izbor zadatka — živi nalaz:
    # „2x + 5 = 13“ se folda u tri riječi s ciframa, pa je bio čitan kao izbor
    # zadatka 2 i dobijao odgovor na PRETHODNI zadatak.
    if label and len(normalized.split()) <= 3 and not _MATH_CONTENT_RE.search(message or ""):
        return INTENT_SUBTASK, label
    return INTENT_RESULT, label


def _reply_limit_for(intent):
    """Rezultat ostaje kratak; objašnjenje koje je učenik TRAŽIO ima svoju,
    i dalje čvrstu granicu (nikad esej)."""
    if intent in (INTENT_EXPLAIN, INTENT_VERIFY):
        return config.MAX_QUICK_EXPLANATION_CHARS
    return config.MAX_QUICK_REPLY_CHARS


# NOVI ZADATAK BRIŠE STARI KONTEKST. Uska, izričita najava — dvosmislena poruka
# („a ovaj?“) NE briše kontekst, nego ga koristi.
_NEW_TASK_MARKERS = ("novi zadatak", "nova zadaca", "drugi zadatak",
                     "sljedeci zadatak", "sledeci zadatak", "novo pitanje")


def starts_new_task(message):
    normalized = _normalized_conversation_phrase(message)
    return any(marker in normalized for marker in _NEW_TASK_MARKERS)


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


def run_quick_turn(llm, turn, image=None, context_store=None):
    """turn: očišćeni dict iz api.py. Vraća JSON-spreman dict.

    `image`: matbot.imageinput.ValidatedImage ili None. SAMI BAJTOVI slike važe
    SAMO ZA OVAJ turn — server ih nigdje ne pamti i ne šalje u sljedećim
    pozivima.

    `context_store`: matbot.quick_context.QuickContextStore ili None. Pamti se
    ISKLJUČIVO semantika AKTIVNOG zadatka (tekst zadatka, dati podaci, tražena
    veličina, zadnji rezultat, inventar zadataka sa stranice) — nikad slika.
    Bez storea mod radi tačno kao ranije (stateless), pa stara sesija i restart
    padaju bezbjedno."""
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

    # --- AKTIVAN ZADATAK -----------------------------------------------------
    # Nova slika ili izričita najava novog zadatka BRIŠU stari kontekst; sve
    # ostalo ga koristi. Kontekst nikad ne prelazi granicu sesije ni moda.
    session_id = turn.get("session_id") or ""
    stored_context = None if image is not None else (
        context_store.get(session_id) if context_store else None)
    if stored_context and starts_new_task(student_message):
        stored_context = None
        if context_store:
            context_store.clear(session_id)
    intent, task_label = classify_quick_intent(
        student_message, has_context=bool(stored_context))
    # AKTIVAN ZADATAK SE ŠALJE MODELU SAMO ZA NASTAVAK (objasni/provjeri/izbor).
    # Poruka koja SAMA nosi zadatak je nov zadatak i ne smije vidjeti stari
    # kontekst — živa kampanja, turn 03: poslije „3456 + 2891“ je na „2x + 5 =
    # 13“ stigao odgovor „$3456+2891=6347$“, jer je stari zadatak i dalje bio u
    # promptu. Historija razgovora ostaje, pa referentna pitanja i dalje rade.
    follow_up = intent in (INTENT_EXPLAIN, INTENT_VERIFY, INTENT_SUBTASK)
    if not follow_up:
        stored_context = None
    # Izbor zadatka sa stranice se radi SAMO kad je poruka stvarno izbor
    # („Treći.“). Inače je broj u poruci dio matematike — turn 18: „Zašto si
    # dijelio sa 5/9?“ je čitano kao izbor zadatka 5 i vraćalo poruku o
    # nečitljivoj slici.
    if stored_context and task_label and intent == INTENT_SUBTASK:
        stored_context, status = quick_context.select_task(stored_context, task_label)
        if status == "unreadable":
            # Pošteno: taj zadatak NIJE pouzdano pročitan — ne pogađa se.
            #
            # OBLIK ODGOVORA (usklađeno 2026-08-16): kao SVAKA druga slikovna
            # kapija u ovom fajlu (`_image_gate_message` niže, blokada
            # `image_math_source_missing`) — bez polja `status`. To nije
            # kozmetika: `status` je jedino strukturno polje po kojem frontend
            # zna da je odgovor STVARNO objavljen, pa je ova jedina kapija s
            # „ready“ nudila prečicu „Objasni postupak“ nad zadatkom koji
            # nije ni pročitan. Poruka i zapis konteksta ostaju isti.
            logger.info("quick_turn request_id=%s task_select=unreadable", request_id)
            if context_store:
                context_store.save(stored_context)
            return {"answer": IMAGE_UNREADABLE_MESSAGE, "last_tutor_task": ""}
        if status == "selected" and context_store:
            context_store.save(stored_context)

    instructions = prompts.build_quick_instructions(
        turn["grade"], lesson_title=lesson_title, oblast=oblast,
        repair_intent=repair_intent, image_present=image is not None,
        intent=intent,
    )
    input_text = prompts.build_quick_input(
        lesson_title=lesson_title,
        oblast=oblast,
        history=_clean_history(turn.get("conversation_history")),
        student_message=student_message,
        image_present=image is not None,
        server_default_instruction=server_default_instruction,
        task_context=quick_context.prompt_block(stored_context),
    )

    if image is not None:
        # Ograničeni metapodaci (ValidatedImage.log_metadata) — bez bajtova,
        # bez base64, bez data URL-a, bez EXIF-a, bez imena fajla.
        logger.info("quick_turn request_id=%s image_in %s", request_id, image.log_metadata())

    try:
        result = llm.quick_turn(instructions, input_text, image=image)
        reply_limit = _reply_limit_for(intent)
        if image is not None:
            # SAMO osnovna provjera (neprazan reply) PRIJE kapije čitljivosti.
            # Puna ograničenja internih polja dolaze POSLIJE kapije — živa
            # proba migracije na Sol: na fotografiji CIJELE stranice udžbenika
            # temeljit čitač prijavi evidenciju svih zadataka, pa su dužinske
            # granice obarale odgovor generičkom porukom PRIJE nego što bi
            # kapija rekla ispravno „Na slici vidim više zadataka…“. Kapija
            # čita samo šemom tipizirana polja (Literal/bool), pa joj dužinske
            # granice nisu preduslov.
            validate_quick_output(result.output, max_reply_chars=reply_limit)
        else:
            validate_quick_output(result.output, max_reply_chars=reply_limit)
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
            # STRANICA S VIŠE ZADATAKA: odgovor je pitanje „koji da riješim?",
            # ali ono što je model pročitao se PAMTI — inače sljedeće „Treći."
            # nema odakle da se riješi (živi baseline, turnovi 6 i 7).
            if gate_issue == "multiple_tasks" and context_store and session_id:
                inventory = quick_context.from_image_output(session_id, result.output)
                if inventory.get("detected_tasks"):
                    context_store.save(inventory)
                    logger.info("quick_turn request_id=%s stored_task_inventory=%s",
                                request_id, len(inventory["detected_tasks"]))
            return {"answer": _image_gate_message(gate_issue), "last_tutor_task": ""}

        # Puna ograničenja internih polja evidencije — TEK POSLIJE kapije
        # (vidi komentar uz poziv modela). Prekršaj i dalje pada zatvoreno.
        try:
            validate_quick_image_output(result.output, max_reply_chars=reply_limit)
        except InvalidOutputError as e:
            logger.warning("quick_turn request_id=%s category=invalid_output detail=%s",
                           request_id, e)
            return {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": ""}

        # Deterministička provjera podržanih porodica — TRI RAZLIČITA STANJA
        # (doktrina precizirana 2026-08-15, poslije Sol migracije; živa
        # dijagnostika 4 lažna odbijanja — sva četiri su bila not_engaged
        # spojen s failed):
        #
        #   VERIFIED      (engaged ∧ verified)  → objava smije dalje;
        #   CONTRADICTED  (engaged ∧ ¬verified) → BLOKADA: nezavisan račun je
        #                 DOKAZAO da se odgovor ne slaže s vidljivim dokazom;
        #   NOT_APPLICABLE (¬engaged)           → verifikator NE POKRIVA baš
        #                 ovaj podoblik (pravougaonik iz stranice+dijagonale,
        #                 nejednačina u polju jednačine, miješane jedinice…).
        #                 To NIJE dokaz greške — odgovor ide dalje kroz opšte
        #                 validatore (mathsafe/mathcheck/geometrycheck…), a
        #                 „provjereno“ se NIKAD ne tvrdi.
        #
        # IZUZETAK koji ostaje blokada (tačno D35T-2 rupa): model za porodicu
        # izraza/jednačine NIJE dao nikakav dokazni zapis (prazan ili
        # naslovni `visible_math`) iako kapija čitljivosti tvrdi da je sve
        # vidljivo — kontradikcija vlastitih tvrdnji, nikad objava.
        verification = imagecheck.verify_image_answer(result.output)
        if verification.supported and not verification.may_publish:
            blocking = (
                verification.engaged                       # CONTRADICTED
                or verification.code == "image_math_source_missing"
            )
            logger.warning(
                "quick_turn request_id=%s category=%s detail=image_check "
                "task_type=%s engaged=%s verified=%s code=%s",
                request_id,
                "invalid_output" if blocking else "image_check_not_applicable",
                result.output.task_type, verification.engaged,
                verification.verified, verification.code,
            )
            if blocking:
                return {"answer": IMAGE_UNREADABLE_MESSAGE, "last_tutor_task": ""}
            # NOT_APPLICABLE: nastavi kroz opšte validatore ispod.

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

    # PRAZAN VIDLJIV ODGOVOR NIKAD NE IZLAZI KAO USPJEH (živi nalaz P0-2,
    # finalna prijemna kampanja e767cac, turn D08). Šema odbija prazan `reply`,
    # ali gleda SIROVI izlaz modela — `$$` je za nju neprazan string, a
    # sanitizacija ga svede na `""` i pri tom kaže `is_safe=True`. Rezultat je
    # bio objavljen `{"status":"ready","answer":""}`: učenik vidi „Nema
    # odgovora.“, a prečica „Objasni postupak“ se svejedno iscrta jer je status
    # `ready`. Provjera je NAMJERNO na finalnom tekstu (poslije transporta,
    # sanitizacije i terminologije), pa hvata i prazne delimitere i izlaz koji
    # nosi samo razmačne komande — ne samo doslovni `$$`. Bez drugog poziva
    # modela: isti kanonski sigurni odgovor kao svako drugo odbijanje.
    # Prošireno (živi nalaz): odgovor NE SMIJE biti ni prazan ni bezsadržajan.
    # Produkcija je objavila doslovno `$:$` — nije prazno, pa je prošlo prvu
    # provjeru, a učeniku ne znači ništa. Mjeri se sadržaj, ne dužina, pa
    # kratki tačni odgovori (`0`, `$x=4$`, `$\pi$`, `12`) ostaju netaknuti.
    if lacks_meaningful_content(answer):
        logger.warning("quick_turn request_id=%s category=empty_visible_answer", request_id)
        return {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": ""}

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

    # AKTIVAN ZADATAK poslije uspješnog odgovora. Slika daje bogat kontekst
    # (pročitani zadatak + dati podaci); tekstualni zadatak pamti sam sebe, a
    # follow-up (objasni/zašto/provjeri) samo osvježava zadnji rezultat.
    if context_store and session_id:
        if image is not None:
            # Nova slika UVIJEK zamjenjuje aktivan zadatak.
            fresh = quick_context.from_image_output(session_id, result.output)
        elif follow_up and stored_context:
            # Nastavak radi nad ISTIM zadatkom — osvježava se samo rezultat.
            fresh = stored_context
        else:
            # Sve ostalo je nov zadatak i briše stari kontekst.
            fresh = quick_context.from_text_task(session_id, student_message)
        context_store.save(quick_context.with_result(fresh, answer))

    logger.info(
        "quick_turn request_id=%s ok intent=%s context=%s latency_ms=%s usage=%s",
        request_id, intent, "yes" if stored_context else "no",
        result.latency_ms, result.usage,
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
