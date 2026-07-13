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
    derive_expected,
    detect_referenced_items,
    extract_task_expressions,
    parse_student_answers,
    split_numbered_items,
    summarize_result,
)
from matbot.bosnian import to_ijekavica
from matbot.grading_guard import authoritative_verdict, enforce_grading_consistency, has_grade_contradiction
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
    build_result_mode_prompt,
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
# BUG 14 (2026-07-10): odgovori za sliku sa više pod-stavki znali su biti
# odsječeni usred rečenice — budžeti podignuti (quick 400→600, ostali +200).
# LIVE nalaz (2026-07-10): gpt-5-mini je reasoning model — reasoning tokeni
# troše dio budžeta pa je 900 (practice) redovno pucao na finish_reason=length
# i radio retry (2 poziva/potez). Baza podignuta da većina poteza bude 1 poziv;
# max_completion_tokens uključuje i reasoning, pa je viša baza jeftinija (manje
# odbačenih odsječenih poziva), ne skuplja.
_MAX_TOKENS = {"quick": 900, "explain": 1400, "practice": 1400, "exam": 1700}
# Retry budžet kad je odgovor prazan/odsječen (finish_reason == "length").
_RETRY_MAX_TOKENS_CAP = 2400

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

# Afirmativna potvrda ponude ("Hoćeš li još jedan zadatak?" → "može"/"daj"/
# "može još jedan"). Jezgro potvrde smije nositi opcioni dodatak "još jedan/
# jednu(-i) [zadatak]" — sve to znači "da, izvrši ponuđeno".
_SHORT_AFFIRMATIVE_RE = re.compile(
    r"^(?:da|moze|mozes|mozemo|nastavi|hajde|ajde|ajmo|ok|okej|u\s?redu|yes|"
    r"daj|naravno|svakako|moze\s+moze|moze\s+da)"
    r"(?:\s+(?:jos\s+)?(?:jedan|jednu|jedno)(?:\s+zadatak\w*)?)?"
    r"[\s.!?]*$"
)
_SHORT_NEGATIVE_RE = re.compile(
    r"^(ne|nemoj|stani|dosta|no)[\s.!?]*$"
)
_YES_NO_TASK_RE = re.compile(
    r"\b(da\s+li|je\s+li|jesu\s+li|tacno\s+ili\s+netacno|"
    r"odgovori\s+(?:sa\s+)?da\s+ili\s+ne|da/ne)\b"
)

# Ponuda novog sličnog zadatka — mora pokriti sve uobičajene formulacije, ne
# samo "sličan/novi zadatak": i "još jedan zadatak", "probamo još jedan",
# "hoćeš li još jedan?". Bez ovoga se ponuda ne upamti kao pending_action pa
# potvrda ("može") ne izvrši ništa (BUG3).
_SIMILAR_TASK_OFFER_RE = re.compile(
    r"\b(?:slic\w+|nov\w*)\b.{0,80}\b(?:zadat\w+|primjer\w*)\b"
    r"|\bjos\s+(?:jedan|jednu|jedno)\b.{0,40}\b(?:zadat\w+|primjer\w*|vjezb\w*)\b"
    r"|\bprob\w+\b.{0,40}\bjos\s+(?:jedan|jednu|jedno)\b"
    r"|\bjos\s+(?:jedan|jednu|jedno)\b[^?]*\?"
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


_PRACTICE_ORDINAL_WORDS = (
    (1, r"prv\w*"),
    (2, r"drug\w*"),
    (3, r"trec\w*"),
    (4, r"cetvrt\w*"),
    (5, r"pet\w*"),
    (6, r"sest\w*"),
    (7, r"sedm\w*"),
    (8, r"osm\w*"),
)
_PRACTICE_HINT_RE = re.compile(
    r"\b(hint|daj\s+hint|savjet|nagovjestaj|pomozi|pomoc)\b"
)
_PRACTICE_EXPLAIN_RE = re.compile(
    r"\b(objasni|pojasni|kako\s+(?:ide|se|da)|postupak|korak\s+po\s+korak|"
    r"zasto|zbog\s+cega|otkud)\b"        # N6: "a zašto..." je pitanje, ne odgovor
)
# N6: pitanja o bodovima/ocjeni ("koliko bi to bilo bodova, jesam prosao") nisu
# odgovor za ocjenjivanje — ranije su dobijala labelu ili ponovljeno rješenje.
_SCORE_QUESTION_RE = re.compile(
    r"\bbod(?:ova|a|ovi)?\b|\bjesam\s+(?:li\s+)?pros(?:ao|la)\b|"
    r"\bkoja\s+(?:bi\s+)?(?:mi\s+)?(?:to\s+)?(?:bila\s+)?ocjena\b|\bkolika\s+ocjena\b"
)
# N2: učenik izričito traži da mu se NE otkrije rješenje.
_NO_SOLUTION_RE = re.compile(
    r"\bnemoj\s+(?:mi\s+)?(?:dat[i]?\s+|reci\s+|napisat[i]?\s+)?rjesenj|"
    r"\bbez\s+rjesenja\b|\bne\s+otkrivaj\b|\bnemoj\s+rijesit\b"
)
_PRACTICE_SOLVE_RE = re.compile(
    r"\b(uradi|rijesi|izracunaj|pokazi|prikazi|daj)\b"
)
_PRACTICE_ANSWER_CLAIM_RE = re.compile(
    r"\b(odgovor\w*|mislim|dobio|dobila|napisao|napisala|jednako|je\s+da)\b|="
)
# Živi nalaz 2026-07-11: frustracija/samokritika bez pokušaja odgovora ("uh
# preteško, mrzim razlomke", "glup sam") padala je u grading pa je dobijala
# labelu "Netačno". Treba je tretirati kao poziv u pomoć (empatija + hint).
_DISTRESS_SIGNAL_RE = re.compile(
    r"\bglup\w*\b|\bpretesk\w*\b|\btesko\s+mi\b|\bmrzim\b|\bne\s+volim\b|"
    r"\bodustaj\w*\b|\bne\s+ide\s+mi\b|\bbezveze\b|\bnesposoban\w*\b|\bblesav\w*\b|"
    r"\bmrsko\b|\bdosadn\w*\b|\bnikad(?:a)?\s+ne(?:\s+cu|cu)?\b|\bbeznadezn\w*\b"
)
# Nejasno pitanje "kolko je to / koji je rezultat" bez odgovora je molba za
# pomoć, ne pokušaj odgovora — ranije ocijenjeno "Tačno" (model ga sam riješi).
_VAGUE_QUESTION_RE = re.compile(
    r"\bkoli?ko\s+je\s+(?:to|ovo|ono)\b"
    r"|\b(?:koji|koja|koje)\s+je\s+(?:rezultat|rjesenj\w*|odgovor)\b"
    r"|\b(?:sta)\s+je\s+(?:rezultat|rjesenj\w*|odgovor|tacno)\b"
)


def _practice_referenced_items(message: Any, valid_numbers: list[int]) -> set[int]:
    refs = set(detect_referenced_items(message, valid_numbers))
    folded = fold_diacritics(message)
    for n, pat in _PRACTICE_ORDINAL_WORDS:
        if n in valid_numbers and re.search(rf"\b{pat}\b", folded):
            refs.add(n)
    for m in re.finditer(
        r"\b(\d{1,2})\s*[.)]?\s*(?:zadat\w*|pitanj\w*|stavk\w*)?\s*\?",
        folded,
    ):
        refs.add(int(m.group(1)))
    return {n for n in refs if n in valid_numbers}


def _has_practice_answer_attempt(message: Any, valid_numbers: list[int]) -> bool:
    mode, answers = parse_student_answers(message)
    if mode == "none":
        return False
    if mode == "numbered":
        return bool(set(answers) & set(valid_numbers or []))
    return mode in ("single", "ordered")


def _select_practice_help_task(task: str, message: Any) -> tuple[int | None, str]:
    items = split_numbered_items(task)
    if not items:
        return None, task
    valid = [n for n, _text in items]
    refs = _practice_referenced_items(message, valid)
    if refs:
        wanted = min(refs)
        by_n = {n: text for n, text in items}
        return wanted, by_n.get(wanted, task)
    return None, task


def _apply_practice_help_contract(payload: dict) -> None:
    """Practice follow-up koji traži pomoć/rješenje nije pokušaj odgovora."""
    if payload.get("_direct_answer") is not None or payload.get("_skip_answer_check"):
        return
    if normalize_value(payload.get("intent")):
        return
    if normalize_value(payload.get("interaction_phase")).lower() != "answering_practice_task":
        return

    task = normalize_value(payload.get("last_tutor_task"))
    message = normalize_value(payload.get("student_message") or payload.get("message"))
    if not task or not message:
        return

    items = split_numbered_items(task)
    valid = [n for n, _text in items] if items else [1]
    folded = fold_diacritics(message)
    refs = _practice_referenced_items(message, valid) if items else set()
    has_answer = _has_practice_answer_attempt(message, valid)
    wants_hint = bool(_PRACTICE_HINT_RE.search(folded))
    wants_explain = bool(_PRACTICE_EXPLAIN_RE.search(folded))
    wants_solve = bool(
        not wants_hint
        and _PRACTICE_SOLVE_RE.search(folded)
        and (refs or not has_answer)
    )
    # BUG (2026-07-10): "ne znam / ne razumijem / ne znam gdje je zapelo" BEZ
    # pokušaja odgovora je signal da je učenik zapeo — NIJE odgovor za ocjenu.
    # Ranije je padao u grading pa je model lupao "Netačno" i ponavljao rješenje.
    is_stuck = bool(_STUCK_SIGNAL_RE.search(folded)) and not has_answer
    # frustracija (#1) i nejasno pitanje (#3): bez pokušaja odgovora → pomoć
    is_distress = bool(_DISTRESS_SIGNAL_RE.search(folded)) and not has_answer
    is_vague_q = bool(_VAGUE_QUESTION_RE.search(folded)) and not has_answer
    # N6: pitanje o bodovima/ocjeni — meta, ne odgovor (broj "100" nije pokušaj)
    is_score_q = bool(_SCORE_QUESTION_RE.search(folded))
    if is_score_q:
        has_answer = False
    terse_ref_request = bool(
        refs
        and not has_answer
        and len(folded) <= 80
        and not _PRACTICE_ANSWER_CLAIM_RE.search(folded)
    )
    if not (wants_hint or wants_explain or wants_solve or terse_ref_request
            or is_stuck or is_distress or is_vague_q or is_score_q):
        return
    if has_answer and not (wants_hint or wants_explain or wants_solve):
        return

    item, help_task = _select_practice_help_task(task, message)
    intent = (
        "hint"
        if (wants_hint or is_stuck or is_distress or is_vague_q or is_score_q)
        and not (wants_explain or wants_solve or terse_ref_request)
        else "solve"
    )
    # N2: "daj hint ali NEMOJ rješenje" — izričita zabrana otkrivanja rezultata
    if _NO_SOLUTION_RE.search(folded):
        payload["_no_solution_requested"] = True
        intent = "hint"
    # N6: pitanje o bodovima/ocjeni ide kao meta-pitanje uz zadatak
    if is_score_q and not (wants_hint or wants_explain or wants_solve):
        payload["_score_question"] = True
    if is_stuck or is_distress:
        # F5: "ne znam"/frustracija i dalje broji kao "zapeo" (video ramp), iako
        # je poruka preusmjerena u help umjesto grading.
        payload["_stuck_help"] = True
    payload["_skip_answer_check"] = True
    payload["_practice_help_intent"] = intent
    payload["_practice_help_task"] = help_task[:600]
    if item:
        payload["_practice_help_item"] = item
    payload["mode"] = "explain"
    payload["interaction_phase"] = "practice_help"
    # bug #2 (2026-07-11): originalnu poruku čuvamo PRIJE nego je prepišemo
    # sintetičkim hint-tekstom — prompt_builder iz nje detektuje frustraciju
    # ("glup sam"/"preteško") i ubacuje istaknutu empatija-direktivu.
    payload["_original_student_message"] = message
    # N6: konceptualno "zašto/zbog čega" pitanje — odgovori NA PITANJE, ne
    # rješavaj zadatak (sintetička "objasni i riješi" poruka bi otkrila rezultat).
    is_why_q = bool(re.search(r"\bzasto\b|\bzbog\s+cega\b|\botkud\b", folded))
    if is_why_q and not (wants_hint or wants_solve or payload.get("_score_question")):
        payload["student_message"] = (
            "Učenik postavlja konceptualno pitanje uz aktivni zadatak:\n"
            f"\"{message[:300]}\"\n"
            "Odgovori kratko i razumljivo NA NJEGOVO PITANJE (zašto pravilo "
            "vrijedi), bez ocjenske labele. NEMOJ riješiti ni otkriti rezultat "
            "aktivnog zadatka — poslije objašnjenja pozovi učenika da ga sam "
            "pokuša.\n\n"
            f"AKTIVNI ZADATAK (kontekst):\n{help_task[:400]}"
        )
        return
    if payload.get("_score_question"):
        # N6: meta-pitanje o bodovima/ocjeni — odgovori na NJEGA, bez ocjenske
        # labele i bez ponavljanja rješenja.
        payload["student_message"] = (
            "Učenik pita koliko bi bodova/koju ocjenu donio njegov dosadašnji rad "
            "i je li prošao. Na osnovu prethodnih poruka (koje su stavke tačne, a "
            "koje ne) kratko i realno procijeni, naglasi da konačnu ocjenu daje "
            "nastavnik, i ohrabri ga za dalje. NE ponavljaj rješenja zadataka i "
            "NE koristi ocjenske labele."
        )
    elif intent == "hint":
        # CLASS 1: hint tipično postavi pod-korak ("koliko je 1/2 s nazivnikom
        # 6?"). Označi potez da sljedeći učenikov odgovor ne ocijenimo kao
        # FINALNI (tačan međukorak ne smije dobiti "Netačno").
        payload["_gave_hint_step"] = True
        payload["student_message"] = (
            "Daj mi jedan kratki hint za ovaj zadatak. "
            "Ne otkrivaj konačan rezultat.\n\n"
            f"ZADATAK:\n{help_task[:600]}"
        )
    else:
        payload["_solution_revealed"] = True
        label = f"{item}. zadatak" if item else "prethodni zadatak"
        payload["student_message"] = (
            f"Objasni i riješi {label}. Ako prikažeš kompletno rješenje, "
            "ne traži od mene da ponovo odgovorim na isti zadatak.\n\n"
            f"ZADATAK:\n{help_task[:600]}"
        )


# --- N5 (2026-07-12): meta pitanja o botu — deterministički topli odgovor -----------
# Djeca sigurno pitaju "jesi li robot", "ko te napravio", "špijuniraš li me".
# Ranije: hladni refusal / lista tema. Sada: kratak prijateljski odgovor bez
# modela, pa nazad na matematiku.

_META_IDENTITY_RE = re.compile(
    r"\bjesi\s+li\s+(?:ti\s+)?(?:pravi\s+)?(?:covjek|ziv\w*|robot|bot|masina|"
    r"program|ai|umjetna)\b|"
    r"\bko\s+te\s+(?:je\s+)?(?:napravio|programirao|stvorio|izmislio)\b|"
    r"\bkako\s+se\s+zoves\b|\bimas\s+li\s+ime\b|"
    r"\bspijuniras\b|\bpratis\s+(?:li\s+)?(?:me|nas)\b|"
    r"\bvidis\s+li\s+sta\s+(?:radim|kucam|gledam)\b|\bsnimas\s+(?:li\s+)?me\b"
)

_META_IDENTITY_ANSWER = (
    "Ja sam AI tutor za matematiku — program, ne čovjek. 🙂 Napravljen sam da ti "
    "pomognem oko zadataka i lekcija. Ne vidim ništa na tvom uređaju niti te "
    "pratim — vidim samo poruke koje mi ovdje pošalješ. Hajmo na matematiku: "
    "šta radimo danas?"
)


def _apply_meta_identity_contract(payload: dict) -> None:
    if payload.get("_direct_answer") is not None:
        return
    if normalize_value(payload.get("intent")):
        return
    if normalize_value(payload.get("interaction_phase")):
        return                                  # usred zadatka → model (bez labele)
    message = fold_diacritics(
        payload.get("student_message") or payload.get("message")
    )
    if message and _META_IDENTITY_RE.search(message):
        payload["_direct_answer"] = _META_IDENTITY_ANSWER


# --- N1 (2026-07-12): UČENIKOV VLASTITI ZADATAK u Vježbi ----------------------------
# Dijete radi SVOJU domaću: "evo prvi zadatak iz knjige: 3/4 + 5/6" ili lista
# "1/2+1/4, 2/3+1/6, ...". Ranije je bot generisao SVOJ "sličan" zadatak pa su
# tačni odgovori na učenikov zadatak dobijali "Netačno". Sada: konkretni izrazi
# iz poruke postaju AKTIVNI zadatak (last_tutor_task + task_items za više njih),
# a prompt vodi učenika kroz NJEGOV zadatak.

def _apply_student_task_contract(payload: dict) -> None:
    if payload.get("_direct_answer") is not None or payload.get("_skip_answer_check"):
        return
    if normalize_value(payload.get("intent")):
        return
    if normalize_value(payload.get("interaction_phase")):
        return                                    # odgovori/potvrde/nastavci — ne
    if normalize_value(payload.get("mode")).lower() not in ("practice", "vjezba"):
        return
    message = normalize_value(payload.get("student_message") or payload.get("message"))
    if not message:
        return
    exprs = extract_task_expressions(message)
    if not exprs:
        return
    pretty = [e.replace("*", "·") for e in exprs]
    if len(pretty) == 1:
        task = f"Izračunaj: {pretty[0]}"
    else:
        task = "\n".join(f"{i}. Izračunaj: {e}" for i, e in enumerate(pretty, 1))
    payload["_student_task"] = task[:600]


# --- Zahtjev za NOVI (teži/lakši) zadatak — BUG 6/8 (2026-07-10) --------------------
# "zadatak", "novi zadatak", "daj mi teži", "lakši" tokom vježbe NISU odgovor za
# ocjenjivanje niti molba za objašnjenje starog zadatka — učenik traži NOVI
# zadatak. Ranije je "zadatak" išao na re-grade, a "daj mi teži" u help contract
# (re-rješavanje starog zadatka).

_NEW_TASK_WORD_RE = re.compile(
    r"\b(zadatak|zadatke|zadacic\w*|primjer)\b|\bjos\s+jedn?(?:an|u|o)\b"
)
_DIFF_HARDER_RE = re.compile(r"\btez\w*\b")
_DIFF_EASIER_RE = re.compile(r"\blaks\w*\b")
# CLASS 2 (2026-07-12): težinski zahtjev u PRIRODNOJ rečenici ("to je previše
# lagano daj mi teže", "ovo mi je prelagano", "hoću izazov"). Ranije je
# detekcija zahtijevala da SVE riječi budu filler pa je ovakva poruka propadala
# modelu, koji bi onda riješio svoj zadatak umjesto da da teži.
_DIFF_HARDER_STRONG_RE = re.compile(
    r"\bprelagan\w*|\bprelak\w*|\bprevise\s+lagan\w*|\bprevise\s+lak\w*|"
    r"\bpre\s?lagan\w*|\bizazov\w*|\bkomplikovanij\w*"
)
_DIFF_EASIER_STRONG_RE = re.compile(
    r"\blaks\w*|\blaganij\w*|\bjednostavnij\w*|\bpretesk\w*|\bpretez\w*|\bprekompl\w*"
)
_DIFF_HARDER_WEAK_RE = re.compile(r"\btez\w*")
# Poruka koja imenuje DRUGU (konkretnu) temu/oblast — tada NE preusmjeravaj kao
# čist "teži zadatak iz iste teme" (izgubila bi se tema); pusti normalan tok.
_NAMES_OTHER_TOPIC_RE = re.compile(
    r"\b(razlom\w*|procen\w*|postot\w*|ugl\w*|ugao|uglov\w*|geometr\w*|"
    r"jednacin\w*|nejednacin\w*|decimal\w*|djeljiv\w*|deljiv\w*|kruzn\w*|"
    r"povrsin\w*|obim\w*|razmjer\w*|koordinat\w*|skupov\w*|mnozenj\w*|"
    r"dijeljenj\w*|sabiranj\w*|oduzimanj\w*|stepen\w*|prostih\s+broj\w*)\b"
)


def _detect_difficulty_adjustment(folded: str) -> str | None:
    """"harder"|"easier"|None iz slobodne rečenice (redoslijed bitan: "prelagano"
    sadrži "lagan", a "pretesko" sadrži "tesk" — jaki obrasci se provjeravaju
    prije slabih)."""
    if _DIFF_HARDER_STRONG_RE.search(folded):
        return "harder"
    if _DIFF_EASIER_STRONG_RE.search(folded):
        return "easier"
    if _DIFF_HARDER_WEAK_RE.search(folded):
        return "harder"
    return None


# N6 (2026-07-12): "isti" maknut iz blokera — "daj jos jedan isti takav" je
# zahtjev za NOVIM zadatkom iste težine (ponavljanje i dalje blokira "ponovi").
_NEW_TASK_BLOCKER_RE = re.compile(
    r"\b(objasni|pojasni|ponovi|hint|pomoc|pomozi|kako|zasto|"
    r"rijesi|uradi|pokazi|prikazi|provjeri|odgovor\w*)\b|[=?]|\d"
)


# Riječi koje smiju činiti čist zahtjev za novim zadatkom — sve OSTALO znači da
# poruka nosi dodatni sadržaj (npr. temu: "daj mi zadatke sa razlomcima") i NE
# smije se prepisati sintetičkom porukom (izgubila bi se tema).
_NEW_TASK_FILLER = frozenset({
    "daj", "mi", "jos", "jedan", "jednu", "jedno", "novi", "nov", "novu",
    "drugi", "drugu", "sljedeci", "sljedecu", "slican", "slicni", "slicnu",
    "zadatak", "zadatke", "primjer", "primjere", "za", "vjezbu", "vjezba",
    "molim", "te", "mozes", "moze", "mozemo", "hocu", "zelim", "trebam",
    "malo", "sada", "sad", "idemo", "hajde", "ajde", "tezi", "teze", "tezu",
    "laksi", "lakse", "laksu", "iz", "iste", "teme",
    # N6: "isti takav" = nov zadatak iste vrste/težine
    "isti", "istu", "isto", "takav", "takvu", "takvo", "ovakav", "ovakvu",
    # N12: "samo jos jedan pa idem (spavati)" — najava kraja NE poništava
    # zahtjev za još jednim zadatkom.
    "samo", "pa", "onda", "idem", "moram", "ici", "spavat", "spavati",
    "kuci", "gotov", "gotovo", "zavrsavam", "kraj", "zadnji", "posljednji",
})


def detect_new_task_request(text: Any) -> str | None:
    """"harder" | "easier" | "same" kada je poruka ČIST zahtjev za novim
    zadatkom; None inače. Konzervativno: kratka poruka bez brojeva, odgovora,
    objašnjenja i bez dodatnog sadržaja (teme)."""
    folded = fold_diacritics(text).strip()
    if not folded or len(folded) > 80:
        return None
    if _NEW_TASK_BLOCKER_RE.search(folded):
        return None
    # CLASS 2: težinski zahtjev važi i u prirodnoj rečenici ("to je previše
    # lagano daj mi teže") — SAMO ako poruka ne imenuje DRUGU temu (tada tema
    # ima prednost pa ide normalnim tokom).
    diff = _detect_difficulty_adjustment(folded)
    if diff and not _NAMES_OTHER_TOPIC_RE.search(folded):
        return diff
    if not _NEW_TASK_WORD_RE.search(folded):
        return None
    words = re.findall(r"[a-z]+", folded)
    if any(w not in _NEW_TASK_FILLER for w in words):
        return None                         # nosi temu/sadržaj — normalan tok
    return "same"


def _apply_new_task_intent(payload: dict) -> None:
    """Preusmjeri zahtjev za novim zadatkom PRIJE ocjenjivanja i help contract-a.

    Radi SAMO u kontekstu vježbe/kontrolnog (mod ili aktivna practice faza) —
    u Objašnjenju "još jedan primjer" ostaje objašnjenje, a prelazak u Vježbu
    radi UI eksplicitno."""
    if payload.get("_direct_answer") is not None or payload.get("_skip_answer_check"):
        return
    if normalize_value(payload.get("intent")):
        return
    mode_l = normalize_value(payload.get("mode")).lower()
    phase = normalize_value(payload.get("interaction_phase")).lower()
    if mode_l not in ("practice", "vjezba", "exam", "kontrolni") and phase != "answering_practice_task":
        return
    diff = detect_new_task_request(
        payload.get("student_message") or payload.get("message")
    )
    if diff is None:
        return
    payload["_skip_answer_check"] = True
    payload["intent"] = "new_task_request"
    # Kontrolni sesija zadržava exam (novi set zadataka); sve ostalo → practice.
    if normalize_value(payload.get("mode")).lower() not in ("exam", "kontrolni"):
        payload["mode"] = "practice"
    payload["interaction_phase"] = ""
    if diff in ("harder", "easier"):
        payload["_difficulty_hint"] = diff
    extra = {
        "harder": " Neka bude malo TEŽI od prethodnog (veći brojevi ili korak više).",
        "easier": " Neka bude malo LAKŠI od prethodnog.",
    }.get(diff, "")
    payload["student_message"] = (
        "Daj mi jedan novi zadatak iz iste teme za vježbu." + extra +
        " Ne ocjenjuj ovu poruku kao odgovor."
    )


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


_FRESH_EXAM_PREP_RE = re.compile(r"\b(kontroln\w*|test\w*|priprem\w*)\b")


# N8 (2026-07-13): "objasni mi X" u Vježbi BEZ aktivne answer-faze = zahtjev za
# OBJAŠNJENJEM — potez ide kao explain, pa proza objašnjenja ne postaje
# last_tutor_task (ranije: objašnjenje ušlo u task state → sljedeći odgovor
# ocjenjivan protiv proze).
_EXPLAIN_REQUEST_RE = re.compile(
    r"^(?:ma\s+|a\s+|pa\s+)?(?:objasni|pojasni)\b|\bobjasni\s+mi\b|\bpojasni\s+mi\b"
)


def _apply_explain_request_contract(payload: dict) -> None:
    if payload.get("_direct_answer") is not None or payload.get("_skip_answer_check"):
        return
    if normalize_value(payload.get("intent")):
        return
    if normalize_value(payload.get("interaction_phase")):
        return                              # answer/help faze imaju svoje contracte
    if normalize_value(payload.get("mode")).lower() not in ("practice", "vjezba", "exam", "kontrolni"):
        return
    message = normalize_value(payload.get("student_message") or payload.get("message"))
    folded = fold_diacritics(message)
    if not _EXPLAIN_REQUEST_RE.search(folded):
        return
    if extract_task_expressions(message):
        return                              # "objasni mi 3/4+5/6" nosi SVOJ zadatak (N1)
    payload["mode"] = "explain"             # prompt-mod; UI session_mode ostaje


def _is_fresh_exam_prep_request(payload: dict) -> bool:
    mode = normalize_value(payload.get("mode")).lower()
    if mode not in ("exam", "kontrolni", "test", "sutra_imam_kontrolni"):
        return False
    if normalize_value(payload.get("interaction_phase")):
        return False
    message = fold_diacritics(
        payload.get("student_message") or payload.get("message")
    )
    return bool(_FRESH_EXAM_PREP_RE.search(message))


def _apply_exam_context_contract(payload: dict, master: dict) -> None:
    """Fresh controlni prep must use the current explicit UI selection.

    Old practice state/history is useful for follow-ups, but it is dangerous for a
    new exam-prep turn: a stale fractions task in last_tutor_task/recent_tasks or
    an old selected_topic can steer the model away from the chips the student sees.
    """
    if not _is_fresh_exam_prep_request(payload):
        return

    selected_topic = normalize_value(payload.get("selected_topic"))
    selected_oblast = normalize_value(payload.get("selected_oblast"))
    if not selected_topic and not selected_oblast:
        return

    # This is a fresh prep request, not an answer to the previous practice task.
    for key in (
        "last_tutor_task",
        "previous_next_state",
        "tutor_state",
        "pending_action",
        "last_tutor_message",
        "detected_topic",
    ):
        payload.pop(key, None)
    payload["recent_tasks"] = []
    payload["conversation_history"] = []

    # If an old selected_topic conflicts with the explicit oblast currently sent
    # by the UI, prefer the current oblast so exam-by-oblast can build the prompt.
    if selected_topic and selected_oblast:
        topic_ctx = get_topic_context(selected_topic, master)
        topic_oblast = normalize_value(topic_ctx.get("oblast")).lower() if topic_ctx else ""
        if not topic_ctx or (topic_oblast and topic_oblast != selected_oblast.lower()):
            log.info(
                "ai_tutor exam context: ignoring stale selected_topic=%s for selected_oblast=%s",
                selected_topic,
                selected_oblast,
            )
            payload["selected_topic"] = ""
            if normalize_value(payload.get("entry_source")) == "manual_topic_choice":
                payload["entry_source"] = "free_chat"


def _empty_pending_action() -> dict:
    return {"type": None, "source": None, "next_item": None}


def _empty_next_state() -> dict:
    return {
        "expected_user_action": "none",
        "pending_action": _empty_pending_action(),
        "active_task_kind": None,
        "image_test": None,
        "stuck_count": 0,
        "correct_streak": 0,
        "task_items": None,
        # CLASS 1 (2026-07-12): prethodni potez je bio hint sa pod-korakom —
        # sljedeći odgovor može biti MEĐUKORAK, ne finalni odgovor.
        "just_hinted": False,
        # N9 (2026-07-14): mikro-zadatak iz OBJAŠNJENJA ("Probaj ti: 3/8 + 2/8?").
        # NAMJERNO odvojen od last_tutor_task — Objašnjenje ne smije postati mod
        # koji prati zadatke (to je bio izvor BUG 3/9 i N8).
        "micro_task": "",
    }


def _normalize_task_items(raw: Any) -> dict | None:
    """Validiraj ``task_items`` pod-stanje (BUG 12): koje stavke višestavkovnog
    zadatka su VEĆ ocijenjene. ``None`` = nema/nevalidno (jednostavan zadatak)."""
    if not isinstance(raw, dict):
        return None
    labels: list[int] = []
    for x in raw.get("labels") or []:
        try:
            n = int(x)
        except (TypeError, ValueError):
            continue
        if 0 < n <= 20 and n not in labels:
            labels.append(n)
    if len(labels) < 2:
        return None
    graded: list[int] = []
    for x in raw.get("graded") or []:
        try:
            n = int(x)
        except (TypeError, ValueError):
            continue
        if n in labels and n not in graded:
            graded.append(n)
    return {"labels": labels, "graded": sorted(graded)}


# F5 (Vježbajmo): koliko je puta zaredom učenik zapeo na istoj temi. Na pragu se
# u promptu aktivira preporuka videa (prompt_builder: payload["_student_stuck"]).
STUCK_THRESHOLD = 2
_STUCK_SIGNAL_RE = re.compile(
    r"\bne\s+znam\b|\bne\s+razumijem\b|\bne\s+kapiram\b|\bne\s+umijem\b|"
    r"\bne\s+mogu\b|\bnemam\s+pojma\b|\bpomozi\b|\bne\s+kontam\b|\bzapeo\b|\bzapela\b"
)


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
    current = _normalize_next_item(raw.get("current"))
    out = {
        "item_labels": labels,
        "solved": solved,
        "next_item": next_item,
        # AUD-01: "practice" = učenik SAM rješava stavke sa slike (jedna po jedna),
        # za razliku od "step_by_step"/"result_only" gdje tutor rješava.
        "style": style if style in ("step_by_step", "result_only", "practice") else None,
    }
    if current is not None and normalize_value(current).lower() in labels:
        out["current"] = normalize_value(current).lower()
    return out


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
    try:
        stuck = int(raw.get("stuck_count") or 0)
    except (TypeError, ValueError):
        stuck = 0
    try:
        streak = int(raw.get("correct_streak") or 0)
    except (TypeError, ValueError):
        streak = 0
    return {
        "expected_user_action": expected if expected in _NEXT_EXPECTED_ACTIONS else "none",
        "pending_action": _normalize_pending_action(raw.get("pending_action")),
        "active_task_kind": active if active in _ACTIVE_TASK_KINDS else None,
        # image_test pod-stanje putuje kroz klijenta netaknuto (state-driven tok)
        "image_test": _normalize_image_test(raw.get("image_test")),
        "stuck_count": max(0, stuck),
        # F-kvalitet: niz tačnih zaredom (ljestvica težine novih zadataka)
        "correct_streak": max(0, streak),
        # BUG 12: stanje višestavkovnog zadatka (koje stavke su već ocijenjene)
        "task_items": _normalize_task_items(raw.get("task_items")),
        # CLASS 1: marker da je prethodni potez bio hint (pod-korak)
        "just_hinted": bool(raw.get("just_hinted")),
        # N9: mikro-zadatak iz objašnjenja (odvojen od last_tutor_task)
        "micro_task": normalize_value(raw.get("micro_task"))[:300],
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
        # BUG 14: Rezultat sesija OSTAJE quick (style result_only) — hardkodirani
        # "explain" je pravio drift Rezultat→Objašnjenje i duge postupke.
        if normalize_value(payload.get("mode")).lower() not in RESULT_MODES:
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
        # Ljestvica težine: poslije niza tačnih novi zadatak ide stepenicu gore.
        if _previous_next_state(payload).get("correct_streak", 0) >= 1:
            payload.setdefault("_difficulty_hint", "harder")
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


def _practice_flow_context(payload: dict) -> bool:
    """Da li je "da" izgovoreno u kontekstu vježbe (aktivni zadatak / practice
    mod)? Tada afirmacija bez upamćene ponude znači "daj mi novi zadatak"."""
    if normalize_value(payload.get("interaction_phase")).lower() == "answering_practice_task":
        return True
    if normalize_value(payload.get("mode")).lower() in ("practice", "vjezba", "exam", "kontrolni"):
        return True
    return bool(normalize_value(payload.get("last_tutor_task")))


_SIMILAR_TASK_ACTION = {
    "type": "generate_similar_task",
    "source": "practice",
    "next_item": None,
}


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
        elif _practice_flow_context(payload):
            # BUG 1/6: "da" u vježbi bez upamćene ponude = "daj novi zadatak" —
            # nikad meta-pitanje (cilj moda je što više zadataka).
            _rewrite_confirmation_payload(payload, dict(_SIMILAR_TASK_ACTION))
        else:
            payload["_skip_answer_check"] = True
            payload["pending_action"] = action
            payload["_direct_answer"] = _natural_confirmation_clarifier()
        return

    phase = normalize_value(payload.get("interaction_phase")).lower()
    student = payload.get("student_message") or payload.get("message")
    if (
        phase == "answering_practice_task"
        and _short_confirmation_kind(student) == "affirmative"
        and not _task_allows_yes_no_answer(payload.get("last_tutor_task"))
    ):
        # Afirmacija umjesto odgovora na zadatak → novi sličan zadatak (BUG 1).
        payload["intent"] = "continue_confirmation"
        _rewrite_confirmation_payload(payload, dict(_SIMILAR_TASK_ACTION))
        return
    if (
        phase == "answering_practice_task"
        and _short_confirmation_kind(student) == "negative"
        and not _task_allows_yes_no_answer(payload.get("last_tutor_task"))
    ):
        payload["_skip_answer_check"] = True
        payload["_direct_answer"] = _decline_confirmation_answer()


# --- Osporavanje ranije ocjene ("pa to sam i odgovorio") ----------------------------
# Učenik tvrdi da je već dao (tačan) odgovor. To NIJE novi zadatak ni novi
# odgovor — sistem PONOVO deterministički provjeri prethodni odgovor na
# prethodni zadatak i, ako je učenik bio u pravu, prizna grešku.

_CHALLENGE_INTENT_RE = re.compile(
    r"\bto\s+sam\s+(?:i\s+)?(?:odgovor\w*|rek\w+|napisa\w+|kaza\w+)"
    r"|\bpa\s+to\s+sam\b"
    r"|\bpa\s+rek\w+\s+sam\b"
    r"|\b(?:rekao|rekla|napisao|napisala|odgovorio|odgovorila|kazao|kazala)\s+sam\b"
    r"|\bisti\s+odgovor\b"
    r"|\bpa\s+to\s+je\s+isto\b"
)
# Broj kako ga je učenik NAPISAO (čuva tačku: "8.45"), za tekst izvinjenja.
_NUM_IN_TEXT_RE = re.compile(r"-?\d+(?:\s+\d+\s*/\s*\d+|\s*/\s*\d+|[.,]\d+)?")


def detect_challenge_intent(text: Any) -> bool:
    """Učenik osporava raniju ocjenu ("pa to sam i odgovorio", "rekao sam da je
    8.45", "pa to je isto"). Ne tretirati kao novi odgovor ni novi zadatak."""
    return bool(_CHALLENGE_INTENT_RE.search(fold_diacritics(text)))


def _first_number_str(text: Any) -> str:
    m = _NUM_IN_TEXT_RE.search(normalize_value(text))
    return m.group(0).strip() if m else ""


def _has_clean_numeric_answer(text: str) -> bool:
    mode, answers = parse_student_answers(text)
    return mode in ("single", "ordered", "numbered") and any(
        t is not None for t in answers.values()
    )


def _previous_answer_text(payload: dict) -> str:
    """Povrati učenikov PRETHODNI odgovor u izvornom obliku (čuva tačku/zarez).

    Redoslijed: broj naveden u samoj poruci osporavanja → eksplicitno polje
    klijenta → zadnji kratki numerički korisnički unos iz historije."""
    msg = normalize_value(payload.get("student_message") or payload.get("message"))
    if _has_clean_numeric_answer(msg):
        num = _first_number_str(msg)
        if num:
            return num
    field = normalize_value(
        payload.get("last_student_answer") or payload.get("previous_student_answer")
    )
    if field:
        return _first_number_str(field) or field
    for item in reversed(payload.get("conversation_history") or []):
        if isinstance(item, dict):
            role = normalize_value(item.get("role")).lower()
            if role and role != "user":
                continue
            content = normalize_value(
                item.get("content") or item.get("text") or item.get("message")
            )
        elif isinstance(item, str):
            content = normalize_value(item)
        else:
            continue
        if content and _has_clean_numeric_answer(content):
            num = _first_number_str(content)
            if num:
                return num
    return ""


def _challenge_apology_answer(prev: str, check) -> str:
    equiv = f" To je isto kao {prev.replace('.', ',')}." if "." in prev else ""
    return (
        f"U pravu si — tvoj odgovor {prev} je tačan.{equiv} "
        "Izvini na ranijoj zabuni."
    )


def _apply_challenge_contract(payload: dict) -> None:
    """Osporavanje → ponovna deterministička provjera prethodnog odgovora."""
    if payload.get("_direct_answer") is not None or payload.get("_skip_answer_check"):
        return
    if normalize_value(payload.get("intent")):
        return
    message = payload.get("student_message") or payload.get("message")
    if not detect_challenge_intent(message):
        return
    task = normalize_value(payload.get("last_tutor_task"))
    if not task:
        return
    prev = _previous_answer_text(payload)
    if not prev:
        return
    check = check_practice_answer(task, prev)
    payload["answer_check"] = check
    payload["_challenge_recheck"] = prev
    if authoritative_verdict(check) == "correct":
        # Siguran, determinističan odgovor: priznaj grešku i potvrdi tačnost;
        # bez novog zadatka i bez modela.
        payload["_skip_answer_check"] = True
        payload["_direct_answer"] = _challenge_apology_answer(prev, check)
        return
    # Netačno / neprovjerivo → ocijeni ponovo kao ocjenjivački potez: model
    # objašnjava razliku uz OBAVEZUJUĆU presudu iz sistema; guard čuva
    # konzistentnost. Nikad se ne generiše novi zadatak.
    payload["interaction_phase"] = "answering_practice_task"
    payload["student_message"] = prev


def _update_stuck_state(payload: dict) -> None:
    """F5: prati koliko puta zaredom je učenik zapeo (Vježbajmo) i na pragu aktivira
    preporuku videa. "Zapeo" = odgovor na practice zadatak je deterministički
    netačan ILI poruka je "ne znam/ne razumijem/pomozi". Tačan odgovor ili nova
    tema resetuju brojač. Prompt builder čita ``_student_stuck``; response nosi
    ``stuck_count`` naprijed kroz next_state."""
    phase = normalize_value(payload.get("interaction_phase")).lower()
    prev_state = _previous_next_state(payload)
    prev = prev_state.get("stuck_count", 0)
    prev_streak = prev_state.get("correct_streak", 0)

    stuck_signal = False
    verdict = ""
    # "ne znam" preusmjeren u help i dalje broji kao zapeo (flag iz help contract-a).
    if payload.get("_stuck_help"):
        stuck_signal = True
    if phase == "answering_practice_task":
        check = payload.get("answer_check")
        if check is not None:
            verdict = authoritative_verdict(check)
        if verdict == "incorrect":
            stuck_signal = True
        student = payload.get("student_message") or payload.get("message")
        if _STUCK_SIGNAL_RE.search(fold_diacritics(student)):
            stuck_signal = True

    new_stuck = prev + 1 if stuck_signal else 0
    payload["_stuck_count"] = new_stuck
    if new_stuck >= STUCK_THRESHOLD:
        payload["_student_stuck"] = True

    # Ljestvica težine: niz tačnih odgovora zaredom raste, netačan/zapeo resetuje.
    if verdict in ("correct", "partial"):
        payload["_correct_streak"] = prev_streak + 1
    elif stuck_signal or verdict == "incorrect":
        payload["_correct_streak"] = 0
    else:
        payload["_correct_streak"] = prev_streak


# --- Result/Quick mod: kontekst-slobodno rješavanje (bez razreda/teme/lekcije) -----

RESULT_MODES = {"quick", "rezultat", "samo_rezultat", "brzo"}

# Množina ("daj rezultate/sve zadatke") → riješi sve; jednina ("rezultat
# zadatka") bez broja + više zadataka na slici → pitaj koji broj.
_WANTS_ALL_RESULTS_RE = re.compile(r"\b(sve|svih|svaki|sva|rezultate|rezultati)\b")
_WANTS_SINGLE_RESULT_RE = re.compile(r"\brezultat\b")


def is_result_mode(payload: dict) -> bool:
    """Da li je ovaj potez Result/Quick mod (rješenje bez teme/razreda konteksta)?

    Ne primjenjuje se kada je poruka odgovor na practice zadatak, potvrda ili
    nastavak — tada mod nije rezultatski (npr. UI je poslao practice)."""
    if payload.get("_direct_answer") is not None or payload.get("_skip_answer_check"):
        return False
    phase = normalize_value(payload.get("interaction_phase")).lower()
    if phase in ("answering_practice_task", "practice_help", "continuing_explanation"):
        return False
    return normalize_value(payload.get("mode")).lower() in RESULT_MODES


def _result_ocr(payload: dict) -> str:
    """OCR tekst za result-mod odluku: svježa slika ili sačuvani image kontekst."""
    fresh = normalize_value(payload.get("image_ocr_text"))
    if fresh:
        return fresh
    saved = normalize_value(payload.get("last_image_context"))
    return ocr_from_saved_context(saved) if saved else ""


def _fmt_result_value(expected) -> str:
    """Kratak prikaz determinističkog rezultata stavke (broj / nejednakost)."""
    val = expected.value
    if getattr(expected, "kind", "") == "inequality" and expected.required_form:
        num = str(val.numerator) if val.denominator == 1 else f"{val.numerator}/{val.denominator}"
        return f"x {expected.required_form} {num}"
    base = str(val.numerator) if val.denominator == 1 else f"{val.numerator}/{val.denominator}"
    return f"{base} {expected.unit}".strip() if expected.unit else base


def _duplicate_task_numbering(items: list[dict]) -> bool:
    """CLASS 3 (2026-07-12): True kada OCR sadrži RESTART numeracije — npr. dvije
    strane/lista testa (1..5, pa opet 1..5). Tada se labeli sudaraju (dict
    ``tasks_by_label`` kolabira na zadnji), pa "prvi" postaje dvosmislen i
    sadržaj se miješa između listova (prijavljeni bug: test_mat.webp)."""
    nums = [int(it["label"]) for it in items if str(it.get("label", "")).isdigit()]
    return any(nums[i] <= nums[i - 1] for i in range(1, len(nums)))


def _group_task_sheets(items: list[dict]) -> list[list[dict]]:
    """Razdvoji stavke na LISTOVE: novi list počinje kad numeracija krene ispočetka
    (broj <= prethodnom). Slovne pod-stavke ostaju uz svoj list."""
    sheets: list[list[dict]] = []
    current: list[dict] = []
    last_num: int | None = None
    for it in items:
        label = str(it.get("label", ""))
        if label.isdigit():
            n = int(label)
            if last_num is not None and n <= last_num and current:
                sheets.append(current)
                current = []
            last_num = n
        current.append(it)
    if current:
        sheets.append(current)
    return sheets


# "prvi list", "s druge strane", "drugi set", "drugi papir"
_SHEET_REF_RE = re.compile(
    r"\b(prv\w*|drug\w*|gornj\w*|donj\w*)\s+(list\w*|stran\w*|set\w*|papir\w*|dio|dijel\w*)\b"
    r"|\b(list\w*|stran\w*|set\w*)\s+(prv\w*|drug\w*|jedan|dva|1|2)\b"
)
_SHEET_FIRST_RE = re.compile(r"\b(prv\w*|gornj\w*|jedan|1)\b")
_SHEET_SECOND_RE = re.compile(r"\b(drug\w*|donj\w*|dva|2)\b")


def _detect_sheet_ref(folded: str, sheet_count: int) -> int | None:
    """Indeks lista (0-based) iz poruke tipa 'prvi list' / 's druge strane'."""
    m = _SHEET_REF_RE.search(folded)
    if not m:
        return None
    span = m.group(0)
    if _SHEET_SECOND_RE.search(span):
        idx = 1
    elif _SHEET_FIRST_RE.search(span):
        idx = 0
    else:
        return None
    return idx if idx < sheet_count else None


def _duplicate_sets_message(chosen: int | str | None = None) -> str:
    """Poruka kada slika sadrži dva odvojena seta zadataka (dupla numeracija)."""
    if chosen:
        return (
            f"Na slici vidim dva seta zadataka i broj {chosen} se pojavljuje u "
            "oba. Reci mi s kojeg lista ga želiš — npr. \"{0}. zadatak s prvog "
            "lista\" ili \"{0}. zadatak s drugog lista\".".format(chosen)
        )
    return (
        "Izgleda da slika sadrži dva odvojena seta zadataka (numeracija se "
        "ponavlja). Reci mi s kojeg lista i koji broj želiš — npr. \"prvi "
        "zadatak s drugog lista\"."
    )


def _duplicate_sheets_clarification(payload: dict) -> str:
    """CLASS 3: kada slika sadrži dva lista s istom numeracijom, ne pogađaj koji
    zadatak učenik misli — vrati pitanje za razjašnjenje (prazno = normalan tok).

    Vrijedi u SVIM modovima (Vježba/Kontrolni/Rezultat), jer bi inače koračanje
    kroz sliku ili result-selekcija pomiješali listove."""
    ocr = _result_ocr(payload)
    if not ocr:
        return ""
    items = extract_image_tasks(ocr)
    if len(items) < 2 or not _duplicate_task_numbering(items):
        return ""
    message = normalize_value(payload.get("student_message") or payload.get("message"))
    folded = fold_diacritics(message)
    sheets = _group_task_sheets(items)
    if _detect_sheet_ref(folded, len(sheets)) is not None:
        return ""                       # učenik je naveo list — normalan tok
    numeric = [int(it["label"]) for it in items if str(it.get("label", "")).isdigit()]
    refs = detect_referenced_items(message, numeric) if numeric else set()
    chosen = min(refs) if refs else None
    if chosen is not None and numeric.count(chosen) > 1:
        return _duplicate_sets_message(chosen)      # "prvi" postoji na oba lista
    if chosen is None and (
        payload.get("image_ocr_text") or _WANTS_SINGLE_RESULT_RE.search(folded)
    ):
        return _duplicate_sets_message()            # svježa slika / traži rezultat
    return ""


def _multi_task_ask_message(items: list[dict]) -> str:
    """Pitaj koji broj zadatka — BEZ otkrivanja rezultata unaprijed (BUG 11:
    nepozvano "za 2. zadatak mogu dati: -6/7" zbunjuje; samo pitaj broj)."""
    if _duplicate_task_numbering(items):
        return _duplicate_sets_message()
    labels = [normalize_value(it.get("label")) for it in items if normalize_value(it.get("label"))]
    listed = ", ".join(labels[:8])
    if listed:
        return (
            f"Na slici vidim više zadataka ({listed}). "
            "Napiši broj zadatka čiji rezultat želiš."
        )
    return "Na slici ima više zadataka. Napiši broj zadatka čiji rezultat želiš."


def _resolve_result_selection(payload: dict) -> dict | None:
    """Za Result mod sa slikom više zadataka odredi šta raditi.

    Vrati ``None`` (jedan zadatak ili bez slike → normalno riješi), ili:
      {"action": "ask", "message": str, "count": int}         — pitaj koji broj
      {"action": "solve", "item": int|str, "count": int}      — riješi tu stavku
    """
    ocr = _result_ocr(payload)
    if not ocr:
        return None
    items = extract_image_tasks(ocr)
    count = len(items)
    if count < 2:
        return None
    payload["_image_result_available"] = any(
        derive_expected(normalize_value(it.get("task"))) is not None for it in items
    )
    numeric = [int(it["label"]) for it in items if str(it.get("label", "")).isdigit()]
    message = normalize_value(payload.get("student_message") or payload.get("message"))
    folded = fold_diacritics(message)
    dup = _duplicate_task_numbering(items)
    refs = detect_referenced_items(message, numeric) if numeric else set()
    if dup:
        # CLASS 3: slika sadrži dva lista (numeracija se ponavlja). Broj sam po
        # sebi je dvosmislen — treba i LIST. Kad znamo oboje, modelu šaljemo
        # TAČAN TEKST zadatka (ne samo broj) da se listovi ne mogu pomiješati.
        sheets = _group_task_sheets(items)
        sheet_idx = _detect_sheet_ref(folded, len(sheets))
        chosen = min(refs) if refs else None
        if sheet_idx is not None and chosen is not None:
            for it in sheets[sheet_idx]:
                if str(it.get("label", "")) == str(chosen):
                    payload["_result_solve_task"] = normalize_value(it.get("task"))[:600]
                    return {"action": "solve", "item": chosen, "count": count}
        if chosen is not None and numeric.count(chosen) > 1:
            return {"action": "ask", "message": _duplicate_sets_message(chosen), "count": count}
        if chosen is None and _WANTS_SINGLE_RESULT_RE.search(folded):
            return {"action": "ask", "message": _duplicate_sets_message(), "count": count}
    if refs:
        return {"action": "solve", "item": min(refs), "count": count}
    # "rezultate/sve zadatke" (množina) → riješi sve (normalan tok, ne pitaj).
    if _WANTS_ALL_RESULTS_RE.search(folded):
        return None
    # "rezultat zadatka" (jednina) bez broja + više zadataka → pitaj koji broj.
    if _WANTS_SINGLE_RESULT_RE.search(folded):
        return {"action": "ask", "message": _multi_task_ask_message(items), "count": count}
    # AUD-07 (B3): SVJEŽA slika sa ≥2 zadatka + generička/prazna poruka (bez
    # broja, bez vlastitog zadatka) → deterministički pitaj koji broj, umjesto
    # da model povremeno riješi SVE. Poruka s brojevima/zadatkom ide normalno.
    if (
        payload.get("image_ocr_text")
        and len(folded) <= 60
        and not re.search(r"\d", folded)
    ):
        return {"action": "ask", "message": _multi_task_ask_message(items), "count": count}
    return None


# --- image_test: deterministička mašina stanja za zadatke sa slike ------------------

_CONTINUE_SIGNAL_RE = re.compile(r"\b(nastav\w*|sljedec\w*|dalje|idemo)\b")
# Zahtjev da se PRETHODNO objašnjenje ponovi jednostavnije/drugačije — NE prelazi
# na sljedeći zadatak (dugme "Objasni jednostavnije" i sl.).
_REEXPLAIN_SIMPLER_RE = re.compile(
    r"\b(jednostavnij\w*|pojednostav\w*|jednostavnije|prostij\w*|"
    r"lakse|razumljivij\w*|jos\s+jednom|opet\s+objasni|ponovo\s+objasni|"
    r"ne\s+razumijem\s+objasnjenje)\b|objasni\s+mi\s+to\b"
)


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
    if _duplicate_task_numbering(items):
        # CLASS 3: slika sadrži dva lista s istom numeracijom — labeli se sudaraju
        # (tasks_by_label bi kolabirao na zadnji list) pa bi koračanje miješalo
        # zadatke. Ne koračaj; razjašnjenje ide kroz _duplicate_sheets_clarification.
        return None
    labels = [str(it["label"]).lower() for it in items]
    tasks_by_label = {str(it["label"]).lower(): it["task"] for it in items}

    prev = _previous_next_state(payload).get("image_test") or {}
    prev_solved = [s for s in prev.get("solved") or [] if s in labels]
    pending = _pending_action_from_payload(payload)
    mode_val = normalize_value(payload.get("mode")).lower()
    style = (
        normalize_value(payload.get("explicit_style")).lower()
        or normalize_value(prev.get("style")).lower()
        or ("result_only" if mode_val == "quick"
            # AUD-01: u Vježbi/Kontrolnom učenik sam rješava zadatke sa slike
            else "practice" if mode_val in ("practice", "exam")
            else "step_by_step")
    )
    if style not in ("step_by_step", "result_only", "practice"):
        style = "step_by_step"

    message = normalize_value(payload.get("student_message") or payload.get("message"))
    numeric = [int(l) for l in labels if l.isdigit()]
    refs = detect_referenced_items(message, numeric) if numeric else set()
    reexplain = bool(_REEXPLAIN_SIMPLER_RE.search(fold_diacritics(message)))

    current: str | None = None
    if reexplain and prev_solved and not refs and style != "practice":
        # "Objasni jednostavnije" usred koračanja sa slike: PONOVI zadnji
        # objašnjeni zadatak jednostavnije — NE prelazi na sljedeći.
        current = prev_solved[-1]
        payload["_reexplain_simpler"] = True
    elif (
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
    elif fresh_ocr and not prev and style == "practice":
        # AUD-01: SVJEŽA slika sa ≥2 zadatka u Vježbi/Kontrolnom bez ijednog
        # drugog signala → učenik sam rješava, kreni od prve stavke (umjesto da
        # practice generator izmisli nepovezane zadatke).
        current = None                       # → prva neriješena stavka ispod
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
_TASK_LABEL_RE = re.compile(r"\bzadatak(?:\s+za\s+(?:vjezbu|tebe))?\s*[:.\-]\s*")
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
    # N4 guard: preusmjerenje/refusal nikad nije zadatak ("Postavi mi pitanje...")
    if folded.startswith("postavi mi pitanje"):
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
    koje stavke postoje (ranije se pamtio samo prvi zadatak).

    BUG (2026-07-10): stavka verbalno formulisana (bez cifre i bez "?", npr.
    "2. Predstavi dio kruga ako su obojana tri od osam dijela") je ispadala pa
    su labele bile [1,3] (rupa), a grading je preskakao stavku 2. Popravka:
    numerisana lista se prihvata SAMO ako je UZASTOPNA (1,2,3,…) i tada se
    zadržavaju SVE stavke, uključujući verbalno formulisane."""
    all_lines: list[tuple[int, str]] = []
    for line in raw.splitlines():
        m = _NUMBERED_TASK_LINE_RE.match(line)
        if m:
            all_lines.append((int(m.group(1)), m.group(2).strip()))
    numbers = [n for n, _b in all_lines]
    # mora biti tačno uzastopna lista 1..k (nikad rupa: rupa = nešto je ispalo)
    if len(all_lines) < 2 or numbers != list(range(1, len(all_lines) + 1)):
        return ""
    # bar polovina stavki mora ličiti na zadatak (inače to nije spisak zadataka)
    task_like = sum(
        1 for _n, b in all_lines
        if _looks_like_practice_task_text(b)
        or any(ch.isdigit() for ch in b)
        or b.rstrip().endswith("?")
    )
    if task_like * 2 < len(all_lines):
        return ""
    return "\n".join(f"{n}. {b}" for n, b in all_lines)[:limit]


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

    # bug #4 (2026-07-11): eksplicitni "Zadatak:" marker je pouzdaniji od
    # paragraf-heuristike (koja preferira trailing imperativ). Prompti ga traže.
    marked = _extract_marker_paragraph(raw, limit)
    if marked:
        return marked

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


# BUG 1 (2026-07-10): na potezu OCJENJIVANJA riješeni izraz iz objašnjenja se znao
# prepoznati kao "novi zadatak" pa je ponuda ("Želiš li još jedan?") gubila
# pending_action, a sljedeće "da" padalo u meta-pitanje. Na grading potezu se zato
# novi zadatak prihvata ISKLJUČIVO uz eksplicitni "Zadatak:" marker (prompt
# instrukcije ga traže od modela).
# Prihvata i uvodne fraze koje model povremeno napiše umjesto čistog "Zadatak:"
# ("Evo novi zadatak za tebe:", "Sljedeći zadatak:", "Još jedan zadatak:") — inače
# se novi zadatak izgubi pa se naredni odgovor ocijeni protiv PRETHODNOG zadatka.
_TASK_MARKER_LINE_RE = re.compile(
    r"(?m)^[ \t]*(?:evo(?:\s+ti)?\s+)?(?:novi|novo|sljedec\w*|jos\s+jedan|drugi)?\s*"
    r"zadatak(?:\s+za\s+(?:tebe|vjezbu))?\s*[:\-—]"
)


def _extract_marker_paragraph(raw: Any, limit: int = 600) -> str:
    """Kad postoji eksplicitni "Zadatak:" red, uzmi tekst POSLJEDNJEg markera
    ograničen na NJEGOV paragraf (do prve prazne linije).

    2026-07-11 (bug #4): trailing meta-uputa u zasebnom paragrafu ("Riješi
    zadatak i napiši svoje odgovore.") znala je biti izabrana umjesto samog
    zadatka jer paragraf-heuristika preferira imperativ. Pošto prompti sada
    OBAVEZUJU format "Zadatak: ...", marker je pouzdaniji izvor."""
    raw = normalize_value(raw).replace("\r\n", "\n").replace("\r", "\n")
    if not raw:
        return ""
    folded = fold_diacritics(raw)
    if len(folded) != len(raw):
        folded = raw.lower()
    last = None
    for m in _TASK_MARKER_LINE_RE.finditer(folded):
        last = m
    if last is None:
        return ""
    segment = raw[last.start():]
    para = re.split(r"\n\s*\n", segment, maxsplit=1)[0]
    cleaned = _clean_task_candidate(para, limit)
    if not cleaned or len(cleaned) < 8:
        return ""
    # Model je EKSPLICITNO označio ovo kao "Zadatak:" — vjerujemo labeli i ne
    # tražimo strogu proznu heuristiku (_looks_like_practice_task_text je
    # podešen za NEoznačenu prozu i propušta zadatke s glagolima "navedi/
    # zapiši/koliki" kojih nema u action-regexu). Dovoljan je math-signal ili
    # smislena dužina; odbaci samo čiste prelazne/ponudne fraze.
    folded_clean = fold_diacritics(cleaned)
    if _TRANSITION_TEXT_RE.match(folded_clean) or _CONTINUE_OFFER_RE.search(folded_clean):
        if not _TASK_SIGNAL_RE.search(folded_clean):
            return ""
    if _TASK_SIGNAL_RE.search(folded_clean) or len(cleaned.split()) >= 4:
        return cleaned[:limit]
    return ""


def extract_marked_task(answer: Any, limit: int = 600) -> str:
    """Izvuci zadatak SAMO ako u odgovoru postoji eksplicitni "Zadatak:" red;
    uzima se POSLJEDNJI takav (novi zadatak dolazi na kraju ocjene)."""
    return _extract_marker_paragraph(answer, limit=limit)


# BUG 4 (2026-07-10): model kod višestavčne ocjene numeriše svaku stavku "1."
# (markdown navika). Deterministička renumeracija: SAMO kada su SVI numerisani
# redovi "1." (degenerisan slučaj — nikad legitiman), postaju 1., 2., 3. …
_NUMBERED_LINE_START_RE = re.compile(r"(?m)^([ \t]*)(\d{1,2})([.)])\s")


def fix_repeated_item_numbering(text: str) -> str:
    numbers = [int(m.group(2)) for m in _NUMBERED_LINE_START_RE.finditer(text or "")]
    if len(numbers) < 2 or any(n != 1 for n in numbers):
        return text
    counter = 0

    def _renum(m: re.Match) -> str:
        nonlocal counter
        counter += 1
        return f"{m.group(1)}{counter}{m.group(3)} "

    return _NUMBERED_LINE_START_RE.sub(_renum, text)


def list_topics(master: dict | None = None, grade: int | str = DEFAULT_GRADE) -> dict:
    """Lista NPP tema za UI dropdown (GET /api/ai-tutor/topics), oblast-first.

    Izvor je ``get_master`` (Excel je izvor istine; ništa nije hardkodirano).
    U NPP modelu ``topic`` = ``npp_topic_id``, ``display_name`` = ``tema_ui``.
    Redoslijed oblasti je iz ``master["areas"]`` (po ``area_order``), a teme unutar
    oblasti u redoslijedu NPP_TOPICS sheeta. Svaka tema nosi ``has_video`` da UI
    može prikazati "Objasni mi" oznaku za video, i ``oblast`` radi lakšeg filtera::

        {
          "grade": 6,
          "areas": [{"oblast","area_order","topic_count","topics_with_video"}, ...],
          "oblast_order": ["Skupovi i skupovne operacije", ...],
          "topics":  [{"oblast","topic","display_name","has_video"}, ...],
          "grouped": {"Skupovi i skupovne operacije": [ ... ], ...},
        }
    """
    g = normalize_grade(grade)
    master = master if master is not None else get_master(grade=g)
    rows = master.get("topics", [])
    videos = master.get("videos_by_topic", {})

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
            "has_video": bool(videos.get(r["topic"])),
        }
        for r in ready
    ]

    grouped: dict[str, list] = {}
    seen_order: list[str] = []
    for t in topics:
        if t["oblast"] not in grouped:
            seen_order.append(t["oblast"])
        grouped.setdefault(t["oblast"], []).append(t)

    # Oblast redoslijed iz areas (area_order); teme bez oblasti idu na kraj.
    areas = [a for a in master.get("areas", []) if a["oblast"] in grouped]
    oblast_order = [a["oblast"] for a in areas]
    for oblast in seen_order:  # fallback: bilo koja oblast koje nema u areas
        if oblast not in oblast_order:
            oblast_order.append(oblast)

    grades = {normalize_value(r.get("grade")) for r in ready if r.get("grade")}
    grade = int(next(iter(grades))) if len(grades) == 1 and next(iter(grades)).isdigit() else g

    return {
        "grade": grade,
        "areas": areas,
        "oblast_order": oblast_order,
        "topics": topics,
        "grouped": grouped,
    }


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
        "— retry sa max_tokens=%s + reasoning_effort=minimal", finish, mode, cap, retry_cap,
    )
    try:
        # KORAK 3 (2026-07-11): na retry-u spusti reasoning na "minimal" da se
        # oslobodi completion budžet (gpt-5-mini troši dio na razmišljanje pa je
        # prazan odgovor često znak da je reasoning pojeo max_completion_tokens).
        # Ako injektovani callable ne podržava kwarg, padni na poziv bez njega.
        try:
            retry_resp = openai_chat(
                model, messages, timeout=timeout, max_tokens=retry_cap,
                reasoning_effort="minimal",
            )
        except TypeError:
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


# --- N9 (2026-07-14): mikro-zadatak u Objašnjenju ("Probaj ti: …") ------------------
# Produkt-odluka (Faris): Objašnjenje SMIJE provjeriti razumijevanje mikro-zadatkom,
# ali NE postaje mod koji prati zadatke — zadatak živi u next_state.micro_task,
# nikad u last_tutor_task. Odgovor se deterministički provjeri, ali se saopštava
# TOPLO (vođeni korak), bez tvrde labele "Netačno." — isto kao post-hint tok.

_MICRO_TASK_RE = re.compile(
    r"(?mi)^[ \t>*-]*probaj\s+ti\s*[:\-—]\s*(.+?)\s*$"
)


def extract_micro_task(answer: Any, limit: int = 300) -> str:
    """Tekst mikro-zadatka iz odgovora tutora ("Probaj ti: koliko je 3/8 + 2/8?").

    Uzima POSLJEDNJI marker; prazan string kada ga nema. Marker je eksplicitan
    (prompt ga zahtijeva) pa se proza nikad ne pogađa."""
    raw = normalize_value(answer)
    last = None
    for m in _MICRO_TASK_RE.finditer(raw):
        last = m
    if last is None:
        return ""
    text = re.sub(r"\s+", " ", last.group(1)).strip()
    # mora nositi matematički signal (broj/operator) — inače nije zadatak
    if not text or not _TASK_SIGNAL_RE.search(fold_diacritics(text)):
        return ""
    return text[:limit]


_MICRO_LABEL_RE = re.compile(
    r"^\s*(?:netaca?n\w*|taca?no|toca?no|dj?el[io]micn\w*\s+taca?n\w*)\s*[.!:,–—-]*\s*"
)
_MICRO_OPENER = {
    "correct": "Tako je!",
    "partial": "Blizu si —",
    "incorrect": "Nije baš —",
}
# Tekst koji VEĆ počinje mekim sudom ("Nije baš tačno…", "Skoro!") ne treba još
# jedan uvod — inače ispadne "Nije baš — Nije baš tačno…".
_MICRO_HAS_OPENER_RE = re.compile(
    r"^\s*(nije\b|skoro\b|blizu\b|ma\s+nije\b|tako\s+je\b|bravo\b|"
    r"dobar\s+pokusaj\b|odlicno\b|super\b)"
)


def _soften_micro_task_answer(answer: Any, check: Any) -> str:
    """N9: Objašnjenje NIJE Vježba — nikad ocjenske labele.

    Presuda iz koda ostaje obavezujuća (guard uklanja kontradikciju), ali se
    tvrda labela ("Netačno.") zamjenjuje toplim uvodom. Model je povremeno ipak
    napiše uprkos zabrani u promptu, pa je ovo deterministički enforcement."""
    out = normalize_value(answer)
    if not out:
        return out
    if check is not None:
        out = enforce_grading_consistency(out, check)
    verdict = authoritative_verdict(check) if check is not None else "unknown"
    stripped = out
    for _ in range(3):                       # "Netačno. Tačno. …" → očisti sve
        folded = fold_diacritics(stripped)
        m = _MICRO_LABEL_RE.match(folded)
        if not m:
            break
        # i zaostalu interpunkciju/crticu ("Netačno — izgleda…" → "izgleda…"),
        # inače uvod ispadne "Nije baš — — izgleda…"
        stripped = stripped[m.end():].lstrip(" \t.!?:;,-–—")
    if stripped == out or not stripped:
        return out                           # nije bilo labele — ne diraj
    opener = _MICRO_OPENER.get(verdict)
    if not opener or _MICRO_HAS_OPENER_RE.match(fold_diacritics(stripped)):
        return stripped                      # već ima meki uvod — ne dupliraj
    return f"{opener} {stripped}"


def _apply_micro_task_contract(payload: dict) -> None:
    """Učenik odgovara na mikro-zadatak iz prethodnog OBJAŠNJENJA."""
    if payload.get("_direct_answer") is not None or payload.get("_skip_answer_check"):
        return
    if normalize_value(payload.get("intent")):
        return
    if normalize_value(payload.get("interaction_phase")).lower() == "answering_practice_task":
        return                              # pravi practice odgovor ima prednost
    micro = normalize_value(_previous_next_state(payload).get("micro_task"))
    if not micro:
        return
    message = normalize_value(payload.get("student_message") or payload.get("message"))
    if not message:
        return
    # Samo POKUŠAJ ODGOVORA preuzima tok; pitanje/novi zahtjev ide normalno.
    mode_parsed, _answers = parse_student_answers(message)
    if mode_parsed == "none":
        return
    if detect_new_task_request(message) or _PRACTICE_EXPLAIN_RE.search(fold_diacritics(message)):
        return
    result = check_practice_answer(micro, message)
    payload["_micro_task"] = micro
    payload["_micro_task_reply"] = True
    payload["_skip_answer_check"] = True    # nije grading potez → bez tvrde labele
    if result is not None and result.checkable:
        payload["_micro_task_check"] = result
        # izloži presudu i u response-u (telemetrija/testovi); _skip_answer_check
        # drži ovo IZVAN grading toka, pa guard ne postavlja ocjensku labelu.
        payload["answer_check"] = result


def _soften_post_hint_reply(payload: dict) -> None:
    """CLASS 1: kad je prethodni potez bio hint (pod-korak), učenikov odgovor
    može biti tačan MEĐUKORAK, a ne finalni odgovor.

    Ako je deterministička provjera dala PUN tačan finalni odgovor → ostavi je
    (učenik je riješio zadatak, slijedi "Tačno." + novi zadatak). Inače povuci
    presudu i pusti model da procijeni korak (uz `_post_hint_reply` direktivu):
    tačan međukorak dobija potvrdu i sljedeći korak, a NIKAD "Netačno." samo zato
    što nije finalni rezultat."""
    check = payload.get("answer_check")
    items = getattr(check, "items", None) if check is not None else None
    verdicts = [getattr(i, "verdict", None) for i in items] if items else []
    all_correct = bool(verdicts) and all(v == "correct" for v in verdicts)
    if all_correct:
        return                      # finalni tačan odgovor — deterministička Tačno ostaje
    # Nije (pouzdano) finalno tačno → tretiraj kao mogući međukorak.
    payload["answer_check"] = None
    payload["_skip_answer_check"] = True
    payload["_post_hint_reply"] = True


def _run_answer_check(payload: dict) -> None:
    """Deterministička provjera odgovora + atribucija stavke (BUG 12).

    Kod višestavkovnog zadatka stanje ``task_items`` (iz prethodnog next_state)
    zna koje su stavke VEĆ ocijenjene. Ako učenik pošalje JEDAN nenumerisan
    odgovor, a preostala je TAČNO JEDNA stavka, odgovor se pripisuje njoj —
    ranije je provjera vraćala checkable=False pa je model pogađao i brkao
    stavke."""
    task = normalize_value(payload.get("last_tutor_task"))
    student = normalize_value(payload.get("student_message") or payload.get("message"))
    if not (task and student):
        return

    items = split_numbered_items(task)
    prev_items = _previous_next_state(payload).get("task_items")
    if items and prev_items:
        labels = [n for n, _t in items]
        if set(prev_items.get("labels") or []) == set(labels):
            graded = [n for n in prev_items.get("graded") or [] if n in labels]
            pending = [n for n in labels if n not in graded]
            payload["_task_items_prev"] = {"labels": labels, "graded": graded}
            answer_mode, _answers = parse_student_answers(student)
            refs = detect_referenced_items(student, labels)
            if len(pending) == 1 and not refs and answer_mode == "single":
                n = pending[0]
                by_n = dict(items)
                result = check_practice_answer(by_n.get(n, ""), student)
                if result.checkable and result.items:
                    for item_check in result.items:
                        item_check.n = n
                    payload["answer_check"] = result
                    payload["_current_task_item"] = n
                    return

    payload["answer_check"] = check_practice_answer(task, student)


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


def _apply_image_practice_followup(payload: dict) -> None:
    """AUD-01 + N3: odgovor na stavku iz image_test "practice" toka.

    Pripiše odgovor PRAVOJ stavci (poslana/current → prva pending → zadnja
    riješena za ispravke; ako je odgovor tačan za neku kasniju kandidatkinju,
    prebaci ocjenu na nju), postavi ``_image_answered_label`` (state) i
    ``_image_practice_answer`` (prompt: SLJEDEĆA stavka sa slike, ne izmišljaj)
    te ``_image_next_task_text`` (persist za last_tutor_task)."""
    if payload.get("_skip_answer_check"):
        return
    if normalize_value(payload.get("interaction_phase")).lower() != "answering_practice_task":
        return
    prev_img = _previous_next_state(payload).get("image_test") or {}
    if prev_img.get("style") != "practice":
        return
    saved = normalize_value(payload.get("last_image_context"))
    ocr = ocr_from_saved_context(saved) if saved else ""
    items = extract_image_tasks(ocr) if ocr else []
    labels = [str(it["label"]).lower() for it in items]
    tasks_by_label = {str(it["label"]).lower(): normalize_value(it["task"]) for it in items}
    if not labels:
        return
    solved = [s for s in (prev_img.get("solved") or []) if s in labels]

    # koja stavka je odgovorena? poslani task (browser = izvor istine) → current
    sent = normalize_value(payload.get("last_tutor_task"))
    sent_label = None
    if sent:
        for lbl, text in tasks_by_label.items():
            if text and (text[:600] == sent or sent.startswith(text[:80]) or text.startswith(sent[:80])):
                sent_label = lbl
                break
    prev_current = normalize_value(prev_img.get("current")).lower()
    if prev_current not in labels:
        prev_current = None
    pending = [l for l in labels if l not in solved]
    student = normalize_value(payload.get("student_message") or payload.get("message"))

    def _chk(lbl):
        text = tasks_by_label.get(lbl or "", "")
        return check_practice_answer(text, student) if text else None

    # kandidati po prioritetu; tačan pogodak bilo gdje pobjeđuje (N3: ispravka
    # poslije reveala ili odgovor na najavljenu sljedeću stavku bez "da")
    cands: list[str] = []
    for lbl in (sent_label or prev_current,
                pending[0] if pending else None,
                solved[-1] if solved else None):
        if lbl and lbl not in cands:
            cands.append(lbl)
    answered, result = None, None
    for lbl in cands:
        r = _chk(lbl)
        if r and r.checkable and r.items and r.items[0].verdict in (
                "correct", "correct_value_wrong_form"):
            answered, result = lbl, r
            break
    if answered is None and cands:
        answered = cands[0]
        result = _chk(answered)
    if answered:
        if result is not None and result.checkable:
            payload["answer_check"] = result
        payload["_image_answered_label"] = answered
    new_solved = solved + ([answered] if answered and answered not in solved else [])
    rem = [l for l in labels if l not in new_solved]
    if rem:
        nxt = rem[0]
        payload["_image_practice_answer"] = {
            "next_label": nxt,
            "next_task": tasks_by_label.get(nxt, "")[:500],
            "done": False,
        }
        payload["_image_next_task_text"] = tasks_by_label.get(nxt, "")[:600]
    else:
        payload["_image_practice_answer"] = {"next_label": None, "next_task": "", "done": True}
        payload["_image_next_task_text"] = ""


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
    # Session mod = ono što je korisnik izabrao u UI-ju; contracts smiju mijenjati
    # SAMO prompt-mod (interno rutiranje), a UI prikazuje session mod (BUG 10/14).
    payload["_session_mode"] = normalize_value(payload.get("mode")).lower() or "explain"
    # Eksplicitna namjera (stil/obim) se čita iz ORIGINALNE poruke, prije nego
    # što je confirmation contract eventualno zamijeni sintetičkom.
    _apply_explicit_intent(payload)
    _apply_confirmation_contract(payload)
    # "zadatak", "novi zadatak", "daj mi teži/lakši" → NOVI zadatak, ne ocjena
    # ni objašnjenje starog (BUG 6/8). Poslije potvrda, prije challenge/help.
    _apply_new_task_intent(payload)
    # Osporavanje ranije ocjene ("pa to sam i odgovorio") → ponovna provjera
    # prethodnog odgovora (ne novi zadatak). Poslije confirmation contract-a da
    # potvrde ("da"/"ne") imaju prednost.
    _apply_challenge_contract(payload)
    # "a treći zadatak?", "daj hint", "objasni treći" nisu predani odgovori.
    # Preusmjeri ih prije determinističkog ocjenjivanja.
    _apply_practice_help_contract(payload)
    # N1: "evo moj zadatak: 3/4 + 5/6" u Vježbi → TAJ zadatak postaje aktivni.
    _apply_student_task_contract(payload)
    # N8: "objasni mi X" u Vježbi bez answer-faze → explain potez (proza
    # objašnjenja ne smije postati last_tutor_task).
    _apply_explain_request_contract(payload)
    # N9: odgovor na mikro-zadatak iz prethodnog objašnjenja ("Probaj ti: …").
    _apply_micro_task_contract(payload)
    # N5: "jesi li robot / ko te napravio / špijuniraš li me" → topli direktni odgovor.
    _apply_meta_identity_contract(payload)

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

    def _plain_direct_prep(message: str) -> dict:
        """Determinističan direktan odgovor u BILO kojem modu (npr. CLASS 3
        razjašnjenje "s kojeg lista?"). Ne dira result-mod context policy."""
        payload["_direct_answer"] = message
        m = master if master is not None else get_master(grade=payload["grade"])
        prompt_result = _direct_prompt_result(payload)
        return {
            "payload": payload,
            "master": m,
            "lookup_result": {"status": "found", "source": "intent_contract"},
            "prompt_result": prompt_result,
            "mode": prompt_result["mode"],
            "status": prompt_result["status"],
            "effective_topic": "unknown",
            "topic_context": {},
            "messages": None,
            "use_model": model,
            "direct_answer": message,
        }

    def _result_direct_prep(message: str) -> dict:
        """Determinističan Result-mod odgovor (npr. "koji broj zadatka?")."""
        payload["_direct_answer"] = message
        m = master if master is not None else get_master(grade=payload["grade"])
        prompt_result = _direct_prompt_result(payload)
        prompt_result["context_policy"] = "disabled_for_result_mode"
        return {
            "payload": payload,
            "master": m,
            "lookup_result": {"status": "found", "source": "result_mode_disabled"},
            "prompt_result": prompt_result,
            "mode": prompt_result["mode"],
            "status": prompt_result["status"],
            "effective_topic": None,
            "topic_context": {},
            "messages": None,
            "use_model": model,
            "direct_answer": message,
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
    answering = normalize_value(payload.get("interaction_phase")).lower() == "answering_practice_task"
    # CLASS 1 (2026-07-12): prethodni potez je bio hint sa pod-korakom. Učenikov
    # odgovor sada može biti tačan MEĐUKORAK (npr. hint pita "koliko je 1/2 s
    # nazivnikom 6?", učenik: "3/6"). Bez ovoga bi se gradirao protiv FINALNOG
    # rezultata i dobio "Netačno" iako je korak tačan (live: 5/6 slučajeva).
    post_hint = (
        answering
        and not payload.get("_skip_answer_check")
        and bool(_previous_next_state(payload).get("just_hinted"))
    )
    if not payload.get("_skip_answer_check") and answering:
        _run_answer_check(payload)
    if post_hint:
        _soften_post_hint_reply(payload)
    # AUD-01: odgovor na stavku image_test "practice" toka → pripremi SLJEDEĆU
    # stavku sa slike (da followup ne izmisli novi zadatak).
    _apply_image_practice_followup(payload)

    # F5: ažuriraj "stuck" brojač (za preporuku videa u Vježbajmo) prije prompta.
    _update_stuck_state(payload)

    grade = payload["grade"]
    master = master if master is not None else get_master(grade=grade)
    tmap = tmap if tmap is not None else get_thinkific_map(grade=grade)
    _apply_exam_context_contract(payload, master)

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

    # --- CLASS 3: slika sa DVA lista (numeracija se ponavlja) --------------------
    # "prvi" tada postoji na oba lista. Ne pogađaj — pitaj s kojeg lista, inače se
    # zadaci miješaju (prijavljeni bug: test_mat.webp, "prvi" → zadatak s 2. lista,
    # a objašnjenje s 1.). Vrijedi u svim modovima, PRIJE image_test/result grane.
    dup_sheets_msg = _duplicate_sheets_clarification(payload)
    if dup_sheets_msg:
        return _plain_direct_prep(dup_sheets_msg)

    # --- image_test: deterministički korak kroz zadatke sa slike -----------------
    # Stanje se gradi iz OCR-a + prethodnog next_state (nikad iz proze); prompt
    # builder ga koristi kao mode blok. image_test tok (nastavi/„da"/sve zadatke)
    # ima prednost i radi u više modova — zato se rješava PRIJE result-mod grane.
    image_test = _resolve_image_test_state(payload)
    if image_test:
        payload["_image_test"] = image_test

    # --- Result/Quick mod: POTPUNO odvojen od razreda/teme/lekcije ---------------
    # Kontekst (opened_lesson_topic, selected_topic, thinkific mapa, video) se NE
    # koristi; izvor istine je tekst/slika. Topic lookup i detekcija se preskaču.
    # Primjenjuje se SAMO kad nema aktivnog image_test toka.
    if image_test is None and is_result_mode(payload):
        payload["_context_policy"] = "disabled_for_result_mode"
        selection = _resolve_result_selection(payload)
        if selection is not None:
            payload["_detected_task_count"] = selection["count"]
            if selection["action"] == "ask":
                # više zadataka, nije rečeno koji → deterministički pitaj broj
                return _result_direct_prep(selection["message"])
            if selection["action"] == "solve":
                payload["_result_solve_item"] = selection["item"]
        lookup_result = {
            "status": "unknown", "source": "result_mode_disabled",
            "final_topic": "unknown", "message": "", "matches": [],
        }
        prompt_result = build_result_mode_prompt(payload)
    else:
        lookup_result = get_final_topic(payload, master, tmap)

        # --- Phase 7: kontrolni za CIJELU OBLAST (selected_oblast bez teme) -----
        exam_oblast_prompt = None
        if lookup_result["status"] == "unknown":
            exam_oblast_prompt = build_exam_oblast_prompt(payload, master)

        # --- Phase 6: free_chat detekcija teme (heuristike → LLM klasifikator) --
        general_answer = False
        if (
            payload.get("_image_test")
            and exam_oblast_prompt is None
            and lookup_result["status"] == "unknown"
        ):
            # image_test tok rješava stavke sa slike (stanje, ne proza) — detekcija
            # teme nije potrebna i ne smije trošiti dodatni LLM poziv.
            general_answer = True
        elif exam_oblast_prompt is None and lookup_result["status"] == "unknown":
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
            elif combined and (has_image or not is_vague_message(combined, master)):
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

    if has_question and _SIMILAR_TASK_OFFER_RE.search(folded):
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

    # AUD-01: image_test "practice" — učenik SAM rješava stavke sa slike, pa se
    # na potezu ODGOVORA (kad _image_test nije aktivan jer answering_practice_task
    # vraća None) tekuća stavka označava riješenom i nudi se nastavak na sljedeću.
    prev_img = _previous_next_state(payload).get("image_test") or {}
    answering = normalize_value(payload.get("interaction_phase")).lower() == "answering_practice_task"
    img = payload.get("_image_test")
    if (not img and answering and prev_img.get("style") == "practice"
            and not payload.get("_skip_answer_check")):
        labels = [normalize_value(l).lower() for l in prev_img.get("item_labels") or []]
        solved = [s for s in (prev_img.get("solved") or []) if s in labels]
        # N3: stavku određuje helper (poslani task / ispravka / eager-next);
        # bez pogađanja rem[0] — ispravka NE smije "pojesti" sljedeću stavku.
        cur = normalize_value(payload.get("_image_answered_label")).lower()
        if not cur or cur not in labels:
            cur = normalize_value(prev_img.get("current")).lower()
        if cur and cur in labels and cur not in solved:
            solved.append(cur)
        unsolved = [l for l in labels if l not in solved]
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
                    "item_labels": labels, "solved": solved,
                    "next_item": _item_out(nxt), "style": "practice",
                },
            }
        # sve stavke sa slike riješene → izlaz iz image toka (normalna logika ispod)

    # image_test ima APSOLUTNU prednost i računa se iz stanja, ne iz proze:
    # stavka koju smo u OVOM potezu dali modelu postaje "riješena", sljedeća
    # neriješena ide u pending_action.next_item.
    if img:
        if img.get("style") == "practice":
            # PREZENTACIONI potez: tekuća stavka se NE označava riješenom —
            # učenik tek treba poslati svoj odgovor (grade-a se sljedeći potez).
            labels = list(img.get("labels") or [])
            solved = list(img.get("solved") or [])
            cur = img.get("current")
            remaining = [l for l in labels if l not in solved and l != cur]
            return {
                "expected_user_action": "answer_task",
                "pending_action": _empty_pending_action(),
                "active_task_kind": "image_test",
                "image_test": {
                    "item_labels": labels, "solved": solved, "current": cur,
                    "next_item": _item_out(remaining[0]) if remaining else None,
                    "style": "practice",
                },
            }
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


# "unverified" = učenik JESTE odgovorio stavku, ali je kod nije mogao provjeriti
# (model ju je ocijenio sam). Za praćenje stanja bitno je "odgovoreno", pa i ona
# izlazi iz pending skupa — sljedeći kratki odgovor pripada preostaloj stavci.
_ANSWERED_VERDICTS = ("correct", "correct_value_wrong_form", "incorrect", "unverified")


def _task_items_for_response(payload: dict, task_text: str) -> dict | None:
    """BUG 12: novo/ažurirano ``task_items`` stanje za response.

    Novi višestavkovni zadatak → svježe stanje (ništa ocijenjeno). Grading potez
    bez novog zadatka → prethodno stanje + stavke odgovorene u OVOM potezu."""
    if task_text:
        items = split_numbered_items(task_text)
        if len(items) >= 2:
            return {"labels": [n for n, _t in items], "graded": []}
        return None
    prev = payload.get("_task_items_prev") or _previous_next_state(payload).get("task_items")
    if not prev:
        return None
    labels = list(prev.get("labels") or [])
    graded = [n for n in prev.get("graded") or [] if n in labels]
    check = payload.get("answer_check")
    for item_check in getattr(check, "items", []) or []:
        n = getattr(item_check, "n", None)
        if (
            n in labels
            and n not in graded
            and getattr(item_check, "verdict", "") in _ANSWERED_VERDICTS
        ):
            graded.append(n)
    return {"labels": labels, "graded": sorted(graded)}


def _pending_items_after_grading(payload: dict) -> list:
    """AUD-04 (B2): stavke višestavkovnog zadatka koje POSLIJE ovog grading
    poteza i dalje čekaju odgovor. Prazna lista = ništa ne čeka."""
    state = _task_items_for_response(payload, "")
    if not state:
        return []
    labels = state.get("labels") or []
    graded = set(state.get("graded") or [])
    return [n for n in labels if n not in graded]


def _is_grading_turn(payload: dict) -> bool:
    """Da li ovaj potez ocjenjuje učenikov odgovor na zadatak?

    Samo tada se primjenjuje autoritativno pomirenje ocjene. Potvrde
    (``_skip_answer_check``) i direktni odgovori nisu ocjenjivanje, kao ni
    objašnjenja/nastavci sa slike."""
    if payload.get("_direct_answer") is not None or payload.get("_skip_answer_check"):
        return False
    if payload.get("answer_check") is not None:
        return True
    return normalize_value(payload.get("interaction_phase")).lower() == "answering_practice_task"


def _finalize_response(prep: dict, answer: str) -> dict:
    """Sastavi response dict + activity log (zajedničko za oba puta)."""
    payload = prep["payload"]
    prompt_result = prep["prompt_result"]
    mode, status = prep["mode"], prep["status"]

    # Audit: jezička zaštita — vrlo česti ekavski oblici ("deo", "rešenje") →
    # ijekavica. Streaming klijent na kraju ponovo renderuje answer iz "done"
    # događaja, pa ispravka važi i za streamane odgovore.
    answer = to_ijekavica(answer)

    # BUG 4: višestavčna ocjena/kontrolni sa svim stavkama "1." → renumeriši
    # deterministički (radi samo u degenerisanom slučaju kada su SVE "1.").
    if _is_grading_turn(payload) or mode == "exam":
        answer = fix_repeated_item_numbering(answer)

    # Autoritativno pomirenje ocjene: JEDAN sud (deterministički answer_check)
    # → konzistentan odgovor. Uklanja lažno negativne ocjene za provjereno
    # tačan odgovor i neutrališe samo-kontradikciju ("Nije tačno … tačan").
    # Radi SAMO na potezu ocjenjivanja učenikovog odgovora i PRIJE nego što se
    # doda korekcijski uvod sa slike ("Ranije sam pogrešno napisao …") — taj
    # uvod je priznanje ranije greške tutora, ne ocjena učenika, i ne smije se
    # tumačiti kao kontradikcija.
    if _is_grading_turn(payload):
        answer = enforce_grading_consistency(answer, payload.get("answer_check"))

    # N9: odgovor na mikro-zadatak iz Objašnjenja — presuda iz koda je obavezujuća
    # (bez kontradikcije), ali se saopštava TOPLO, bez ocjenske labele.
    if payload.get("_micro_task_reply"):
        answer = _soften_micro_task_answer(answer, payload.get("_micro_task_check"))

    correction_preface = correction_preface_from_context(
        payload.get("last_image_context", "")
    )
    if correction_preface and "Ranije sam pogrešno napisao" not in answer:
        answer = correction_preface + "\n\n" + answer
    image_verification = None
    # Kad se u Result modu rješava SAMO jedna stavka sa slike (npr. 2. zadatak),
    # odgovor je JEDAN rezultat, a ne numerisana lista za cijelu sliku —
    # verifikator koji poravnava sve OCR stavke bi ga prepisao u pogrešnu listu.
    # Zato se tada NE pokreće. Za sliku s jednim zadatkom verifikacija ostaje.
    if (
        status == "ready"
        and normalize_value(payload.get("image_ocr_text"))
        and not payload.get("_result_solve_item")
    ):
        answer, image_verification = verify_image_result_answer(
            payload.get("image_ocr_text"), answer
        )
        if image_verification:
            payload["image_result_verification"] = image_verification

    # BUG5 sigurnosni prolaz: provjera slike mijenja SAMO numeričke redove
    # rezultata (ne dodaje riječi ocjene), pa ne može uvesti novu kontradikciju
    # ocjene. Ali za svaki slučaj, na ocjenjivačkom potezu BEZ korekcijskog uvoda
    # (koji je legitimno samo-priznanje i ne smije se dirati), ako je ipak ostala
    # kontradikcija — pomiri je još jednom. Idempotentno.
    if (
        _is_grading_turn(payload)
        and not correction_preface
        and has_grade_contradiction(answer)
    ):
        answer = enforce_grading_consistency(answer, payload.get("answer_check"))

    entry_source_used = normalize_value(payload.get("entry_source")) or normalize_value(
        prep["lookup_result"].get("source")
    )
    parent_report_signal = (
        "needs_work"
        if (mode in ("practice", "exam") or status == "fallback")
        else "neutral"
    )

    result_mode = payload.get("_context_policy") == "disabled_for_result_mode"
    response = {
        "answer": answer,
        # Result/Quick mod je kontekst-slobodan: tema/lekcija se NE koriste (null).
        "final_topic": None if result_mode else prompt_result.get("final_topic", "unknown"),
        "opened_lesson_topic": None if result_mode else prompt_result.get("opened_lesson_topic", "unknown"),
        "effective_topic": None if result_mode else prep["effective_topic"],
        "entry_source_used": entry_source_used,
        "topic_conflict": bool(prompt_result.get("topic_conflict", False)),
        "recommended_mode": _RECOMMENDED_MODE.get(mode, "practice"),
        # video preporuka (NPP VIDEO_LINKS) dolazi iz prompt buildera: explain nudi
        # video ako postoji, practice samo kad je učenik zapeo. U result modu False.
        "recommend_video": (
            False if result_mode else bool(prompt_result.get("video_recommended"))
        ),
        "parent_report_signal": parent_report_signal,
        "status": status,
        "mode": mode,
        # BUG 10: mod SESIJE (UI izbor) — contracts smiju interno preusmjeriti
        # prompt-mod, ali UI labela/chipovi prate session mod.
        "session_mode": payload.get("_session_mode") or mode,
    }
    if result_mode:
        response["context_policy"] = "disabled_for_result_mode"
        response["debug"] = {
            "context_policy": "disabled_for_result_mode",
            "grade_source": "soft_metadata_ignored",
            "topic_source": "disabled",
            "ignored_opened_lesson_topic": (
                normalize_value(payload.get("selected_topic"))
                or normalize_value(payload.get("lesson_title"))
                or None
            ),
            "refusal_reason": None,
            "detected_task_count": payload.get("_detected_task_count"),
            "image_result_available": bool(payload.get("_image_result_available")),
        }
    image_context = _make_image_context(payload, answer)
    if image_context:
        response["image_context"] = image_context
    if image_verification:
        response["image_verification"] = image_verification
    if payload.get("_solution_revealed"):
        response["practice_task_state"] = "solution_revealed"
    # Tokom image_test toka odgovor NIKAD ne postaje last_tutor_task — aktivni
    # "zadatak" je stavka sa slike i živi u next_state.image_test, ne u prozi.
    _img_state = payload.get("_image_test") or {}
    if _img_state.get("style") == "practice" and normalize_value(_img_state.get("current_task")):
        # AUD-01: kod image_test "practice" stila TEKUĆA stavka sa slike JESTE
        # aktivni zadatak — postavi je kao last_tutor_task da se učenikov naredni
        # odgovor deterministički provjeri protiv OCR teksta (ne protiv izmišljenog).
        task_text = normalize_value(_img_state.get("current_task"))[:600]
    elif (
        payload.get("_student_task")
        and status == "ready"
        and mode in ("practice", "exam")
        and not payload.get("_image_test")
    ):
        # N1: učenikov vlastiti zadatak iz poruke JESTE aktivni zadatak.
        task_text = normalize_value(payload["_student_task"])[:600]
    elif payload.get("_image_practice_answer") is not None:
        # N3: odgovor na stavku sa slike — SLJEDEĆA ponuđena stavka postaje
        # aktivni zadatak (persist; prazno kad su sve riješene). Model ne smije
        # izmišljati zadatke usred image toka, pa se proza ne ekstrahuje.
        task_text = normalize_value(payload.get("_image_next_task_text"))[:600]
    elif (
        payload.get("_direct_answer") is not None
        or payload.get("_image_test")
        or payload.get("_solution_revealed")
        or payload.get("_post_hint_reply")
        or status != "ready"
        or mode not in ("practice", "exam")
    ):
        # BUG 3/9: samo Vježba/Kontrolni prate aktivni zadatak; explain/quick
        # nikad (proza objašnjenja ne smije postati last_tutor_task).
        # CLASS 1: post-hint vođeni potez ne mijenja zadatak — original persistira.
        task_text = ""
    elif _is_grading_turn(payload):
        # BUG 1: na ocjenjivačkom potezu SAMO eksplicitni "Zadatak:" marker —
        # riješeni izraz iz objašnjenja ne smije postati "novi zadatak".
        task_text = extract_marked_task(answer)
        # AUD-04 (B2): dok stavke višestavkovnog zadatka ČEKAJU odgovor, novi
        # zadatak je zabranjen — model ga povremeno ipak doda, pa bi zamijenio
        # aktivni multi-zadatak i pokvario task_items praćenje. Server gate.
        if task_text and _pending_items_after_grading(payload):
            task_text = ""
    else:
        task_text = extract_practice_task(answer, mode=mode)
    # Server je jedini izvor istine za aktivni zadatak: polje se šalje UVIJEK
    # (i prazno), da klijent ne izvodi vlastitu heuristiku nad prozom.
    response["last_tutor_task"] = task_text
    response["next_state"] = _next_state_for_response(
        payload, answer, mode=mode, status=status, task_text=task_text
    )
    # F5: prenesi "stuck" brojač naprijed da klijent vrati stanje sljedeći put.
    response["next_state"]["stuck_count"] = int(payload.get("_stuck_count", 0) or 0)
    response["next_state"]["correct_streak"] = int(payload.get("_correct_streak", 0) or 0)
    # CLASS 1: ako je OVAJ potez bio hint sa pod-korakom, obilježi ga da sljedeći
    # učenikov odgovor tretiramo kao mogući međukorak (ne finalni).
    response["next_state"]["just_hinted"] = bool(payload.get("_gave_hint_step"))
    # N9: mikro-zadatak iz OBJAŠNJENJA živi u vlastitom polju (ne last_tutor_task),
    # da Objašnjenje ostane mod koji ne prati zadatke. Persistira dok učenik ne
    # odgovori (ili dok objašnjenje ne ponudi novi).
    if status == "ready" and mode == "explain" and not payload.get("_image_test"):
        micro = extract_micro_task(answer)
        if not micro and payload.get("_micro_task_reply"):
            micro = ""                      # odgovorio je — mikro-zadatak potrošen
        response["next_state"]["micro_task"] = micro
    # BUG 12: stanje višestavkovnog zadatka (labels + graded) putuje naprijed.
    task_items = _task_items_for_response(payload, task_text)
    if task_items:
        response["next_state"]["task_items"] = task_items
        # #5 (2026-07-11): na višestavkovnom (exam) potezu bez NOVOG zadatka, a
        # kad preostaju NEocijenjene stavke, zadrži prethodni zadatak u
        # last_tutor_task da se naredni odgovor na jednu stavku može pripisati
        # (inače se briše pa turn+1 ocjenjuje sve iz historije). NE dira task_text
        # koji je već ušao u stanje (graded se ne resetuje).
        if not task_text and mode in ("practice", "exam"):
            labels = task_items.get("labels") or []
            pending = [n for n in labels if n not in (task_items.get("graded") or [])]
            persisted = normalize_value(payload.get("last_tutor_task"))
            if len(labels) >= 2 and pending and persisted:
                response["last_tutor_task"] = persisted[:600]

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

# Veličina komada za progresivan prikaz pomirenog (puferovanog) odgovora.
_STREAM_CHUNK_CHARS = 40


def _chunk_for_stream(text: str, size: int = _STREAM_CHUNK_CHARS) -> list[str]:
    """Podijeli pomireni tekst na komade za progresivan prikaz.

    Cijepa na granicama riječi (čuva bijele znakove), pa je zbir komada TAČNO
    jednak ulazu — klijent koji spaja delte dobije isti tekst kao "done"."""
    if not text:
        return []
    chunks: list[str] = []
    buf = ""
    for token in re.split(r"(\s+)", text):
        if buf and len(buf) + len(token) > size:
            chunks.append(buf)
            buf = token
        else:
            buf += token
    if buf:
        chunks.append(buf)
    return chunks


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

    # Ocjenjivački potez se PUFERUJE: autoritativni sud (answer_check) poznat je
    # PRIJE poziva modela, pa se sirovi tok (koji može početi lažnim "Nije
    # tačno") NE smije prikazati prije pomirenja. Delte se šalju tek nakon što
    # _finalize_response pomiri odgovor. Ne-ocjenjivački potezi teku uživo
    # (token po token) kao i ranije.
    grading_turn = _is_grading_turn(prep["payload"])
    assembled: list[str] = []
    try:
        for delta in openai_chat_stream(
            prep["use_model"], prep["messages"],
            timeout=timeout, max_tokens=_MAX_TOKENS.get(prep["mode"], 700),
        ):
            if delta:
                assembled.append(delta)
                if not grading_turn:
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
        if not grading_turn:
            yield {"event": "delta", "data": {"delta": answer}}

    done = _finalize_response(prep, answer)

    # Ocjenjivački potez: odgovor je sada pomiren (bez kontradikcije i lažno
    # negativnog uvoda) — emituj GA kao delte radi progresivnog prikaza. Zbir
    # delti je tačno ``done["answer"]``, isti tekst koji nosi i "done" događaj.
    if grading_turn:
        for chunk in _chunk_for_stream(done["answer"]):
            yield {"event": "delta", "data": {"delta": chunk}}

    yield {"event": "done", "data": done}
